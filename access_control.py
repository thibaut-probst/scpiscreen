"""Shared access code with per-client lockout, signed sessions and an audit log.

- The access code is compared on SHA-256 digests (constant time, no length leak).
- Each client gets MAX_FAILED_ATTEMPTS attempts before a BAN_DURATION_SECONDS ban.
  Attempts are registered *before* the code is compared, in one atomic step, so
  parallel sessions cannot exceed the limit. Counters expire after FAILURE_WINDOW_SECONDS.
- Optional global limiter (SCPISCREEN_GLOBAL_ATTEMPT_LIMIT, off by default): caps the number
  of login attempts, all clients together, per hour. It protects against distributed guessing
  when the client address is unavailable. Visitors holding a valid cookie are not affected.
- Clients are stored under a keyed HMAC tag, never as a clear IP address.
- Sessions are STATELESS: the cookie carries an expiry and a random nonce, signed with a key
  derived from the server secret and from the access code. Nothing about a session is stored on
  disk, so sessions survive restarts, redeployments and ephemeral file systems, as long as the
  secret stays the same. Changing the access code or the secret signs everybody out.
- The server secret comes from the SCPISCREEN_SERVER_SECRET environment variable (recommended,
  at least 32 characters). Without it, a random secret is created in the state directory: that
  only works on a host whose disk persists between restarts.
- Authentication events go to a time-rotated log file (0600). The access code is never logged.

Bans and counters live in one JSON file protected by a thread lock and an inter-process file lock.

Command line (same SCPISCREEN_STATE_DIR as the server):
    python -m access_control bans
    python -m access_control unban --ip 203.0.113.7
    python -m access_control unban --tag 0123456789abcdef
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import string
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

try:  # POSIX only; on other platforms the thread lock is the only protection.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

ACCESS_CODE_MIN_LENGTH = 16
MAX_FAILED_ATTEMPTS = 5
BAN_DURATION_SECONDS = 24 * 60 * 60
FAILURE_WINDOW_SECONDS = 24 * 60 * 60
SESSION_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_TOKEN_LENGTH = 128
# Hard cap so that a flood of distinct clients cannot grow the state file without limit.
MAX_FAILURE_ENTRIES = 10_000
DEFAULT_LOG_RETENTION_DAYS = 90
SECRET_BYTES = 32
SECRET_ENV = "SCPISCREEN_SERVER_SECRET"
SECRET_ENV_MIN_LENGTH = 32
GLOBAL_LIMIT_ENV = "SCPISCREEN_GLOBAL_ATTEMPT_LIMIT"
GLOBAL_WINDOW_SECONDS = 60 * 60
SESSION_TOKEN_VERSION = "v1"

_STATE_FILE_NAME = "access_state.json"
_AUDIT_FILE_NAME = "auth.log"
_SECRET_FILE_NAME = "server_secret"
_LOCK_FILE_NAME = "access.lock"
_TAG_LENGTH = 32  # hex characters stored as key; the audit log shows the first 16
_LOG_TAG_LENGTH = 16
_MAC_LENGTH = 43

_LOCK = threading.RLock()
_AUDIT_LOGGER = logging.getLogger("scpiscreen.auth")
_T = TypeVar("_T")

_SECRET: bytes | None = None
_CLIENT_KEY: bytes | None = None
_CODE_FINGERPRINT: str | None = None
_LOGS_PURGED = False
_CACHE: dict[str, Any] = {"path": None, "signature": None, "state": None}


class AccessStateError(RuntimeError):
    """The access state cannot be read safely; callers must fail closed."""


@dataclass(frozen=True)
class AttemptDecision:
    """Outcome of registering one login attempt.

    allowed:      False when the attempt is refused (the code must not be compared).
    remaining:    attempts left after this one; 0 means this is the last allowed attempt
                  (a wrong code now keeps the ban that was just set).
    banned_until: expiry timestamp of the ban or of the global block when one is active.
    scope:        "client" for a ban of this client, "global" for the global limiter.
    """

    allowed: bool
    remaining: int
    banned_until: float | None
    scope: str = "client"


# ---- Paths and low-level helpers ------------------------------------------------


def _state_directory() -> Path:
    configured = os.environ.get("SCPISCREEN_STATE_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent / ".state"


def _state_path() -> Path:
    return _state_directory() / _STATE_FILE_NAME


def _ensure_state_directory() -> Path:
    directory = _state_directory()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise AccessStateError("state directory cannot be created") from error
    with contextlib.suppress(OSError):  # mkdir does not tighten a pre-existing directory
        os.chmod(directory, 0o700)
    return directory


def _as_float(value: Any) -> float:
    """Tolerant float conversion: anything unusable (including nan/inf) becomes 0.0."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _is_tag(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _TAG_LENGTH
        and all(character in string.hexdigits for character in value)
    )


