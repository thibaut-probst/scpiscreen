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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

import access_control
from html_frame import render_html_frame

logger = logging.getLogger("scpiscreen")

ACCESS_CODE_ENV = "SCPISCREEN_ACCESS_CODE"
INSECURE_HTTP_ENV = "SCPISCREEN_ALLOW_INSECURE_HTTP"
# The __Host- prefix makes browsers accept the cookie only if it is Secure, has Path=/ and no Domain.
AUTH_COOKIE_NAME = "__Host-scpiscreen_access"
# Over plain HTTP (a local run on http://localhost) Firefox and Safari refuse Secure and __Host-
# cookies, so a plain cookie is used there. Both names are read; HTTPS deployments never use it.
PLAIN_COOKIE_NAME = "scpiscreen_access"
AUTH_COOKIE_NAMES = (AUTH_COOKIE_NAME, PLAIN_COOKIE_NAME)
AUTH_COOKIE_TTL_SECONDS = access_control.SESSION_TTL_SECONDS
# Lax (not Strict): a Strict cookie is not sent when the app is opened from another site
# (a link in a message, an embedding page), which looked like "the cookie does not work".
AUTH_COOKIE_SAMESITE = "Lax"
ACCESS_CODE_MAX_INPUT = 256
BAN_DURATION_HOURS = access_control.BAN_DURATION_SECONDS // 3600
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DISPLAY_TIMEZONE = "Europe/Paris"
SESSION_FLAG = "access_granted"
PENDING_COOKIE_KEY = "pending_session_cookie"
COOKIE_LOGGED_KEY = "cookie_state_logged"
# Temporary diagnostics: set SCPISCREEN_DIAGNOSTICS=1 on the server, then open the site with ?diag=1.
DIAGNOSTICS_ENV = "SCPISCREEN_DIAGNOSTICS"
# Some hosts (Streamlit Community Cloud) do not pass JavaScript-written cookies to the server on
# the WebSocket connection, so st.context.cookies stays empty there. The browser still has the
# cookie: a probe frame reads it and reloads the page once with the token in this query parameter.
HANDOFF_PARAM = "sat"
HANDOFF_TRIED_KEY = "handoff_tried"


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


