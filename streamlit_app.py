import html
import hashlib
import hmac
import os
import time
from urllib.parse import quote, urlencode
from typing import Any

import extra_streamlit_components as stx
import streamlit as st

# 
try:
    from st_keyup import st_keyup
    HAS_KEYUP = True
except ImportError:
    HAS_KEYUP = False

from scpi_data import (
    CATEGORY_ORDER,
    PROFILE_ORDER,
    SHEET_ID,
    WORKSHEETS,
    SheetLoadError,
    build_rankings,
    load_google_sheet,
)


st.set_page_config(
    page_title="SCPIScreen",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap');
    
    :root {
        color-scheme: light;
        --ink: #152a40;
        --muted: #657a8d;
        --blue: #087e74;
        --blue-deep: #102b46;
        --blue-pale: #e2f4ef;
        --ice: #bcecdf;
        --coral: #bd604d;
        --gold: #a87822;
        --line: #dce7e8;
        --paper: #f4f8f7;
        --surface: #fff;
        --surface-soft: #f8fbfa;
        --shadow-card: 0 18px 48px rgba(20, 48, 67, .075), 0 2px 8px rgba(20, 48, 67, .035);
    }
    html[data-theme="dark"] {
        color-scheme: dark;
        --ink: #e8edf2;
        --muted: #a7b7c6;
        --blue: #72d5bf;
        --blue-deep: #142d40;
        --blue-pale: #1c403f;
        --ice: #bcecdf;
        --gold: #e4bd68;
        --line: #304a57;
        --paper: #101b24;
        --surface: #182832;
        --surface-soft: #1d303a;
        --shadow-card: 0 18px 48px rgba(0, 0, 0, .2);
    }
    
    .stApp {
        background: radial-gradient(ellipse at 5% 0%, rgba(8, 126, 116, .055), transparent 35rem), var(--paper);
        color: var(--ink);
    }
    .stApp::before { content: ''; position: fixed; z-index: 1000; top: 0; left: 0; width: 100%; height: 3px; background: linear-gradient(90deg, #087e74, #83dbc1 60%, #c2f1df); }
    
    [data-testid="stMainBlockContainer"] { max-width: 1440px; padding: clamp(1.4rem, 4vw, 3.5rem) clamp(1rem, 4.8vw, 4.5rem) 4rem; }
    [data-testid="stHeader"] { background: color-mix(in srgb, var(--paper) 88%, transparent); }
    
    h1, h2, h3 { color: var(--ink); }
    h1 { max-width: 850px; margin: .55rem 0 .45rem !important; font-family: 'Fraunces', Georgia, serif !important; font-size: 3rem !important; font-weight: 500 !important; line-height: 1.08 !important; letter-spacing: -.045em !important; }
    h2, h3 { font-family: 'DM Sans', sans-serif !important; letter-spacing: -.025em !important; font-weight: 650 !important; }
    p, label, div, span { font-family: 'DM Sans', sans-serif; }
    
    /* Masthead */
    .masthead { position: relative; overflow: hidden; margin: .2rem 0 1.7rem; padding: clamp(1.5rem, 3vw, 2.5rem); border: 1px solid rgba(176, 220, 216, .18); border-radius: 22px; color: #f5f9fd; background: radial-gradient(ellipse at 88% 0%, rgba(77, 190, 165, .2), transparent 25rem), linear-gradient(125deg, #102b46 0%, #153c55 100%); box-shadow: 0 24px 60px rgba(16, 43, 70, .16); }
    .masthead::after { content: ''; position: absolute; right: -3rem; top: -8rem; width: 27rem; height: 27rem; border: 1px solid rgba(188, 236, 223, .15); border-radius: 50%; box-shadow: 0 0 0 3rem rgba(188, 236, 223, .045), 0 0 0 6rem rgba(188, 236, 223, .025); pointer-events: none; }
    .brand-line { position: relative; z-index: 1; display: flex; align-items: center; gap: .75rem; color: #d3e3f0; font-size: .67rem; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
    .brand-mark { display: inline-flex; align-items: center; justify-content: center; width: 2.15rem; height: 2.15rem; border: 1px solid rgba(188, 236, 223, .46); border-radius: 9px; background: rgba(188, 236, 223, .1); color: #c9f5e5; font-family: 'Fraunces', Georgia, serif; font-size: .85rem; letter-spacing: 0; }
    .brand-divider { width: 1px; height: 1rem; background: rgba(220,237,249,.27); }
    .masthead-body { position: relative; z-index: 1; display: flex; align-items: flex-end; justify-content: space-between; gap: 1.5rem; margin-top: clamp(1.5rem, 3vw, 2.6rem); }
    .masthead-kicker { margin-bottom: .5rem; color: var(--ice); font-size: .68rem; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
    .masthead-title { max-width: 780px; color: #fff; font-family: 'Fraunces', Georgia, serif; font-size: clamp(2rem, 4vw, 3.15rem); letter-spacing: -.04em; font-weight: 500; line-height: 1.12; }
    .masthead-lede { max-width: 42rem; margin-top: .6rem; color: #c4d4e2; font-size: .98rem; line-height: 1.5; }
    .masthead-tools { display: flex; align-items: center; gap: .55rem; flex: 0 0 auto; }
    
    .refresh-action { display: inline-flex; align-items: center; gap: .35rem; padding: .66rem .95rem; border: 1px solid #a9ebd4; border-radius: 10px; color: #103e3c !important; background: linear-gradient(135deg, #b9f0dc, #8edfc7); font-size: .82rem; font-weight: 700; text-decoration: none !important; white-space: nowrap; box-shadow: 0 5px 16px rgba(1, 15, 26, .16); transition: transform .18s ease, box-shadow .18s ease, background .18s ease; }
    .refresh-action:hover, .refresh-action:focus, .refresh-action:visited { border-color: #d2f8e9; color: #103e3c !important; background: #d2f8e9; box-shadow: 0 8px 20px rgba(1, 15, 26, .22); transform: translateY(-1px); text-decoration: none !important; }
    .source-badge { flex: 0 0 auto; padding: .58rem .78rem; border: 1px solid rgba(210,231,246,.22); border-radius: 999px; color: #deebf5; background: rgba(255,255,255,.06); font-size: .62rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    
    /* Controls */
    .profile-label { margin: .15rem 0 .55rem; color: var(--muted); font-size: .65rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    [data-testid="stSegmentedControl"] { margin-bottom: .5rem; }
    [data-testid="stSegmentedControl"] > div { padding: .28rem; border: 1px solid var(--line); border-radius: 13px; background: var(--surface); box-shadow: 0 5px 18px rgba(20, 48, 67, .045); }
    [data-testid="stSegmentedControl"] button { min-height: 2.7rem; border-radius: 9px !important; font-size: .82rem !important; font-weight: 600 !important; transition: color .16s ease, background .16s ease, box-shadow .16s ease; }
    [data-testid="stSegmentedControl"] button[aria-pressed="true"] { border-color: #b8e6d8 !important; color: #075d56 !important; background: #e3f5ee !important; box-shadow: 0 2px 7px rgba(8, 126, 116, .1) !important; }
    [data-testid="stSegmentedControl"] button:focus-visible, [data-testid="stToggle"] [role="switch"]:focus-visible { outline: 3px solid rgba(22,119,255,.35) !important; outline-offset: 2px; }
    [data-testid="stToggle"] [role="switch"][aria-checked="true"] { background: #087e74 !important; border-color: #087e74 !important; }
    
    .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: 1.8rem 0 .9rem; }
    .section-kicker { color: var(--blue); font-size: .66rem; font-weight: 700; letter-spacing: .15em; text-transform: uppercase; }
    .section-title { margin-top: .3rem; color: var(--ink); font-family: 'DM Sans', sans-serif; font-size: 1.35rem; font-weight: 700; letter-spacing: -.035em; }
    
    /* Table Desktop */
    .ranking-scroll { width: 100%; overflow-x: auto; overflow-y: auto; border: 1px solid var(--line); border-radius: 17px; background: var(--surface); box-shadow: var(--shadow-card); }
    .ranking-table { width: 100%; min-width: 1250px; border-collapse: separate; border-spacing: 0; }
    .ranking-table th { position: sticky; top: 0; z-index: 1; color: #dce9f5; background: #15364f; text-align: center; text-transform: uppercase; font-size: .64rem; font-weight: 700; letter-spacing: .12em; padding: 1rem 1.1rem; border-bottom: 1px solid #315a80; border-color: rgba(188, 236, 223, .16); white-space: nowrap; }
    .ranking-table th:first-child { padding-left: 1.2rem; border-top-left-radius: 16px; }
    .ranking-table th:last-child { border-top-right-radius: 16px; }
    .ranking-table td { padding: 1rem 1.1rem; border-bottom: 1px solid var(--line); font-size: .84rem; line-height: 1.5; vertical-align: middle; color: var(--ink); }
    .ranking-table tr:last-child td { border-bottom: 0; }
    .ranking-table tbody tr { transition: background-color .16s ease; }
    .ranking-table tbody tr:hover { background: #f0f8f5; }
    
    /* Table Utils */
    .rank-number { color: #8a9aa8; font-size: .78rem !important; font-weight: 700; font-variant-numeric: tabular-nums; }
    .rank-first { color: #087e74; }
    .company-link { color: var(--ink); font-weight: 700; text-decoration: none; text-underline-offset: 4px; }
    .company-link:hover { color: var(--blue); text-decoration: underline; }
    .data-chip { display: inline-block; padding: .34rem .58rem; border: 1px solid #e6eeeb; border-radius: 999px; background: #f4f8f7; color: #435b68; font-size: .75rem; line-height: 1.45; }
    .discount-value { color: var(--ink); font-size: .83rem; font-weight: 600; font-variant-numeric: tabular-nums; }
    .score-pill { display: inline-flex; align-items: center; justify-content: center; padding: .2rem .45rem; font-size: .75rem; font-weight: 700; font-variant-numeric: tabular-nums; border-radius: 6px; line-height: 1; }
    
    /* Categories */
    .category-green-vivid { color: #075c4a; background: #aee9ce; }
    .category-green-pale { color: #176747; background: #e0f4e8; }
    .category-yellow { color: #735500; background: #fff1bf; }
    .category-orange { color: #78410f; background: #f9d6ae; }
    .category-red { color: #fff; background: #c84d51; }
    .category-unavailable { color: var(--muted); background: #e9eef3; }
    
    /* Single SCPI Card */
    .scpi-card { margin: .9rem 0 1rem; padding: clamp(1.2rem, 3vw, 2rem); border: 1px solid var(--line); border-radius: 20px; background: var(--surface); box-shadow: var(--shadow-card); }
    .card-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; padding-bottom: 1.25rem; border-bottom: 1px solid var(--line); }
    .card-name { font-family: 'Fraunces', Georgia, serif; font-size: clamp(1.5rem, 3vw, 2rem); line-height: 1.2; color: var(--ink); letter-spacing: -.035em; }
    .card-meta { color: var(--muted); font-size: .82rem; margin-top: .28rem; }
    .category-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .75rem; padding: 1.1rem 0; border-bottom: 1px solid var(--line); }
    .category-item { display: flex; align-items: center; gap: .55rem; min-width: 0; }
    .category-dot { display: inline-flex; align-items: center; justify-content: center; min-width: 3.6rem; height: 2.35rem; flex: 0 0 auto; padding: 0 .35rem; border-radius: 9px; font-size: .68rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .category-label { color: var(--muted); font-size: .7rem; line-height: 1.2; }
    .fact-grid { margin-top: 1.1rem; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .8rem; }
    .fact-cell { padding: 1rem; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); transition: border-color .16s ease, transform .16s ease; }
    .fact-cell:hover { border-color: #a9d9cc; transform: translateY(-1px); }
    .fact-label { color: var(--muted); font-size: .66rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
    .fact-value { margin-top: .25rem; color: var(--ink); font-size: .9rem; font-weight: 600; line-height: 1.45; }
    
    /* Buttons & Inputs */
    [data-testid="stButton"] button { min-height: 2.75rem; border-radius: 10px; border-color: #c8dcda; color: #096b64; font-weight: 650; transition: transform .16s ease, background .16s ease, border-color .16s ease; }
    [data-testid="stButton"] button:hover { border-color: #79bfb1; background: var(--blue-pale); color: #075d56; transform: translateY(-1px); }
    [data-testid="stForm"] { padding: clamp(1rem, 3vw, 1.5rem); border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow-card); }
    [data-testid="stTextInput"] input { min-height: 3rem; border-radius: 10px; border-color: var(--line); background: var(--surface); }
    
    /* Dark Mode Overrides */
    html[data-theme="dark"] .stApp { background: radial-gradient(ellipse at 5% 0%, rgba(63, 181, 151, .08), transparent 35rem), var(--paper); }
    html[data-theme="dark"] [data-testid="stHeader"] { background: rgba(16, 27, 36, .9); }
    html[data-theme="dark"] .ranking-table th { background: #15364a; }
    html[data-theme="dark"] .ranking-table td { border-color: #30424e; }
    html[data-theme="dark"] .ranking-table tbody tr:hover { background: #203a42; }
    html[data-theme="dark"] .data-chip { border-color: var(--line); background: #203640; color: #d7e5e7; }
    html[data-theme="dark"] [data-testid="stSegmentedControl"] button[aria-pressed="true"] { border-color: #37675e !important; color: #b6efdc !important; background: #23463f !important; }
    html[data-theme="dark"] .category-green-vivid { color: #063c35; background: #8cddbb; }
    html[data-theme="dark"] .category-green-pale { color: #b9efd0; background: #28523e; }
    html[data-theme="dark"] .category-yellow { color: #f4dfa1; background: #51451f; }
    html[data-theme="dark"] .category-orange { color: #f7d1a7; background: #553b27; }
    html[data-theme="dark"] .category-red { color: #fff; background: #a63e47; }
    html[data-theme="dark"] .category-unavailable { background: #354653; }
    html[data-theme="dark"] [data-testid="stButton"] button { border-color: #3a5a63; color: #9de0cf; }
    html[data-theme="dark"] [data-testid="stButton"] button:hover { background: #214541; }
    
    /* Hide some st default menus */
    [data-testid="stMainMenuItem-recordScreencast"] { display: none !important; }
    [data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"] { font-size: 0; }
    [data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"]::after { content: "Imprimer"; font-size: .875rem; }
    [data-testid="stMainMenuItem-rerun"] [data-testid="stMainMenuItemLabel"], [data-testid="stMainMenuItem-clearCache"] [data-testid="stMainMenuItemLabel"] { font-size: 0; }
    [data-testid="stMainMenuItem-rerun"] [data-testid="stMainMenuItemLabel"]::after { content: "Relancer"; font-size: .875rem; }
    [data-testid="stMainMenuItem-clearCache"] [data-testid="stMainMenuItemLabel"]::after { content: "Vider le cache"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-System"], [data-testid="stMainMenuItem-theme-Light"], [data-testid="stMainMenuItem-theme-Dark"] { font-size: 0; }
    [data-testid="stMainMenuItem-theme-System"]::after { content: "Système"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-Light"]::after { content: "Jour"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-Dark"]::after { content: "Nuit"; font-size: .875rem; }
    
    /* MOBILE VIEW */
    @media (max-width: 760px) {
        [data-testid="stMainBlockContainer"] { padding: .85rem .85rem 2.5rem; }
        .masthead { margin-bottom: 1.25rem; padding: 1.2rem 1.1rem 1.3rem; border-radius: 18px; }
        .masthead-body { flex-direction: column; gap: 1rem; margin-top: 1.35rem; align-items: flex-start; }
        .masthead-title { font-size: clamp(1.8rem, 8vw, 2.35rem); }
        .masthead-lede { font-size: .88rem; }
        .masthead-tools { flex-wrap: wrap; }
        .source-badge { display: none; } /* Hide badge on mobile */
        [data-testid="stSegmentedControl"] button { min-height: 2.8rem; font-size: .76rem !important; }
        .section-heading { margin-top: 1.3rem; }
        
        .category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; }
        .indicator-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .fact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; }
        .fact-cell { padding: .8rem; border-radius: 10px; }
        .scpi-card { padding: 1.1rem; border-radius: 17px; }
        
        /* Table overrides for mobile (Flex Layout) */
        .ranking-scroll { max-width: 100%; overflow: visible; border: 0; background: transparent; box-shadow: none; }
        .ranking-table, .ranking-table tbody { display: block; width: 100%; min-width: 0; max-width: 100%; }
        .ranking-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
        
        .ranking-table tbody tr {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.35rem 0.5rem;
            margin-bottom: 0.6rem;
            padding: 0.6rem 0.75rem;
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--surface);
            box-shadow: 0 4px 12px rgba(20, 48, 67, .04);
        }
        
        .ranking-table td { 
            padding: 0; display: flex; flex-direction: row; 
            align-items: center; width: auto; border: none; overflow-wrap: anywhere;
        }
        .ranking-table td::before { 
            content: attr(data-label) " : "; font-size: .6rem; letter-spacing: .05em; 
            margin-right: 0.25rem; font-weight: 700; text-transform: uppercase; color: var(--muted); 
        }
        
        .ranking-table .cell-place { order: 1; font-weight: bold; }
        .ranking-table .cell-place::before { content: "#"; margin-right: 0.1rem; font-size: .75rem; }
        
        .ranking-table .cell-scpi { order: 2; flex: 1 1 auto; }
        .ranking-table .cell-scpi::before { content: none; }
        .ranking-table .cell-scpi .company-link { font-size: 1.05rem; }
        
        .ranking-table .cell-score { order: 3; }
        .ranking-table .cell-score::before { content: none; }
        
        .ranking-table .cell-badge {
            order: 4;
            font-size: 0.7rem;
            background: var(--surface-soft);
            padding: 0.2rem 0.45rem;
            border-radius: 6px;
            border: 1px solid var(--line);
        }
        
        .ranking-table .cell-np {
            background: #e3f5ee;
            border-color: #b8e6d8;
        }
        
        .ranking-table .data-chip { 
            width: auto; max-width: 100%; padding: 0; background: transparent; 
            border: none; font-size: inherit; color: inherit; 
        }
        .ranking-table .discount-value { font-size: inherit; }
        
        html[data-theme="dark"] .ranking-table tbody tr { background: var(--surface); }
        html[data-theme="dark"] .ranking-table .cell-np { background: #1c403f; border-color: #37675e; }
    }
    
    @media (max-width: 420px) {
        [data-testid="stMainBlockContainer"] { padding-right: .65rem; padding-left: .65rem; }
        .profile-label { font-size: .6rem; }
        [data-testid="stSegmentedControl"] button { padding: .35rem .3rem !important; }
        .category-grid { grid-template-columns: 1fr; }
        .fact-grid { grid-template-columns: 1fr 1fr; }
        .ranking-table tbody tr { padding-right: .6rem; padding-left: .6rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


ACCESS_CODE = os.environ.get("SCPISCREEN_ACCESS_CODE", "")
AUTH_COOKIE_NAME = "scpiscreen_access"
AUTH_COOKIE_TTL_SECONDS = 30 * 24 * 60 * 60

if len(ACCESS_CODE) < 16:
    st.error(
        "Authentification non configurée : définissez SCPISCREEN_ACCESS_CODE "
        "(16 caractères minimum) dans l’environnement du serveur."
    )
    st.stop()


def create_access_token() -> str:
    issued_at = str(int(time.time()))
    signature = hmac.new(
        ACCESS_CODE.encode("utf-8"), issued_at.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{issued_at}.{signature}"


def is_valid_access_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        issued_at, signature = token.encode("ascii").decode("ascii").split(".", 1)
        issued_at_seconds = int(issued_at)
    except (UnicodeEncodeError, ValueError):
        return False

    token_age = time.time() - issued_at_seconds
    if token_age < 0 or token_age > AUTH_COOKIE_TTL_SECONDS:
        return False

    expected_signature = hmac.new(
        ACCESS_CODE.encode("utf-8"), issued_at.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


cookie_manager = stx.CookieManager(key="scpiscreen_auth_cookie")
if not st.session_state.get("access_granted", False):
    if is_valid_access_token(cookie_manager.get(AUTH_COOKIE_NAME)):
        st.session_state["access_granted"] = True

if not st.session_state.get("access_granted", False):
    st.markdown(
        "<header class='masthead'><div class='brand-line'><span class='brand-mark'>S</span> SCPIScreen</div>"
        "<div class='masthead-body'><div><div class='masthead-kicker'>Accès privé</div>"
        "<div class='masthead-title'>Classement des SCPI</div>"
        "<div class='masthead-lede'>Saisissez votre code d’accès pour continuer.</div></div></div></header>",
        unsafe_allow_html=True,
    )
    with st.form("access_gate"):
        access_code = st.text_input("Code d’accès", type="password")
        access_submitted = st.form_submit_button("Accéder au classement", type="primary", use_container_width=True)
    if access_submitted:
        if hmac.compare_digest(access_code.encode("utf-8"), ACCESS_CODE.encode("utf-8")):
            st.session_state["access_granted"] = True
            cookie_manager.set(
                AUTH_COOKIE_NAME,
                create_access_token(),
                key="save_scpiscreen_access",
                max_age=AUTH_COOKIE_TTL_SECONDS,
                secure=True,
                same_site="strict",
            )
        else:
            st.error("Code d’accès incorrect.")
    if not st.session_state.get("access_granted", False):
        st.stop()


@st.cache_data(ttl=900, show_spinner=False)
def cached_sheet_data(sheet_id: str) -> dict[str, list[dict[str, str]]]:
    """Cache worksheet data briefly to avoid repeated Google requests."""
    return load_google_sheet(sheet_id, WORKSHEETS)


if st.query_params.get("refresh") == "1":
    cached_sheet_data.clear()
    del st.query_params["refresh"]
    st.rerun()


def display_percent(value: str) -> str:
    value = str(value).strip()
    if not value:
        return "—"
    value = value.replace("%", "").strip()
    return f"{value}%" if value else "—"


def fact_fields(item: dict[str, Any]) -> list[tuple[str, str]]:
    percent_fields = {
        "Bonus Louve",
        "TRI Max Démembrement",
        "Taux de liquidité",
        "Taux d'endettement",
        "Décote",
        "Part de secteur majoritaire",
        "Part de logements hors de  France",
        "Part de région majoritaire",
        "TOF",
        "TOP",
        "TOF-TOP",
        "Frais d'entrée",
        "Frais de gestion",
        "PGA",
        "TRI",
        "Revalorisation annuelle moyenne historique",
        "Revalorisation annuelle moyenne récente",
    }

    def format_value(label: str, value: Any) -> str:
        value = str(value or "").strip()
        if not value:
            return "—"
        if label in percent_fields and not value.endswith("%"):
            value = f"{value}%"
        if label == "WALB" and not value.lower().endswith("ans"):
            return f"{value} ans"
        if label == "Capitalisation (€)" and not value.lower().endswith("millions"):
            return f"{value} millions"
        return value

    rows = [
        ("Bonus Louve", item.get("bonus_louve", "")),
        ("TRI Max Démembrement", item.get("tri_demembrement", "")),
        ("Durée TRI Max Démembrement", item.get("duree_tri_demembrement", "")),
        ("Souscriptions", item.get("souscriptions", "")),
        ("Retraits", item.get("retraits", "")),
        ("Taux de liquidité", item.get("liquidity_rate", "")),
        ("Taux d'endettement", item.get("debt_rate", "")),
        ("Valeur de souscription", item.get("valeur_souscription", "")),
        ("Valeur de reconstitution", item.get("valeur_reconstitution", "")),
        ("Décote", item.get("discount", "")),
        ("Secteurs majoritaires", item.get("main_sectors", "")),
        ("Part de secteur majoritaire", item.get("part_de_secteur_majoritaire", "")),
        ("Part de logements hors de  France", item.get("part_logements_hors_france", "")),
        ("Part de région majoritaire", item.get("part_de_region_majoritaire", "")),
        ("Régions majoritaires", item.get("main_regions", "")),
        ("TOF", item.get("tof", "")),
        ("TOP", item.get("top", "")),
        ("TOF-TOP", item.get("tof_top", "")),
        ("Capitalisation (€)", item.get("capitalisation_m", "")),
        ("Création", item.get("creation", "")),
        ("Frais d'entrée", item.get("frais_entree", "")),
        ("Frais de gestion", item.get("frais_gestion", "")),
        ("PGA", item.get("pga", "")),
        ("TRI", item.get("tri", "")),
        ("WALB", item.get("walb", "")),
        ("Revalorisation annuelle moyenne historique", item.get("revalorisation_annuelle_historique", "")),
        ("Revalorisation annuelle moyenne récente", item.get("revalorisation_annuelle_recente", "")),
        ("Nombre d'actifs", item.get("nombre_actifs", "")),
    ]
    formatted = []
    for label, value in rows:
        formatted.append((label, format_value(label, value)))
    return formatted


load_error = ""
try:
    data = cached_sheet_data(SHEET_ID)
except SheetLoadError as error:
    data = {"data": [], "scores": []}
    load_error = str(error)

masthead_status = "Source connectée"
refresh_params = dict(st.query_params)
refresh_params["refresh"] = "1"
refresh_url = "?" + urlencode(refresh_params)
st.markdown(
        f"""
        <header class="masthead">
            <div class="brand-line"><span class="brand-mark">S</span><span>SCPIScreen</span>
                <span class="brand-divider"></span></div>
            <div class="masthead-body"><div>
                <div class="masthead-title">Classement des SCPI</div>
                <div class="masthead-lede">Selon plusieurs profils d’investissement</div>
            </div><div class="masthead-tools">
                <a class="refresh-action" href="{html.escape(refresh_url, quote=True)}" title="Télécharger à nouveau les données du Google Sheet">Actualiser</a>
                <span class="source-badge">{masthead_status}</span>
            </div></div>
        </header>
        """,
        unsafe_allow_html=True,
)

profile_column, empty_column = st.columns([1.7, 1.3])
with profile_column:
        st.markdown('<div class="profile-label">Profil d’investissement</div>', unsafe_allow_html=True)
        profile = st.segmented_control(
                "Profil d’investissement",
                PROFILE_ORDER,
                default=PROFILE_ORDER[0],
                label_visibility="collapsed",
                key="profile_selector",
                width="stretch",
        )

rankings = build_rankings(data)
rankings.sort(key=lambda item: item["profile_scores"].get(profile or PROFILE_ORDER[0], 0), reverse=True)
profile = profile or PROFILE_ORDER[0]
fact_rows_by_scpi = [fact_fields(item) for item in rankings]

if load_error:
    st.error(f"Les données du Google Sheet ne sont pas accessibles : {html.escape(load_error)}")

selected_scpi_id = st.query_params.get("scpi")
selected_scpi = next(
    (item for item in rankings if item["scpi_id"] == selected_scpi_id),
    None,
)

if selected_scpi is None and selected_scpi_id:
    st.error("Cette fiche SCPI est introuvable dans la source de données.")
    if st.button("Retour au classement", icon=":material/arrow_back:"):
        st.query_params.clear()
        st.rerun()
elif selected_scpi is not None:
    selected_rank = rankings.index(selected_scpi) + 1
    if st.button("Retour au classement", icon=":material/arrow_back:"):
        st.query_params.clear()
        st.rerun()

    item = selected_scpi
    category_markup = []
    for category in CATEGORY_ORDER:
        score = item["category_scores"].get(category)
        if score is None:
            score_text, score_class = "—/100", "category-unavailable"
        else:
            score_text = f"{score:.0f}/100"
            score_class = (
                "category-green-vivid" if score >= 70 else
                "category-green-pale" if score >= 60 else
                "category-yellow" if score >= 50 else
                "category-orange" if score >= 40 else
                "category-red"
            )
        category_markup.append(
            f"<div class='category-item'><span class='category-dot {score_class}'>{score_text}</span>"
            f"<span class='category-label'>{html.escape(category)}</span></div>"
        )

    fact_rows = fact_fields(item)
    fact_cells = "".join(
        f"<div class='fact-cell'><div class='fact-label'>{html.escape(label)}</div><div class='fact-value'>{html.escape(value)}</div></div>"
        for label, value in fact_rows
    )

    if not fact_rows:
        fact_cells = "<div class='fact-cell'><div class='fact-label'>Données</div><div class='fact-value'>À renseigner dans le Google Sheet</div></div>"

    st.markdown(
        f"""
        <article class="scpi-card">
          <div class="card-top">
            <div><div class="card-name">{html.escape(item.get('name', 'SCPI'))}</div>
                            <div class="card-meta">{selected_rank}e place · Profil {html.escape(profile)}</div>
            </div>
          </div>
                    <div class="category-grid">{''.join(category_markup)}</div>
          <div class="fact-grid">{fact_cells}</div>
        </article>
        """,
        unsafe_allow_html=True,
    )
else:
    # Header du tableau avec la barre de recherche intelligente
    col_heading, col_search = st.columns([1.5, 1])
    with col_heading:
        st.markdown(
            f"<div class='section-heading' style='margin-top: 0.5rem; margin-bottom: 0.5rem;'><div><div class='section-kicker'>{html.escape(profile)}</div>"
            "<div class='section-title'>Classement complet</div></div></div>",
            unsafe_allow_html=True,
        )
    with col_search:
        if HAS_KEYUP:
            search_query = st_keyup(
                "Recherche",
                placeholder="🔍 Rechercher une SCPI...",
                label_visibility="collapsed",
                debounce=250 
            )
        else:
            search_query = st.text_input(
                "Recherche",
                placeholder="🔍 Rechercher une SCPI... (Appuyez sur Entrée)",
                label_visibility="collapsed"
            )
    st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True)
    
    table_rows = []
    search_q = search_query.strip().lower() if search_query else ""
    
    for rank, item in enumerate(rankings, start=1):
        scpi_name = html.escape(item.get("name", "SCPI"))
        
        # Filtre de recherche
        if search_q and search_q not in scpi_name.lower():
            continue
            
        score = item["profile_scores"].get(profile, 0)
        score_class = (
            "category-green-vivid" if score >= 70 else
            "category-green-pale" if score >= 60 else
            "category-yellow" if score >= 50 else
            "category-orange" if score >= 40 else
            "category-red"
        )
        scpi_id = quote(str(item.get("scpi_id", "")), safe="")
        discount_value = item.get("discount", "").strip()
        discount = html.escape(display_percent(discount_value))

        pga = html.escape(display_percent(item.get("pga", "")))
        tri = html.escape(display_percent(item.get("tri", "")))
        
        tri_nue_propriete = html.escape(display_percent(item.get("tri_demembrement", "")))
        duree_nue_propriete = str(item.get("duree_tri_demembrement", "")).strip()
        if duree_nue_propriete and not duree_nue_propriete.lower().endswith("ans"):
            duree_nue_propriete = f"{duree_nue_propriete} ans"
            
        bonus_louve = html.escape(display_percent(item.get("bonus_louve", "")))
        
        row_cells = [
            f"<td class='rank-number {'rank-first' if rank == 1 else ''} cell-place' data-label='Place'>{rank:02}</td>",
            f"<td class='cell-scpi' data-label='SCPI'><a class='company-link' href='?scpi={scpi_id}'>{scpi_name}</a></td>",
            f"<td class='cell-score' data-label='Note / 100'><span class='score-pill {score_class}'>{score:.1f}</span></td>",
            f"<td class='cell-badge' data-label='Décote'><span class='discount-value'>{discount}</span></td>",
            f"<td class='cell-badge' data-label='PGA'><span class='discount-value'>{pga}</span></td>",
            f"<td class='cell-badge' data-label='TRI'><span class='discount-value'>{tri}</span></td>",
            f"<td class='cell-badge' data-label='Secteurs majoritaires'><span class='data-chip'>{html.escape(item.get('main_sectors', '—'))}</span></td>",
            f"<td class='cell-badge' data-label='Régions majoritaires'><span class='data-chip'>{html.escape(item.get('main_regions', '—'))}</span></td>",
            f"<td class='cell-badge cell-np' data-label='TRI Max Nue Propriété'><span class='discount-value'>{tri_nue_propriete}</span></td>",
            f"<td class='cell-badge cell-np' data-label='Durée TRI Max Nue Propriété'><span class='discount-value'>{html.escape(duree_nue_propriete or '—')}</span></td>",
            f"<td class='cell-badge' data-label='Bonus Louve (cashback)'><span class='discount-value'>{bonus_louve}</span></td>"
        ]
        
        table_rows.append("<tr>" + "".join(row_cells) + "</tr>")
    
    if not table_rows:
        table_rows.append(
            "<tr><td colspan='9' style='text-align: center; padding: 3rem 1rem; color: var(--muted); font-size: 1rem; border-bottom: none;'>"
            "Aucune SCPI ne correspond à votre recherche.</td></tr>"
        )
        
    st.markdown(
        "<div class='ranking-scroll'><table class='ranking-table'><thead><tr><th>Place</th><th>SCPI</th>"
        "<th>Note / 100</th><th>Décote</th><th>PGA</th><th>TRI</th><th>Secteurs<br>majoritaires</th>"
        "<th>Régions<br>majoritaires</th><th>TRI Max<br>Nue Propriété</th><th>Durée TRI Max<br>Nue Propriété</th>"
        "<th>Bonus Louve<br>(cashback)</th></tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )

st.markdown(
    "**Licence Open Source (GNU GPL v3) :** Cet outil est distribué sous licence libre GNU GPL v3. "
    "Toute redistribution de ce projet ou de ses œuvres dérivées doit être effectuée sous cette même licence, "
    "conserver l'accès libre au code source et aux formules, et maintenir les crédits d'origine. "
    "Les données publiques de marché utilisées proviennent notamment de [Louve Invest](https://www.louveinvest.com/scpi/liste-scpi).\n\n"
    "**Avertissement de responsabilité :** Cet outil est un support quantitatif d'analyse et de décision fondé sur des données publiques et des modèles mathématiques de pondération pour calculer des scores et établir un classement. "
    "Il ne constitue en aucun cas un conseil en investissement ni une activité de démarchage financier au sens de la réglementation de l'AMF. "
    "Il n'a pas vocation à fournir un conseil personnalisé. Les performances passées ne préjugent pas des performances futures."
)