@contextlib.contextmanager
def _exclusive_lock() -> Iterator[None]:
    """Thread lock plus an inter-process file lock (POSIX), held for a read-modify-write."""
    with _LOCK:
        if fcntl is None:
            yield
            return
        directory = _ensure_state_directory()
        try:
            descriptor = os.open(directory / _LOCK_FILE_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise AccessStateError("lock file cannot be opened") from error
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as error:
                raise AccessStateError("lock file cannot be locked") from error
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


# ---- Server secret, client tags and code fingerprint ----------------------------


def _load_or_create_secret() -> bytes:
    """Server secret used for client tags, the access-code fingerprint and session signatures.

    SCPISCREEN_SERVER_SECRET wins when set: it survives restarts on any host. Otherwise a random
    secret is stored in the state directory (needs a disk that persists).
    """
    global _SECRET
    if _SECRET is not None:
        return _SECRET

    configured = os.environ.get(SECRET_ENV, "").strip()
    if configured:
        if len(configured) < SECRET_ENV_MIN_LENGTH:
            raise AccessStateError(f"{SECRET_ENV} must contain at least {SECRET_ENV_MIN_LENGTH} characters")
        _SECRET = hashlib.sha256(configured.encode("utf-8")).digest()
        return _SECRET

    directory = _ensure_state_directory()
    path = directory / _SECRET_FILE_NAME
    with _exclusive_lock():
        try:
            data: bytes | None = path.read_bytes()
        except FileNotFoundError:
            data = None
        except OSError as error:
            raise AccessStateError("server secret cannot be read") from error
        if data is None:
            data = secrets.token_bytes(SECRET_BYTES)
            descriptor, temp_name = tempfile.mkstemp(dir=directory, prefix=".secret_", suffix=".tmp")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name)
                raise
        elif len(data) != SECRET_BYTES:
            raise AccessStateError("server secret file is invalid; delete it to sign everyone out")
    _SECRET = data
    return data


def secret_is_persistent() -> bool:
    """True when the secret comes from the environment (stable across restarts and redeployments)."""
    return bool(os.environ.get(SECRET_ENV, "").strip())


def _ensure_client_key() -> bytes:
    global _CLIENT_KEY
    if _CLIENT_KEY is None:
        secret = _load_or_create_secret()
        _CLIENT_KEY = hmac.new(secret, b"scpiscreen-client-tag", hashlib.sha256).digest()
    return _CLIENT_KEY


def client_tag(client: str | None) -> str:
    """Keyed HMAC tag of a client address (32 hex characters)."""
    if client is None:
        return "none"
    key = _ensure_client_key()
    return hmac.new(key, client.encode("utf-8"), hashlib.sha256).hexdigest()[:_TAG_LENGTH]