def _format_local_time(timestamp: float) -> str:
    """Date and time in the visitors' time zone (the server usually runs in UTC)."""
    try:
        moment = datetime.fromtimestamp(timestamp, ZoneInfo(DISPLAY_TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        moment = datetime.fromtimestamp(timestamp)
    return moment.strftime("%d/%m/%Y à %H:%M")


def render_ban_notice(until: float, scope: str = "client") -> None:
    until_text = _format_local_time(until)
    if scope == "global":
        st.error(
            "Trop de tentatives de connexion ont été enregistrées sur l’application. "
            f"Réessayez après {until_text}."
        )
        return
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
    """Session cookie sent by the browser with the connection, read on the server.

    Both cookie names are looked at; a valid token wins over a stale one (for example a cookie
    written by an older version of the app that the browser could not overwrite).
    """
    cookies = getattr(st.context, "cookies", None)
    if cookies is None:
        return None
    candidates: list[str] = []
    for name in AUTH_COOKIE_NAMES:
        try:
            value = cookies.get(name)
        except Exception:
            continue
        if value:
            candidates.append(str(value))
    for value in candidates:
        if access_control.session_status(value) == "valid":
            return value
    return candidates[0] if candidates else None


def _page_is_https() -> bool:
    """False only when the server clearly sees a plain-HTTP page (the default is HTTPS)."""
    try:
        url = str(getattr(st.context, "url", "") or "")
    except Exception:
        return True
    return urlsplit(url).scheme != "http" if url else True


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
    SameSite=Lax, one year), which is what the __Host- prefix requires and what the server
    receives on the next visit. The frame is rendered again on every run of this session, so
    a rerun right after the login cannot remove it before it has executed.
    """
    token = st.session_state.get(PENDING_COOKIE_KEY)
    if not token:
        return
    if _page_is_https():
        cookie = (
            f"{AUTH_COOKIE_NAME}={token}; Max-Age={AUTH_COOKIE_TTL_SECONDS}; "
            f"Path=/; Secure; SameSite={AUTH_COOKIE_SAMESITE}"
        )
    else:
        cookie = (
            f"{PLAIN_COOKIE_NAME}={token}; Max-Age={AUTH_COOKIE_TTL_SECONDS}; "
            f"Path=/; SameSite={AUTH_COOKIE_SAMESITE}"
        )
    script = (
        "<script>(function(){var c=" + json.dumps(cookie) + ";"
        "try{window.parent.document.cookie=c;}catch(e){"
        "try{document.cookie=c;}catch(e2){}}})();</script>"
    )
    with st.container(key="cookie_setter"):
        render_html_frame(script, height=0, scrolling=False)


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
                "Accéder au classement", type="primary", width="stretch"
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
            access_control.log_event("ban_refused", client_key, scope=decision.scope)
            render_ban_notice(decision.banned_until or time.time(), decision.scope)
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


def _received_cookie_names() -> list[str]:
    """Names (never values) of the cookies the browser sent with the connection."""
    cookies = getattr(st.context, "cookies", None)
    if cookies is None:
        return []
    try:
        return sorted(str(name) for name in cookies.keys())
    except Exception:
        return []


def _resume_session_from_cookie(client_key: str | None) -> None:
    """Grant access from a valid cookie, and log (once per session) why a visitor was not resumed."""
    status = access_control.session_status(_read_auth_cookie())
    if status == "valid":
        st.session_state[SESSION_FLAG] = True
        access_control.log_event("session_resumed", client_key)
        return
    if not st.session_state.get(COOKIE_LOGGED_KEY):
        st.session_state[COOKIE_LOGGED_KEY] = True
        # cookie_absent: the browser did not send it (never written, expired, blocked, not sent);
        #   received= lists the cookies that did arrive.
        # cookie_rejected: it was sent but refused: reason=bad_signature (the secret or the access
        #   code changed), expired or malformed.
        access_control.log_event(
            "cookie_absent" if status == "absent" else "cookie_rejected",
            client_key,
            reason=status,
            received=",".join(_received_cookie_names()) or "none",
        )


def _try_url_handoff(client_key: str | None) -> None:
    """Accept a session token handed over in the URL by the probe frame (see HANDOFF_PARAM).

    The parameter is removed from the URL at once, whatever the outcome. A valid token also
    gets a fresh cookie (new one-year expiry), so regular visitors never expire.
    """
    token = st.query_params.get(HANDOFF_PARAM)
    if token is None:
        return
    try:
        del st.query_params[HANDOFF_PARAM]
    except KeyError:
        pass
    st.session_state[HANDOFF_TRIED_KEY] = True  # the probe never runs twice in one session
    status = access_control.session_status(str(token))
    if status == "valid":
        st.session_state[SESSION_FLAG] = True
        st.session_state[PENDING_COOKIE_KEY] = access_control.create_session()
        access_control.log_event("session_resumed", client_key, via="url")
    else:
        access_control.log_event("handoff_rejected", client_key, reason=status)


def _handoff_probe_script(cookie_names: tuple[str, ...] = AUTH_COOKIE_NAMES) -> str:
    """Runs in a frame of the login page: if the browser holds a session cookie, reload with it.

    Only tokens of the current format are considered, so a stale cookie never triggers a reload.
    """
    prefix = access_control.SESSION_TOKEN_VERSION + "."
    return (
        "<script>(function(){"
        "var names=" + json.dumps(list(cookie_names)) + ",prefix=" + json.dumps(prefix) + ";"
        "try{"
        "var doc=window.parent.document,loc=window.parent.location,url=new URL(loc.href);"
        "if(url.searchParams.has(" + json.dumps(HANDOFF_PARAM) + ")){return;}"
        "var token=null;"
        "doc.cookie.split('; ').forEach(function(pair){"
        "names.forEach(function(name){"
        "if(pair.indexOf(name+'=')===0){var value=pair.substring(name.length+1);"
        "if(value.indexOf(prefix)===0){token=value;}}"
        "});});"
        "if(!token){return;}"
        # Hide the login form while the page reloads, so returning visitors do not see it flash.
        "var style=doc.createElement('style');"
        "style.textContent='[data-testid=\"stMain\"]{visibility:hidden}';"
        "doc.head.appendChild(style);"
        "url.searchParams.set(" + json.dumps(HANDOFF_PARAM) + ",token);"
        "loc.replace(url.toString());"
        "}catch(e){/* the login form stays usable */}"
        "})();</script>"
    )


def _render_handoff_probe() -> None:
    if st.session_state.get(HANDOFF_TRIED_KEY) or HANDOFF_PARAM in st.query_params:
        return
    with st.container(key="handoff_probe"):
        render_html_frame(_handoff_probe_script(), height=0, scrolling=False)


def _diagnostics_requested() -> bool:
    return os.environ.get(DIAGNOSTICS_ENV, "").strip() == "1" and st.query_params.get("diag") == "1"


def _diagnostics_script() -> str:
    """Report written inside a frame: what the BROWSER sees and allows (cookie names only)."""
    return """<body style="margin:0;font:13px/1.5 ui-monospace,Menlo,monospace;color:#152a40"><pre id="out" style="margin:0;white-space:pre-wrap"></pre><script>(function () {
var out = [];
function add(label, value) { out.push(label + " : " + value); }
var cookieNames = %s, doc = null;
try { doc = window.parent.document; add("accès au document de la page", "oui"); }
catch (e) { add("accès au document de la page", "NON (" + e.name + ")"); }
try { add("protocole de la page", window.parent.location.protocol); } catch (e) { /* ignore */ }
add("cookies activés (navigateur)", String(navigator.cookieEnabled));
add("contexte sécurisé", String(window.isSecureContext));
try { add("page affichée dans un cadre", String(window.parent !== window.parent.top)); }
catch (e) { add("page affichée dans un cadre", "oui, d'une autre origine (" + e.name + ")"); }
if (doc) {
    var names = doc.cookie ? doc.cookie.split("; ").map(function (c) { return c.split("=")[0]; }) : [];
    add("cookies visibles par le navigateur", names.length ? names.join(", ") : "aucun");
    add("cookie de session présent", String(cookieNames.some(function (n) { return names.indexOf(n) !== -1; })));
    try {
        doc.cookie = "__Host-scpiscreen_test=1; Max-Age=60; Path=/; Secure; SameSite=Lax";
        add("écriture d'un cookie de test", String(doc.cookie.indexOf("__Host-scpiscreen_test=") !== -1));
    } catch (e) { add("écriture d'un cookie de test", "NON (" + e.name + ")"); }
}
document.getElementById("out").textContent = out.join("\\n");
})();</script></body>""" % json.dumps(list(AUTH_COOKIE_NAMES))


def _render_diagnostics(client_key: str | None) -> None:
    """What the server and the browser each see about the session cookie. Off unless enabled."""
    try:
        url = str(getattr(st.context, "url", "") or "")
    except Exception:
        url = ""
    lines = [
        f"cookies reçus par le serveur   : {', '.join(_received_cookie_names()) or 'aucun'}",
        f"cookie de session (serveur)    : {access_control.session_status(_read_auth_cookie())}",
        f"adresse client exposée         : {'oui' if client_key else 'non'}",
        f"secret serveur stable (env)    : {'oui' if access_control.secret_is_persistent() else 'non'}",
        f"schéma de l'URL vue du serveur : {urlsplit(url).scheme or 'inconnu'}",
    ]
    st.warning("Diagnostic temporaire (SCPISCREEN_DIAGNOSTICS=1) : à désactiver après usage.")
    st.code("\n".join(lines), language=None)
    render_html_frame(_diagnostics_script(), height=210, scrolling=True)


def require_access() -> None:
    """Return only when the visitor is allowed in; otherwise render the gate and stop the script."""
    access_code = os.environ.get(ACCESS_CODE_ENV, "").strip()
    if len(access_code) < access_control.ACCESS_CODE_MIN_LENGTH:
        # The variable name stays in the server log: visitors only get a generic message.
        logger.error(
            "%s is missing or shorter than %d characters",
            ACCESS_CODE_ENV,
            access_control.ACCESS_CODE_MIN_LENGTH,
        )
        st.error("Le contrôle d’accès n’est pas configuré. Contactez l’administrateur.")
        st.stop()

    client_key = current_client_key()  # None when Streamlit does not expose the address
    try:
        access_control.configure(access_code)
        if _diagnostics_requested():
            _render_diagnostics(client_key)
        banned_until = (
            access_control.ban_until(client_key) if client_key is not None else _fallback_ban_until()
        )
        if banned_until is None and not st.session_state.get(SESSION_FLAG, False):
            _resume_session_from_cookie(client_key)
            if not st.session_state.get(SESSION_FLAG, False):
                _try_url_handoff(client_key)
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
        _render_handoff_probe()
        _render_login(access_code, client_key)
        if not st.session_state.get(SESSION_FLAG, False):
            st.stop()
    _persist_cookie()
