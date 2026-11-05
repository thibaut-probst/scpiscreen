"""Shared access code with per-client lockout, server-side sessions and an audit log.

- The access code is compared on SHA-256 digests (constant time, no length leak).
- Each client gets MAX_FAILED_ATTEMPTS attempts before a BAN_DURATION_SECONDS ban.
  Attempts are registered *before* the code is compared, in one atomic step, so
  parallel sessions cannot exceed the limit. Counters expire after FAILURE_WINDOW_SECONDS.
- Clients are stored under a keyed HMAC tag, never as a clear IP address. The key is a
  random server secret kept in the state directory (it is NOT derived from the access code).
- Session cookies carry random tokens. Only their SHA-256 digests are stored, together
  with a fingerprint of the access code: changing the code invalidates every session.
- Authentication events go to a time-rotated log file (0600). The access code is never logged.

All state lives in one JSON file protected by a thread lock and an inter-process file lock.

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
# Hard caps so that a flood of distinct clients cannot grow the state file without limit.
MAX_FAILURE_ENTRIES = 10_000
MAX_SESSIONS = 10_000
DEFAULT_LOG_RETENTION_DAYS = 90
SECRET_BYTES = 32

_STATE_FILE_NAME = "access_state.json"
_AUDIT_FILE_NAME = "auth.log"
_SECRET_FILE_NAME = "server_secret"
_LOCK_FILE_NAME = "access.lock"
_TAG_LENGTH = 32  # hex characters stored as key; the audit log shows the first 16
_LOG_TAG_LENGTH = 16

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

    allowed:      False when the client is already banned (the code must not be compared).
    remaining:    attempts left after this one; 0 means this is the last allowed attempt
                  (a wrong code now keeps the ban that was just set).
    banned_until: ban expiry timestamp when a ban is active, otherwise None.
    """

    allowed: bool
    remaining: int
    banned_until: float | None


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


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    """Random server secret used for client tags and the access-code fingerprint."""
    global _SECRET
    if _SECRET is not None:
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
    return {"failures": {}, "sessions": {}}


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
        or not isinstance(state.get("sessions", {}), dict)
    ):
        raise AccessStateError("access state file has an invalid structure")
    state.setdefault("failures", {})
    state.setdefault("sessions", {})
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


def _session_expiry(entry: Any) -> float:
    return _as_float(entry.get("expires")) if isinstance(entry, dict) else 0.0


def _is_banned(entry: Any, now: float) -> bool:
    return isinstance(entry, dict) and _as_float(entry.get("banned_until")) > now


def _prune(state: dict[str, Any], now: float) -> None:
    """Drop expired data and enforce the size caps."""
    fingerprint = _CODE_FINGERPRINT
    sessions = {
        digest: entry
        for digest, entry in state["sessions"].items()
        if _session_expiry(entry) > now
        and (fingerprint is None or entry.get("fp") == fingerprint)
    }
    if len(sessions) > MAX_SESSIONS:
        newest = sorted(sessions.items(), key=lambda item: _session_expiry(item[1]), reverse=True)
        sessions = dict(newest[:MAX_SESSIONS])
    state["sessions"] = sessions

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
        _prune(state, now)  # the mutation may have added an entry: re-apply the size caps
        _save(path, state)
        _CACHE.update(path=path, signature=_signature(path), state=state)
        return result


# ---- Public API -------------------------------------------------------------------


def access_code_matches(submitted: str, expected: str) -> bool:
    """Constant-time comparison; both values are hashed first so lengths do not leak."""
    return hmac.compare_digest(
        hashlib.sha256(submitted.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


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
    """
    tag = client_tag(client)

    def mutate(state: dict[str, Any], now: float) -> AttemptDecision:
        entry = state["failures"].get(tag)
        if not isinstance(entry, dict):
            entry = {}
        banned_until = _as_float(entry.get("banned_until"))
        if banned_until > now:
            return AttemptDecision(False, 0, banned_until)
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


def create_session() -> str:
    """Create a session and return the raw token (only its digest is stored)."""
    fingerprint = _require_fingerprint()
    token = secrets.token_urlsafe(32)
    digest = _digest(token)

    def mutate(state: dict[str, Any], now: float) -> None:
        state["sessions"][digest] = {"expires": now + SESSION_TTL_SECONDS, "fp": fingerprint}

    _update(mutate)
    return token


def is_valid_session(token: str | None) -> bool:
    """A session is valid while unexpired AND created under the current access code."""
    if not token:
        return False
    token = str(token)
    if len(token) > MAX_TOKEN_LENGTH:
        return False
    digest = _digest(token)
    fingerprint = _require_fingerprint()

    def reader(state: dict[str, Any], now: float) -> bool:
        entry = state["sessions"].get(digest)
        if not isinstance(entry, dict):
            return False  # unknown, or a legacy entry created before code fingerprints existed
        return _session_expiry(entry) > now and hmac.compare_digest(
            str(entry.get("fp", "")), fingerprint
        )

    return _read(reader)


def revoke_session(token: str | None) -> None:
    """Invalidate one session token immediately (for a future logout button)."""
    if not token:
        return
    digest = _digest(str(token))

    def mutate(state: dict[str, Any], now: float) -> None:
        state["sessions"].pop(digest, None)

    _update(mutate)


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