def configure(access_code: str) -> None:
    """Prepare secrets, the access-code fingerprint and the audit log. Safe to call on every run."""
    global _CODE_FINGERPRINT
    secret = _load_or_create_secret()
    code_key = hmac.new(secret, b"scpiscreen-code-fingerprint", hashlib.sha256).digest()
    _CODE_FINGERPRINT = hmac.new(code_key, access_code.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    _ensure_client_key()
    _configure_audit_log()


def _require_fingerprint() -> str:
    if _CODE_FINGERPRINT is None:
        raise AccessStateError("access control is not configured")
    return _CODE_FINGERPRINT


# ---- Audit log -------------------------------------------------------------------


class _PrivateTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Daily rotation; every log file is created readable by the server user only (0600)."""

    def _open(self):  # type: ignore[override]
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)


def _log_retention_days() -> int:
    raw = os.environ.get("SCPISCREEN_LOG_RETENTION_DAYS", "").strip()
    try:
        days = int(raw) if raw else DEFAULT_LOG_RETENTION_DAYS
    except ValueError:
        days = DEFAULT_LOG_RETENTION_DAYS
    return max(1, min(days, 3650))


def _purge_old_logs(directory: Path, days: int) -> None:
    """Delete rotated logs older than the retention period (rotation alone only runs when logging)."""
    cutoff = time.time() - days * 24 * 60 * 60
    for path in directory.glob(f"{_AUDIT_FILE_NAME}.*"):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()


def _configure_audit_log() -> None:
    global _LOGS_PURGED
    if _AUDIT_LOGGER.handlers:
        return
    days = _log_retention_days()
    try:
        directory = _ensure_state_directory()
        if not _LOGS_PURGED:
            _purge_old_logs(directory, days)
            _LOGS_PURGED = True
        handler = _PrivateTimedRotatingFileHandler(
            directory / _AUDIT_FILE_NAME,
            when="midnight",
            interval=1,
            backupCount=days,
            encoding="utf-8",
            utc=True,
        )
        for path in directory.glob(f"{_AUDIT_FILE_NAME}*"):
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
    except (OSError, AccessStateError):
        logging.getLogger("scpiscreen").exception("Audit log file cannot be opened")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _AUDIT_LOGGER.addHandler(handler)
    _AUDIT_LOGGER.setLevel(logging.INFO)
    _AUDIT_LOGGER.propagate = False


def log_event(event: str, client: str | None, **fields: object) -> None:
    """Write one authentication event. Never pass the access code here."""
    try:
        tag = client_tag(client)[:_LOG_TAG_LENGTH]
    except AccessStateError:
        tag = "none"
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    _AUDIT_LOGGER.info("event=%s client=%s %s", event, tag, details)


# ---- State file -------------------------------------------------------------------


def _empty_state() -> dict[str, Any]:
    return {"failures": {}, "global": {}}


def _load(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError) as error:
        raise AccessStateError("access state file is unreadable") from error
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("failures", {}), dict)
        or not isinstance(state.get("global", {}), dict)
    ):
        raise AccessStateError("access state file has an invalid structure")
    state.setdefault("failures", {})
    state.setdefault("global", {})
    state.pop("sessions", None)  # sessions used to be stored here; they are now signed cookies
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".access_", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _signature(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AccessStateError("access state file is unreadable") from error
    return (info.st_mtime_ns, info.st_size, info.st_ino)


def _is_banned(entry: Any, now: float) -> bool:
    return isinstance(entry, dict) and _as_float(entry.get("banned_until")) > now


def _prune(state: dict[str, Any], now: float) -> None:
    """Drop expired counters and enforce the size cap."""
    kept: dict[str, Any] = {}
    for tag, entry in state["failures"].items():
        # Keys that are not tags are legacy clear-text addresses: they are dropped here.
        if not _is_tag(tag) or not isinstance(entry, dict):
            continue
        last_attempt = _as_float(entry.get("last_attempt"))
        if _is_banned(entry, now) or now - last_attempt < FAILURE_WINDOW_SECONDS:
            kept[tag] = entry
    if len(kept) > MAX_FAILURE_ENTRIES:
        # Keep active bans first, then the most recent counters.
        ranked = sorted(
            kept.items(),
            key=lambda item: (_is_banned(item[1], now), _as_float(item[1].get("last_attempt"))),
            reverse=True,
        )
        kept = dict(ranked[:MAX_FAILURE_ENTRIES])
    state["failures"] = kept


def _read(reader: Callable[[dict[str, Any], float], _T]) -> _T:
    """Read from a cache that is reloaded only when the state file changed on disk."""
    with _LOCK:
        path = _state_path()
        signature = _signature(path)
        if (
            _CACHE["state"] is None
            or _CACHE["path"] != path
            or _CACHE["signature"] != signature
        ):
            _CACHE.update(path=path, signature=signature, state=_load(path))
        return reader(_CACHE["state"], time.time())


def _update(mutator: Callable[[dict[str, Any], float], _T]) -> _T:
    """Atomic read-modify-write across threads and processes."""
    with _exclusive_lock():
        path = _state_path()
        state = _load(path)  # always fresh from disk: another process may have written
        now = time.time()
        _prune(state, now)
        result = mutator(state, now)
        _prune(state, now)  # the mutation may have added an entry: re-apply the size cap
        _save(path, state)
        _CACHE.update(path=path, signature=_signature(path), state=state)
        return result


# ---- Public API: login attempts ---------------------------------------------------


def access_code_matches(submitted: str, expected: str) -> bool:
    """Constant-time comparison; both values are hashed first so lengths do not leak."""
    return hmac.compare_digest(
        hashlib.sha256(submitted.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _global_attempt_limit() -> int:
    """Maximum login attempts per hour, all clients together (0 = limiter disabled)."""
    raw = os.environ.get(GLOBAL_LIMIT_ENV, "").strip()
    try:
        limit = int(raw) if raw else 0
    except ValueError:
        limit = 0
    return max(0, min(limit, 100_000))


def ban_until(client: str) -> float | None:
    """Return the ban expiry timestamp if the client is currently banned, otherwise None."""
    tag = client_tag(client)

    def reader(state: dict[str, Any], now: float) -> float | None:
        entry = state["failures"].get(tag)
        if _is_banned(entry, now):
            return _as_float(entry.get("banned_until"))
        return None

    return _read(reader)


def register_attempt(client: str) -> AttemptDecision:
    """Atomically count one login attempt *before* the code is compared.

    The attempt that reaches MAX_FAILED_ATTEMPTS sets the ban immediately but is still
    evaluated; a correct code then lifts it through clear_failures(). Any further attempt
    is refused, so concurrent sessions cannot test more than MAX_FAILED_ATTEMPTS codes.
    When the global limiter is enabled, an attempt over the hourly budget is refused first.
    """
    tag = client_tag(client)
    limit = _global_attempt_limit()

    def mutate(state: dict[str, Any], now: float) -> AttemptDecision:
        entry = state["failures"].get(tag)
        if not isinstance(entry, dict):
            entry = {}
        banned_until = _as_float(entry.get("banned_until"))
        if banned_until > now:
            return AttemptDecision(False, 0, banned_until)

        if limit:
            window = state["global"]
            started = _as_float(window.get("started"))
            if now - started >= GLOBAL_WINDOW_SECONDS:
                window = {"started": now, "count": 0}
                state["global"] = window
                started = now
            count_so_far = int(_as_float(window.get("count")))
            if count_so_far >= limit:
                return AttemptDecision(False, 0, started + GLOBAL_WINDOW_SECONDS, "global")
            window["count"] = count_so_far + 1

        recent = now - _as_float(entry.get("last_attempt")) < FAILURE_WINDOW_SECONDS
        count = int(_as_float(entry.get("count"))) + 1 if recent else 1
        if count >= MAX_FAILED_ATTEMPTS:
            until = now + BAN_DURATION_SECONDS
            state["failures"][tag] = {"count": 0, "last_attempt": now, "banned_until": until}
            return AttemptDecision(True, 0, until)
        state["failures"][tag] = {"count": count, "last_attempt": now, "banned_until": 0}
        return AttemptDecision(True, MAX_FAILED_ATTEMPTS - count, None)

    return _update(mutate)


def clear_failures(client: str) -> int:
    """Reset a client (successful login or manual unban). Return the number of entries removed."""
    tag = client_tag(client)

    def mutate(state: dict[str, Any], now: float) -> int:
        return 1 if state["failures"].pop(tag, None) is not None else 0

    return _update(mutate)


def clear_failures_by_tag(prefix: str) -> int:
    """Remove every entry whose tag starts with the given hex prefix (as shown in the audit log)."""
    prefix = prefix.strip().lower()
    if len(prefix) < 8 or any(character not in string.hexdigits for character in prefix):
        raise ValueError("the tag prefix must contain at least 8 hexadecimal characters")

    def mutate(state: dict[str, Any], now: float) -> int:
        matching = [tag for tag in state["failures"] if tag.startswith(prefix)]
        for tag in matching:
            del state["failures"][tag]
        return len(matching)

    return _update(mutate)


def active_bans() -> list[tuple[str, float]]:
    """Active bans as (tag, expiry timestamp), soonest expiry first."""

    def reader(state: dict[str, Any], now: float) -> list[tuple[str, float]]:
        bans = [
            (tag, _as_float(entry.get("banned_until")))
            for tag, entry in state["failures"].items()
            if _is_banned(entry, now)
        ]
        return sorted(bans, key=lambda item: item[1])

    return _read(reader)


# ---- Public API: signed sessions ----------------------------------------------------


def _session_mac(payload: str) -> str:
    """Signature of a session payload; bound to the secret AND to the current access code."""
    key = hmac.new(
        _load_or_create_secret(),
        b"scpiscreen-session|" + _require_fingerprint().encode("ascii"),
        hashlib.sha256,
    ).digest()
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:_MAC_LENGTH]


def create_session() -> str:
    """Return a signed session token valid for SESSION_TTL_SECONDS. Nothing is stored."""
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{SESSION_TOKEN_VERSION}.{expires}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_session_mac(payload)}"


def is_valid_session(token: str | None) -> bool:
    """A token is valid while unexpired AND signed under the current secret and access code."""
    if not token:
        return False
    token = str(token)
    if len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        return False
    payload, _, mac = token.rpartition(".")
    parts = payload.split(".")
    if len(parts) != 3 or parts[0] != SESSION_TOKEN_VERSION:
        return False
    if not (parts[1].isascii() and parts[1].isdigit()):
        return False
    if int(parts[1]) <= time.time():
        return False
    return hmac.compare_digest(mac.encode("ascii"), _session_mac(payload).encode("ascii"))


# ---- Command line -----------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m access_control",
        description="Inspect or lift SCPIScreen bans (use the same SCPISCREEN_STATE_DIR as the server).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bans", help="list active bans (tags as shown in auth.log)")
    unban = commands.add_parser("unban", help="lift a ban")
    target = unban.add_mutually_exclusive_group(required=True)
    target.add_argument("--ip", help="client IP address")
    target.add_argument("--tag", help="tag prefix (at least 8 hex characters) from auth.log or 'bans'")
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "bans":
            bans = active_bans()
            if not bans:
                print("No active ban.")
            for tag, until in bans:
                expiry = datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{tag[:_LOG_TAG_LENGTH]}  banned until {expiry}")
            return 0
        removed = (
            clear_failures(arguments.ip)
            if arguments.ip
            else clear_failures_by_tag(arguments.tag)
        )
    except (AccessStateError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"{removed} entry removed." if removed == 1 else f"{removed} entries removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
