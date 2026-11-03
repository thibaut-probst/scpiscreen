"""Shared access code with per-client lockout, server-side sessions and an audit log.

- The access code is compared on SHA-256 digests (constant time, no length leak).
- Each client key (IP address) gets MAX_FAILED_ATTEMPTS failures before a
  BAN_DURATION_SECONDS ban. Failures expire after FAILURE_WINDOW_SECONDS.
- Session cookies carry random tokens. Only their SHA-256 digests are stored.
- Authentication events go to a rotating log file. Client addresses are
  written as keyed HMAC tags, never in clear, and the access code is never logged.

All state lives in one JSON file. Run one server process per state file: the
lock below protects threads, not several processes.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, TypeVar

ACCESS_CODE_MIN_LENGTH = 16
MAX_FAILED_ATTEMPTS = 5
BAN_DURATION_SECONDS = 24 * 60 * 60
FAILURE_WINDOW_SECONDS = 24 * 60 * 60
SESSION_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_TOKEN_LENGTH = 128

_STATE_FILE_NAME = "access_state.json"
_AUDIT_FILE_NAME = "auth.log"
_LOCK = threading.Lock()
_AUDIT_LOGGER = logging.getLogger("scpiscreen.auth")
_AUDIT_KEY: bytes | None = None
_T = TypeVar("_T")


class AccessStateError(RuntimeError):
    """The access state cannot be read safely; callers must fail closed."""


def _state_directory() -> Path:
    configured = os.environ.get("SCPISCREEN_STATE_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent / ".state"


def _state_path() -> Path:
    return _state_directory() / _STATE_FILE_NAME


# ---- Audit log -------------------------------------------------------------

def configure_audit_log(secret: bytes) -> None:
    """Derive the client-tag key from the server secret and attach the file handler once."""
    global _AUDIT_KEY
    _AUDIT_KEY = hmac.new(secret, b"scpiscreen-audit-client", hashlib.sha256).digest()
    if _AUDIT_LOGGER.handlers:
        return
    try:
        directory = _state_directory()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / _AUDIT_FILE_NAME, maxBytes=1_000_000, backupCount=6, encoding="utf-8"
        )
    except OSError:
        logging.getLogger("scpiscreen").exception("Audit log file cannot be opened")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _AUDIT_LOGGER.addHandler(handler)
    _AUDIT_LOGGER.setLevel(logging.INFO)
    _AUDIT_LOGGER.propagate = False


def _client_tag(client: str | None) -> str:
    if client is None or _AUDIT_KEY is None:
        return "none"
    return hmac.new(_AUDIT_KEY, client.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def log_event(event: str, client: str | None, **fields: object) -> None:
    """Write one authentication event. Never pass the access code here."""
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    _AUDIT_LOGGER.info("event=%s client=%s %s", event, _client_tag(client), details)


# ---- State file ------------------------------------------------------------

def _load(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return {"failures": {}, "sessions": {}}
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


def _prune(state: dict[str, Any], now: float) -> None:
    state["sessions"] = {
        digest: expires
        for digest, expires in state["sessions"].items()
        if isinstance(expires, (int, float)) and expires > now
    }
    kept: dict[str, Any] = {}
    for client, entry in state["failures"].items():
        if not isinstance(entry, dict):
            continue
        banned_until = float(entry.get("banned_until", 0) or 0)
        last_failure = float(entry.get("last_failure", 0) or 0)
        if banned_until > now or now - last_failure < FAILURE_WINDOW_SECONDS:
            kept[client] = entry
    state["failures"] = kept


def _read(reader: Callable[[dict[str, Any], float], _T]) -> _T:
    with _LOCK:
        return reader(_load(_state_path()), time.time())


def _update(mutator: Callable[[dict[str, Any], float], _T]) -> _T:
    with _LOCK:
        path = _state_path()
        state = _load(path)
        now = time.time()
        _prune(state, now)
        result = mutator(state, now)
        _save(path, state)
        return result


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---- Public API ------------------------------------------------------------

def access_code_matches(submitted: str, expected: str) -> bool:
    """Constant-time comparison; both values are hashed first so lengths do not leak."""
    return hmac.compare_digest(
        hashlib.sha256(submitted.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def ban_until(client: str) -> float | None:
    """Return the ban expiry timestamp if the client is currently banned, otherwise None."""

    def reader(state: dict[str, Any], now: float) -> float | None:
        entry = state["failures"].get(client)
        if isinstance(entry, dict):
            until = float(entry.get("banned_until", 0) or 0)
            if until > now:
                return until
        return None

    return _read(reader)


def record_failure(client: str) -> int:
    """Count a failed attempt. Return the remaining attempts; 0 means the client is now banned."""

    def mutate(state: dict[str, Any], now: float) -> int:
        entry = state["failures"].get(client)
        if not isinstance(entry, dict):
            entry = {}
        if float(entry.get("banned_until", 0) or 0) > now:
            return 0
        recent = now - float(entry.get("last_failure", 0) or 0) < FAILURE_WINDOW_SECONDS
        count = int(entry.get("count", 0) or 0) + 1 if recent else 1
        if count >= MAX_FAILED_ATTEMPTS:
            state["failures"][client] = {
                "count": 0,
                "last_failure": now,
                "banned_until": now + BAN_DURATION_SECONDS,
            }
            return 0
        state["failures"][client] = {"count": count, "last_failure": now, "banned_until": 0}
        return MAX_FAILED_ATTEMPTS - count

    return _update(mutate)


def clear_failures(client: str) -> None:
    """Reset the failure counter after a successful login."""

    def mutate(state: dict[str, Any], now: float) -> None:
        state["failures"].pop(client, None)

    _update(mutate)


def create_session() -> str:
    """Create a session and return the raw token (only its digest is stored)."""
    token = secrets.token_urlsafe(32)
    digest = _digest(token)

    def mutate(state: dict[str, Any], now: float) -> None:
        state["sessions"][digest] = now + SESSION_TTL_SECONDS

    _update(mutate)
    return token


def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    token = str(token)
    if len(token) > MAX_TOKEN_LENGTH:
        return False
    digest = _digest(token)

    def reader(state: dict[str, Any], now: float) -> bool:
        expires = state["sessions"].get(digest)
        return isinstance(expires, (int, float)) and expires > now

    return _read(reader)


def revoke_session(token: str | None) -> None:
    """Invalidate one session token immediately (for a future logout button)."""
    if not token:
        return
    digest = _digest(str(token))

    def mutate(state: dict[str, Any], now: float) -> None:
        state["sessions"].pop(digest, None)

    _update(mutate)