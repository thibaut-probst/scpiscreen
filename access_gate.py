"""Access gate for SCPIScreen: call require_access() first on every page.

    from access_gate import require_access

    require_access()   # returns only for a visitor who may see the page; otherwise st.stop()

Keeping the whole gate in one function means a future page (pages/ folder, st.navigation)
cannot forget part of it. The page styles (masthead classes) must be injected before the call.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import urlsplit

import streamlit as st
import streamlit.components.v1 as components

import access_control

logger = logging.getLogger("scpiscreen")

ACCESS_CODE_ENV = "SCPISCREEN_ACCESS_CODE"
INSECURE_HTTP_ENV = "SCPISCREEN_ALLOW_INSECURE_HTTP"
# The __Host- prefix makes browsers accept the cookie only if it is Secure, has Path=/ and no Domain.
AUTH_COOKIE_NAME = "__Host-scpiscreen_access"
AUTH_COOKIE_TTL_SECONDS = access_control.SESSION_TTL_SECONDS
ACCESS_CODE_MAX_INPUT = 256
BAN_DURATION_HOURS = access_control.BAN_DURATION_SECONDS // 3600
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SESSION_FLAG = "access_granted"
PENDING_COOKIE_KEY = "pending_session_cookie"


# ---- Helpers ----------------------------------------------------------------------


def current_client_key() -> str | None:
    """Client IP as seen by Streamlit, or None when it is not available."""
    address = getattr(st.context, "ip_address", None)
    if not address:
        return None
    text = str(address).strip()
    return text or None


def render_gate_header() -> None:
    st.markdown(
        "<header class='masthead'><div class='brand-line'><span class='brand-mark'>S</span> SCPIScreen</div>"
        "<div class='masthead-body'><div><div class='masthead-kicker'>Accès privé</div>"
        "<div class='masthead-title'>Classement des SCPI</div>"
        "<div class='masthead-lede'>Saisissez votre code d’accès pour continuer.</div></div></div></header>",
        unsafe_allow_html=True,
    )


def render_ban_notice(until: float) -> None:
    until_text = datetime.fromtimestamp(until).strftime("%d/%m/%Y à %H:%M")
    st.error(f"Accès temporairement bloqué depuis cette adresse jusqu’au {until_text}.")


def _connection_is_insecure() -> bool:
    """True when the browser reached the app over plain HTTP from a non-local host.

    The session cookie is Secure: over HTTP it would never be stored and the access code
    would travel in clear. Set SCPISCREEN_ALLOW_INSECURE_HTTP=1 to bypass on a trusted network.
    """
    if os.environ.get(INSECURE_HTTP_ENV, "").strip() == "1":
        return False
    try:
        url = str(getattr(st.context, "url", "") or "")
    except Exception:  # context information not available yet
        return False
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme == "http" and (parts.hostname or "") not in LOCAL_HOSTS


def _read_auth_cookie() -> str | None:
    """Session cookie sent by the browser with the connection, read on the server."""
    cookies = getattr(st.context, "cookies", None)
    if cookies is None:
        return None
    try:
        value = cookies.get(AUTH_COOKIE_NAME)
    except Exception:
        return None
    return str(value) if value else None


def _fallback_ban_until() -> float | None:
    """Per-browser-session ban, used only when the client address is unavailable."""
    until = st.session_state.get("fallback_banned_until")
    if isinstance(until, (int, float)) and until > time.time():
        return float(until)
    return None


def _register_fallback_attempt() -> access_control.AttemptDecision:
    """Same rules as access_control.register_attempt, counted in the browser session."""
    now = time.time()
    banned_until = _fallback_ban_until()
    if banned_until is not None:
        return access_control.AttemptDecision(False, 0, banned_until)
    count = int(st.session_state.get("fallback_attempts", 0)) + 1
    if count >= access_control.MAX_FAILED_ATTEMPTS:
        until = now + access_control.BAN_DURATION_SECONDS
        st.session_state["fallback_attempts"] = 0
        st.session_state["fallback_banned_until"] = until
        return access_control.AttemptDecision(True, 0, until)
    st.session_state["fallback_attempts"] = count
    return access_control.AttemptDecision(True, access_control.MAX_FAILED_ATTEMPTS - count, None)


def _clear_attempts(client_key: str | None) -> None:
    if client_key is not None:
        access_control.clear_failures(client_key)
    st.session_state.pop("fallback_attempts", None)
    st.session_state.pop("fallback_banned_until", None)


def _unavailable() -> None:
    render_gate_header()
    st.error("Le contrôle d’accès est temporairement indisponible. Contactez l’administrateur.")
    st.stop()


def _persist_cookie() -> None:
    """Write the session cookie from the page itself, with exact attributes.

    A tiny script runs in a same-origin frame and sets the cookie on the page (Path=/, Secure,
    SameSite=Strict, one year), which is what the __Host- prefix requires and what the server
    receives on the next visit. The frame is rendered again on every run of this session, so
    a rerun right after the login cannot remove it before it has executed.
    """
    token = st.session_state.get(PENDING_COOKIE_KEY)
    if not token:
        return
    cookie = (
        f"{AUTH_COOKIE_NAME}={token}; Max-Age={AUTH_COOKIE_TTL_SECONDS}; "
        "Path=/; Secure; SameSite=Strict"
    )
    script = (
        "<script>(function(){var c=" + json.dumps(cookie) + ";"
        "try{window.parent.document.cookie=c;}catch(e){document.cookie=c;}})();</script>"
    )
    with st.container(key="cookie_setter"):
        components.html(script, height=0)


# ---- Login form -------------------------------------------------------------------


def _render_login(access_code: str, client_key: str | None) -> None:
    if _connection_is_insecure():
        render_gate_header()
        st.error(
            "Connexion non chiffrée : ouvrez l’application en HTTPS. "
            "Le code d’accès ne doit pas transiter en clair."
        )
        st.stop()

    # Header and form live in a placeholder so that they disappear as soon as the login succeeds.
    gate_area = st.empty()
    with gate_area.container():
        render_gate_header()
        with st.form("access_gate"):
            submitted_code = st.text_input(
                "Code d’accès", type="password", max_chars=ACCESS_CODE_MAX_INPUT
            )
            submitted = st.form_submit_button(
                "Accéder au classement", type="primary", use_container_width=True
            )
    if not submitted:
        return

    try:
        # The attempt is counted first and atomically; only then is the code compared.
        if client_key is not None:
            decision = access_control.register_attempt(client_key)
        else:
            decision = _register_fallback_attempt()
        if not decision.allowed:
            access_control.log_event("ban_refused", client_key)
            render_ban_notice(decision.banned_until or time.time())
            return

        code_is_valid = len(submitted_code) <= ACCESS_CODE_MAX_INPUT and access_control.access_code_matches(
            submitted_code, access_code
        )
        if code_is_valid:
            session_token = access_control.create_session()
            _clear_attempts(client_key)
            st.session_state[SESSION_FLAG] = True
            gate_area.empty()
            access_control.log_event("login_success", client_key)
            st.session_state[PENDING_COOKIE_KEY] = session_token
            return

        access_control.log_event("login_failed", client_key, remaining=decision.remaining)
        if decision.remaining == 0:
            access_control.log_event("ban_created", client_key, hours=BAN_DURATION_HOURS)
            st.error(f"Trop de tentatives. Accès bloqué pendant {BAN_DURATION_HOURS} heures.")
        else:
            st.error(f"Code d’accès incorrect. Il vous reste {decision.remaining} tentative(s).")
    except access_control.AccessStateError:
        logger.exception("Access state unreadable during login")
        st.error("Le contrôle d’accès est temporairement indisponible. Contactez l’administrateur.")


# ---- Public entry point -----------------------------------------------------------


def require_access() -> None:
    """Return only when the visitor is allowed in; otherwise render the gate and stop the script."""
    access_code = os.environ.get(ACCESS_CODE_ENV, "").strip()
    if len(access_code) < access_control.ACCESS_CODE_MIN_LENGTH:
        st.error(
            f"Authentification non configurée : définissez {ACCESS_CODE_ENV} "
            f"({access_control.ACCESS_CODE_MIN_LENGTH} caractères minimum) dans l’environnement du serveur."
        )
        st.stop()

    client_key = current_client_key()  # None when Streamlit does not expose the address
    try:
        access_control.configure(access_code)
        banned_until = (
            access_control.ban_until(client_key) if client_key is not None else _fallback_ban_until()
        )
        if banned_until is None and not st.session_state.get(SESSION_FLAG, False):
            if access_control.is_valid_session(_read_auth_cookie()):
                st.session_state[SESSION_FLAG] = True
                access_control.log_event("session_resumed", client_key)
    except access_control.AccessStateError:
        logger.exception("Access state unreadable")
        _unavailable()
        return

    if banned_until is not None:
        access_control.log_event("ban_refused", client_key)
        render_gate_header()
        render_ban_notice(banned_until)
        st.stop()

    if not st.session_state.get(SESSION_FLAG, False):
        _render_login(access_code, client_key)
        if not st.session_state.get(SESSION_FLAG, False):
            st.stop()
    _persist_cookie()
