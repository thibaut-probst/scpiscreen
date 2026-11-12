import base64
import hashlib
import html
import logging
import re
from typing import Any

import streamlit as st

from access_gate import require_access
from html_frame import render_html_frame
from method_page import build_method_page_html
from scpi_data import (
    CATEGORY_ORDER,
    DATA_STORE,
    PROFILE_ORDER,
    SHEET_CACHE_TTL_SECONDS,
    WEIGHTS_STORE,
    SheetLoadError,
    build_rankings,
    build_weight_matrix,
    is_missing_value,
    parse_number,
    text_key,
    validate_join,
    validate_missing_criteria,
    validate_scores,
)

st.set_page_config(
    page_title="SCPIScreen",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- Page styles (the ranking table has its own styles inside its frame) -----

st.markdown(
    """
    <style>
    /* Self-hosted variable fonts: see README, static/fonts/. */
    @font-face { font-family: 'DM Sans'; font-style: normal; font-weight: 400 700; font-display: swap; src: url('/app/static/fonts/DMSans-Variable.ttf') format('truetype'); }
    @font-face { font-family: 'Fraunces'; font-style: normal; font-weight: 500 600; font-display: swap; src: url('/app/static/fonts/Fraunces-Variable.ttf') format('truetype'); }

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

    /* Controls */
    .profile-label { margin: .15rem 0 .55rem; color: var(--muted); font-size: .65rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    [data-testid="stSegmentedControl"] { margin-bottom: .5rem; }
    [data-testid="stSegmentedControl"] > div { padding: .28rem; border: 1px solid var(--line); border-radius: 13px; background: var(--surface); box-shadow: 0 5px 18px rgba(20, 48, 67, .045); }
    [data-testid="stSegmentedControl"] button { min-height: 2.7rem; border-radius: 9px !important; font-size: .82rem !important; font-weight: 600 !important; transition: color .16s ease, background .16s ease, box-shadow .16s ease; }
    [data-testid="stSegmentedControl"] button[aria-pressed="true"] { border-color: #b8e6d8 !important; color: #075d56 !important; background: #e3f5ee !important; box-shadow: 0 2px 7px rgba(8, 126, 116, .1) !important; }

    .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin: 1.8rem 0 .9rem; }
    .section-kicker { color: var(--blue); font-size: .66rem; font-weight: 700; letter-spacing: .15em; text-transform: uppercase; }
    .section-title { margin-top: .3rem; color: var(--ink); font-family: 'DM Sans', sans-serif; font-size: 1.35rem; font-weight: 700; letter-spacing: -.035em; }

    /* Buttons & Inputs */
    [data-testid="stButton"] button { min-height: 2.75rem; border-radius: 10px; border-color: #c8dcda; color: #096b64; font-weight: 650; transition: transform .16s ease, background .16s ease, border-color .16s ease; }
    [data-testid="stButton"] button:hover { border-color: #79bfb1; background: var(--blue-pale); color: #075d56; transform: translateY(-1px); }
    [data-testid="stForm"] { padding: clamp(1rem, 3vw, 1.5rem); border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow-card); }
    [data-testid="stTextInput"] input { min-height: 3rem; border-radius: 10px; border-color: var(--line); background: var(--surface); }

    html[data-theme="dark"] .stApp { background: radial-gradient(ellipse at 5% 0%, rgba(63, 181, 151, .08), transparent 35rem), var(--paper); }
    html[data-theme="dark"] [data-testid="stHeader"] { background: rgba(16, 27, 36, .9); }
    html[data-theme="dark"] [data-testid="stSegmentedControl"] button[aria-pressed="true"] { border-color: #37675e !important; color: #b6efdc !important; background: #23463f !important; }
    html[data-theme="dark"] [data-testid="stButton"] button { border-color: #3a5a63; color: #9de0cf; }
    html[data-theme="dark"] [data-testid="stButton"] button:hover { background: #214541; }

    .st-key-page_navigation { margin-bottom: .35rem; }

    /* Profile picker: a segmented control on wide screens, wrapping pills on narrow ones.
       Both widgets share one value (see sync_profile_widgets). */
    .st-key-profile_pills { display: none !important; }
    [data-testid="stPills"] button { min-height: 2.6rem; font-weight: 600; }
    .st-key-cookie_setter { position: absolute; width: 0; height: 0; overflow: hidden; }

    /* Method page */
    .method-intro { max-width: 52rem; margin: .2rem 0 1.2rem; color: var(--muted); font-size: .98rem; line-height: 1.6; }
    .method-intro strong { color: var(--ink); }
    .method-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .9rem; margin-bottom: .6rem; }
    .method-step { padding: 1.2rem 1.3rem; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); box-shadow: var(--shadow-card); }
    .method-step-number { display: inline-flex; align-items: center; justify-content: center; width: 2rem; height: 2rem; border-radius: 9px; color: var(--blue); background: var(--blue-pale); font-size: .85rem; font-weight: 700; }
    .method-step-title { margin-top: .8rem; color: var(--ink); font-size: 1rem; font-weight: 700; letter-spacing: -.02em; }
    .method-step-text { margin-top: .35rem; color: var(--muted); font-size: .86rem; line-height: 1.55; }

    .profile-cards { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .8rem; }
    .profile-card { padding: 1.1rem 1.15rem; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); box-shadow: var(--shadow-card); }
    .profile-card-name { color: var(--ink); font-family: 'Fraunces', Georgia, serif; font-size: 1.2rem; letter-spacing: -.03em; }
    .profile-card-focus { margin-top: .25rem; color: var(--muted); font-size: .74rem; line-height: 1.4; }
    .priority-list { display: grid; gap: .5rem; margin: .85rem 0 0; padding: .8rem 0 0; border-top: 1px solid var(--line); list-style: none; }
    .priority-list li { display: flex; justify-content: space-between; align-items: baseline; gap: .6rem; margin: 0; color: var(--ink); font-size: .8rem; line-height: 1.35; }
    .priority-weight { flex: 0 0 auto; color: var(--blue); font-weight: 700; font-variant-numeric: tabular-nums; }

    .weights-scroll { width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 17px; background: var(--surface); box-shadow: var(--shadow-card); }
    .weights-table { width: 100%; min-width: 720px; border-collapse: separate; border-spacing: 0; }
    .weights-table thead th, .weights-table tbody th, .weights-table tbody td { border-top: 0; border-left: 0; border-right: 0; }
    .weights-table thead th { padding: .95rem 1rem; border-bottom: 1px solid rgba(188, 236, 223, .16); color: #dce9f5; background: #15364f; font-size: .64rem; font-weight: 700; letter-spacing: .1em; text-align: center; text-transform: uppercase; }
    .weights-table thead th:first-child { text-align: left; }
    .weights-table tbody th, .weights-table tbody td { padding: .68rem 1rem; border-bottom: 1px solid var(--line); color: var(--ink); font-size: .84rem; line-height: 1.4; }
    .weights-table tbody th { background: var(--surface); font-weight: 500; text-align: left; }
    .weights-table tbody th.criterion-name { padding-left: 1.7rem; }
    .weights-table .weight-cell { background: color-mix(in srgb, var(--blue) var(--heat, 0%), transparent); font-variant-numeric: tabular-nums; text-align: center; }
    .weights-table .weight-cell.is-zero { color: var(--muted); opacity: .6; }
    .weights-table .group-row th, .weights-table .group-row td { background: var(--blue-pale); font-weight: 700; }
    .weights-table .total-row th, .weights-table .total-row td { border-bottom: 0; color: #f5f9fd; background: var(--blue-deep); font-weight: 700; }
    .weights-table .total-row .weight-cell.is-off { color: #ffb4a8; }
    .weights-table thead th:first-child, .weights-table tbody th { position: sticky; left: 0; z-index: 1; box-shadow: 1px 0 0 var(--line); }
    .weights-table thead th:first-child { z-index: 2; }

    @media (max-width: 1100px) {
        .profile-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }

    @media (max-width: 760px) {
        [data-testid="stMainBlockContainer"] { padding: .85rem .85rem 2.5rem; }
        .st-key-profile_segmented { display: none !important; }
        .st-key-profile_pills { display: block !important; }
        .masthead { margin-bottom: 1.25rem; padding: 1.2rem 1.1rem 1.3rem; border-radius: 18px; }
        .masthead-body { flex-direction: column; gap: 1rem; margin-top: 1.35rem; align-items: flex-start; }
        .masthead-title { font-size: clamp(1.8rem, 8vw, 2.35rem); }
        .masthead-lede { font-size: .88rem; }
        [data-testid="stSegmentedControl"] button { min-height: 2.8rem; font-size: .76rem !important; }
        .section-heading { margin-top: 1.3rem; margin-bottom: 0.5rem; }

        .method-steps, .profile-cards { grid-template-columns: 1fr; }
        .method-intro { font-size: .9rem; }
        .weights-table { min-width: 560px; }
        .weights-table thead th { padding: .75rem .45rem; font-size: .58rem; letter-spacing: .05em; }
        .weights-table tbody th, .weights-table tbody td { padding: .55rem .5rem; font-size: .78rem; }
        .weights-table thead th:first-child, .weights-table tbody th { min-width: 9.5rem; max-width: 9.5rem; }
        .weights-table tbody th.criterion-name { padding-left: .9rem; }
    }

    @media (max-width: 420px) {
        [data-testid="stMainBlockContainer"] { padding-right: .65rem; padding-left: .65rem; }
        .profile-label { font-size: .6rem; }
        [data-testid="stSegmentedControl"] button { padding: .35rem .3rem !important; }
    }

    /* Hide some st default menus. "Vider le cache" is hidden: it would clear the cache for everyone. */
    [data-testid="stMainMenuItem-recordScreencast"] { display: none !important; }
    [data-testid="stMainMenuItem-clearCache"] { display: none !important; }
    [data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"] { font-size: 0; }
    [data-testid="stMainMenuItem-print"] [data-testid="stMainMenuItemLabel"]::after { content: "Imprimer"; font-size: .875rem; }
    [data-testid="stMainMenuItem-rerun"] [data-testid="stMainMenuItemLabel"] { font-size: 0; }
    [data-testid="stMainMenuItem-rerun"] [data-testid="stMainMenuItemLabel"]::after { content: "Relancer"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-System"], [data-testid="stMainMenuItem-theme-Light"], [data-testid="stMainMenuItem-theme-Dark"] { font-size: 0; }
    [data-testid="stMainMenuItem-theme-System"]::after { content: "Système"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-Light"]::after { content: "Jour"; font-size: .875rem; }
    [data-testid="stMainMenuItem-theme-Dark"]::after { content: "Nuit"; font-size: .875rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- Styles and script of the ranking frame ---------------------------------
# The frame is a separate document: it cannot see the page CSS, and the page
# cannot reach into it. Its colours are CSS variables, switched by theme class.

_DARK_DECLARATIONS = (
    "--ink: #e8edf2; --muted: #a7b7c6; --blue: #72d5bf; --blue-pale: #1c403f; --line: #304a57; "
    "--paper: #101b24; --surface: #182832; --surface-soft: #1d303a; --shadow: 0 18px 48px rgba(0, 0, 0, .2); "
    "--thead: #15364a; --thead-line: #315a80; --thead-hover: #1c4660; --thead-ink: #dce9f5; "
    "--row-hover: #203a42; --chip-bg: #203640; --chip-ink: #d7e5e7; --chip-line: #304a57; "
    "--cat-vivid-ink: #063c35; --cat-vivid-bg: #8cddbb; --cat-pale-ink: #b9efd0; --cat-pale-bg: #28523e; "
    "--cat-yellow-ink: #f4dfa1; --cat-yellow-bg: #51451f; --cat-orange-ink: #f7d1a7; --cat-orange-bg: #553b27; "
    "--cat-red-ink: #fff; --cat-red-bg: #a63e47; --cat-na-ink: #a7b7c6; --cat-na-bg: #354653;"
)
_DARK_BLOCK = (
    f"body.dark {{ {_DARK_DECLARATIONS} }}\n"
    f"@media (prefers-color-scheme: dark) {{ body:not(.light) {{ {_DARK_DECLARATIONS} }} }}"
)

_FRAME_CSS_TEMPLATE = """
@font-face { font-family: 'DM Sans'; font-style: normal; font-weight: 400 700; font-display: swap; src: url('/app/static/fonts/DMSans-Variable.ttf') format('truetype'); }
@font-face { font-family: 'Fraunces'; font-style: normal; font-weight: 500 600; font-display: swap; src: url('/app/static/fonts/Fraunces-Variable.ttf') format('truetype'); }

:root {
    --ink: #152a40; --muted: #657a8d; --blue: #087e74; --blue-pale: #e2f4ef; --line: #dce7e8;
    --paper: #f4f8f7; --surface: #fff; --surface-soft: #f8fbfa;
    --shadow: 0 18px 48px rgba(20, 48, 67, .075), 0 2px 8px rgba(20, 48, 67, .035);
    --thead: #15364f; --thead-line: #315a80; --thead-hover: #1a4261; --thead-ink: #dce9f5;
    --row-hover: #f0f8f5; --chip-bg: #f4f8f7; --chip-ink: #435b68; --chip-line: #e6eeeb;
    --cat-vivid-ink: #075c4a; --cat-vivid-bg: #aee9ce;
    --cat-pale-ink: #176747; --cat-pale-bg: #e0f4e8;
    --cat-yellow-ink: #735500; --cat-yellow-bg: #fff1bf;
    --cat-orange-ink: #78410f; --cat-orange-bg: #f9d6ae;
    --cat-red-ink: #fff; --cat-red-bg: #c84d51;
    --cat-na-ink: #657a8d; --cat-na-bg: #e9eef3;
}
__DARK_BLOCK__

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: transparent; color: var(--ink); font-family: 'DM Sans', sans-serif; }
[hidden] { display: none !important; }
/* flow-root keeps the margins of the children inside, so the measured height is the real one. */
.app { display: flow-root; padding-bottom: 8px; }
.app.show-detail .list { display: none; }

/* Toolbar: search field (and the cards / table switch on narrow screens) */
.toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .7rem; margin: 0 0 .9rem; }
.search-field { position: relative; flex: 1 1 16rem; max-width: 26rem; }
#search { width: 100%; min-height: 2.75rem; padding: .5rem 2.5rem .5rem .85rem; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); font-family: inherit; font-size: .9rem; -webkit-appearance: none; appearance: none; }
#search::-webkit-search-cancel-button { display: none; }
#search:focus-visible, .view-toggle button:focus-visible, .ranking-table th:focus-visible, .company-link:focus-visible, .back:focus-visible, #mobile-sort:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
#search-clear { position: absolute; top: 50%; right: .35rem; min-width: 1.9rem; min-height: 1.9rem; padding: 0 .5rem; transform: translateY(-50%); border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-size: .85rem; line-height: 1; cursor: pointer; }
#search-clear:hover, #search-clear:focus-visible { color: var(--ink); background: var(--blue-pale); }
.view-toggle { display: none; gap: .3rem; padding: .25rem; border: 1px solid var(--line); border-radius: 11px; background: var(--surface); }
.view-toggle button { min-height: 2.3rem; padding: .3rem .95rem; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-family: inherit; font-size: .8rem; font-weight: 650; cursor: pointer; }
.view-toggle button[aria-pressed="true"] { color: var(--blue); background: var(--blue-pale); }

/* Table */
.scroll { width: 100%; overflow: auto; border: 1px solid var(--line); border-radius: 17px; background: var(--surface); box-shadow: var(--shadow); }
.ranking-table { width: 100%; min-width: 1250px; border-collapse: separate; border-spacing: 0; }
.ranking-table th { position: sticky; top: 0; z-index: 1; color: var(--thead-ink); background: var(--thead); text-align: center; text-transform: uppercase; font-size: .64rem; font-weight: 700; letter-spacing: .12em; padding: 1rem 1.1rem; border-bottom: 1px solid var(--thead-line); white-space: nowrap; cursor: pointer; user-select: none; transition: background-color .2s ease; }
.ranking-table th:hover { background: var(--thead-hover); }
.ranking-table th:first-child { padding-left: 1.2rem; border-top-left-radius: 16px; }
.ranking-table th:last-child { border-top-right-radius: 16px; }
.ranking-table th .icon { font-size: .85em; margin-left: 3px; }
.ranking-table td { padding: 1rem 1.1rem; border-bottom: 1px solid var(--line); font-size: .84rem; line-height: 1.5; vertical-align: middle; color: var(--ink); }
.ranking-table tr:last-child td { border-bottom: 0; }
.ranking-table tbody tr { transition: background-color .16s ease; }
.ranking-table tbody tr:hover { background: var(--row-hover); }
.ranking-table td.empty { text-align: center; padding: 3rem 1rem; color: var(--muted); font-size: 1rem; border-bottom: none; }

.rank-number { color: #8a9aa8; font-size: .78rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.rank-first { color: #087e74; }
.company-link { color: var(--ink); font-weight: 700; text-decoration: none; text-underline-offset: 4px; cursor: pointer; }
.company-link:hover { color: var(--blue); text-decoration: underline; }
.data-chip { display: inline-block; padding: .34rem .58rem; border: 1px solid var(--chip-line); border-radius: 999px; background: var(--chip-bg); color: var(--chip-ink); font-size: .75rem; line-height: 1.45; }
.discount-value { color: var(--ink); font-size: .83rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.score-pill { display: inline-flex; align-items: center; justify-content: center; padding: .2rem .45rem; font-size: .75rem; font-weight: 700; font-variant-numeric: tabular-nums; border-radius: 6px; line-height: 1; }

.category-green-vivid { color: var(--cat-vivid-ink); background: var(--cat-vivid-bg); }
.category-green-pale { color: var(--cat-pale-ink); background: var(--cat-pale-bg); }
.category-yellow { color: var(--cat-yellow-ink); background: var(--cat-yellow-bg); }
.category-orange { color: var(--cat-orange-ink); background: var(--cat-orange-bg); }
.category-red { color: var(--cat-red-ink); background: var(--cat-red-bg); }
.category-unavailable { color: var(--cat-na-ink); background: var(--cat-na-bg); }

/* Mobile sort select (shown only in cards view on narrow screens) */
.mobile-sort { display: none; align-items: center; gap: .5rem; margin-bottom: .8rem; }
.mobile-sort label { color: var(--muted); font-size: .8rem; font-weight: 700; }
#mobile-sort { flex: 1; padding: .35rem .5rem; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink); font-family: inherit; font-size: .85rem; }

/* Fiche SCPI */
.detail { scroll-margin-top: 4.5rem; }
.back { min-height: 2.75rem; margin: 0 0 1rem; padding: .6rem 1rem; border: 1px solid #c8dcda; border-radius: 10px; background: var(--surface); color: var(--blue); font-family: inherit; font-size: .9rem; font-weight: 650; cursor: pointer; }
.back:hover { background: var(--blue-pale); }
.scpi-card { margin: .2rem 0 1rem; padding: clamp(1.2rem, 3vw, 2rem); border: 1px solid var(--line); border-radius: 20px; background: var(--surface); box-shadow: var(--shadow); }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; padding-bottom: 1.25rem; border-bottom: 1px solid var(--line); }
.card-name { font-family: 'Fraunces', Georgia, serif; font-size: clamp(1.5rem, 3vw, 2rem); line-height: 1.2; color: var(--ink); letter-spacing: -.035em; }
.card-meta { color: var(--muted); font-size: .82rem; margin-top: .28rem; }
.category-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .75rem; padding: 1.1rem 0; border-bottom: 1px solid var(--line); }
.category-item { display: flex; align-items: center; gap: .55rem; min-width: 0; }
.category-dot { display: inline-flex; align-items: center; justify-content: center; min-width: 3.6rem; height: 2.35rem; flex: 0 0 auto; padding: 0 .35rem; border-radius: 9px; font-size: .68rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.category-label { color: var(--muted); font-size: .7rem; line-height: 1.2; }
.fact-grid { margin-top: 1.1rem; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .8rem; }
.fact-cell { padding: 1rem; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); }
.fact-label { color: var(--muted); font-size: .66rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.fact-value { margin-top: .25rem; color: var(--ink); font-size: .9rem; font-weight: 600; line-height: 1.45; }

@media (max-width: 760px) {
    .toolbar { flex-direction: column; align-items: stretch; }
    .search-field { flex: 0 0 auto; max-width: none; }
    .view-toggle { display: flex; }
    .view-toggle button { flex: 1; }
    .view-cards .mobile-sort { display: flex; }
    .view-table .ranking-table th, .view-table .ranking-table td { padding: .75rem .8rem; }
    .view-table .ranking-table td.cell-scpi { position: sticky; left: 0; z-index: 1; background: var(--surface); box-shadow: 1px 0 0 var(--line); }
    .view-table .ranking-table th:nth-child(2) { left: 0; z-index: 3; }

    .category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; }
    .fact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; }
    .fact-cell { padding: .8rem; border-radius: 10px; }
    .scpi-card { padding: 1.1rem; border-radius: 17px; }

    /* Cards: the same table rows, laid out as flex cards */
    .view-cards .scroll { max-width: 100%; overflow: visible; border: 0; background: transparent; box-shadow: none; }
    .view-cards .ranking-table, .view-cards .ranking-table tbody { display: block; width: 100%; min-width: 0; max-width: 100%; }
    .view-cards .ranking-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
    .view-cards .ranking-table tbody tr { display: flex; flex-wrap: wrap; align-items: baseline; gap: .15rem .4rem; margin-bottom: .6rem; padding: .6rem .75rem; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); box-shadow: 0 4px 12px rgba(20, 48, 67, .04); }
    .view-cards .ranking-table td { padding: 0; display: inline-flex; flex-direction: row; align-items: baseline; width: auto; border: none; overflow-wrap: anywhere; }
    .view-cards .ranking-table td::before { content: attr(data-label) " "; font-size: .65rem; letter-spacing: .02em; margin-right: .15rem; font-weight: 700; text-transform: uppercase; color: var(--muted); }
    .view-cards .cell-place { order: 1; font-weight: bold; margin-right: .3rem; }
    .view-cards .cell-place::before { content: "#"; margin-right: .1rem; font-size: .75rem; }
    .view-cards .cell-scpi { order: 2; margin-right: .5rem; }
    .view-cards .cell-scpi::before { content: none; }
    .view-cards .cell-scpi .company-link { font-size: 1.05rem; }
    .view-cards .cell-score { order: 3; flex: 1 1 auto; margin-bottom: .3rem; }
    .view-cards .cell-score::before { content: none; }
    .view-cards .cell-badge { order: 4; font-size: .75rem; background: transparent; padding: 0; border: none; margin-right: .5rem; margin-bottom: .2rem; }
    .view-cards .cell-np { background: transparent !important; border-color: transparent !important; }
    .view-cards .data-chip { width: auto; max-width: 100%; padding: 0; background: transparent; border: none; font-size: inherit; color: inherit; font-weight: 600; }
    .view-cards .discount-value { font-size: inherit; font-weight: 600; }
}

@media (max-width: 420px) {
    .category-grid { grid-template-columns: 1fr; }
    .fact-grid { grid-template-columns: 1fr 1fr; }
    .view-cards .ranking-table tbody tr { padding-right: .6rem; padding-left: .6rem; }
}
"""

FRAME_CSS = _FRAME_CSS_TEMPLATE.replace("__DARK_BLOCK__", _DARK_BLOCK)

FRAME_JS = """
(function () {
    var app = document.querySelector('.app');
    var table = document.getElementById('ranking');
    var select = document.getElementById('mobile-sort');
    var search = document.getElementById('search');
    var clearButton = document.getElementById('search-clear');
    var emptyRow = document.getElementById('empty-row');
    var headers = table ? Array.prototype.slice.call(table.querySelectorAll('th.sort-btn')) : [];
    var viewButtons = Array.prototype.slice.call(document.querySelectorAll('[data-view]'));
    var lastOpener = null;

    // The frame is as tall as its content: no inner scrollbar, no empty space. The frame has the
    // same origin as the page, so it can resize its own <iframe>. Streamlit fixes the height of the
    // containers around the iframe from the initial height: they must follow too, otherwise the
    // content overflows onto the elements below. If resizing is not allowed, the initial height
    // given by the server is kept.
    function fitHeight() {
        try {
            var frame = window.frameElement;
            if (!frame || !app) { return; }
            var height = Math.ceil(app.getBoundingClientRect().height) + 'px';
            var limit = frame.closest('[data-testid="stElementContainer"], .element-container, .stElementContainer');
            var node = frame;
            while (node) {
                node.style.height = height;
                if (node === limit || !limit) { break; }
                node = node.parentElement;
            }
        } catch (error) { /* keep the initial height */ }
    }

    function scrollTo(element, block) {
        try { element.scrollIntoView({ block: block }); } catch (error) { /* ignore */ }
    }

    function normalize(text) {
        return String(text).normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase()
            .replace(/\\u0153/g, 'oe').replace(/\\u00e6/g, 'ae').replace(/[\\u2018\\u2019\\u02bc]/g, "'")
            .replace(/\\s+/g, ' ').trim();
    }

    // Missing values (empty data-sort) always go last, whatever the direction.
    function sortRows(column, type, direction) {
        if (!table) { return; }
        var tbody = table.tBodies[0];
        var rows = Array.prototype.slice.call(tbody.rows).filter(function (row) { return row !== emptyRow; });
        rows.sort(function (a, b) {
            var left = a.cells[column].getAttribute('data-sort') || '';
            var right = b.cells[column].getAttribute('data-sort') || '';
            var result;
            if (type === 'num') {
                var leftNumber = parseFloat(left);
                var rightNumber = parseFloat(right);
                var leftMissing = isNaN(leftNumber);
                var rightMissing = isNaN(rightNumber);
                if (leftMissing || rightMissing) {
                    return leftMissing && rightMissing ? 0 : (leftMissing ? 1 : -1);
                }
                result = leftNumber === rightNumber ? 0 : (leftNumber < rightNumber ? -1 : 1);
            } else {
                result = left.localeCompare(right, 'fr');
            }
            return direction === 'asc' ? result : -result;
        });
        rows.forEach(function (row) { tbody.appendChild(row); });
        if (emptyRow) { tbody.appendChild(emptyRow); }
    }

    headers.forEach(function (th, index) {
        function activate() {
            var direction = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
            headers.forEach(function (header) {
                header.setAttribute('data-dir', 'none');
                header.setAttribute('aria-sort', 'none');
                var icon = header.querySelector('.icon');
                icon.textContent = '\\u2195';
                icon.style.opacity = '0.3';
            });
            th.setAttribute('data-dir', direction);
            th.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
            var activeIcon = th.querySelector('.icon');
            activeIcon.textContent = direction === 'asc' ? '\\u2191' : '\\u2193';
            activeIcon.style.opacity = '1';
            sortRows(index, th.getAttribute('data-type'), direction);
        }
        th.addEventListener('click', activate);
        th.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
        });
    });

    if (select) {
        select.addEventListener('change', function () {
            var option = select.options[select.selectedIndex];
            sortRows(parseInt(option.value, 10), option.getAttribute('data-type'), option.getAttribute('data-dir'));
        });
    }

    // Search: filters the rows in the browser (accents and case ignored). No server round trip,
    // and the current sort order is kept.
    function applyFilter() {
        if (!table) { return; }
        var query = search ? normalize(search.value) : '';
        var visible = 0;
        Array.prototype.forEach.call(table.tBodies[0].rows, function (row) {
            if (row === emptyRow) { return; }
            var match = !query || (row.getAttribute('data-name') || '').indexOf(query) !== -1;
            row.hidden = !match;
            if (match) { visible += 1; }
        });
        if (emptyRow) { emptyRow.hidden = visible !== 0; }
        if (clearButton) { clearButton.hidden = !(search && search.value); }
        fitHeight();
    }
    if (search) { search.addEventListener('input', applyFilter); }
    if (clearButton) {
        clearButton.addEventListener('click', function () {
            search.value = '';
            applyFilter();
            search.focus();
        });
    }

    viewButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var view = button.getAttribute('data-view');
            app.classList.toggle('view-cards', view === 'cards');
            app.classList.toggle('view-table', view === 'table');
            viewButtons.forEach(function (other) {
                other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
            });
            fitHeight();
        });
    });

    function showDetail(id, scroll) {
        Array.prototype.forEach.call(document.querySelectorAll('.detail'), function (detail) {
            detail.hidden = detail.getAttribute('data-id') !== id;
        });
        app.classList.add('show-detail');
        fitHeight();
        var shown = document.querySelector('.detail:not([hidden])');
        if (shown && scroll) { scrollTo(shown, 'start'); }
    }

    function showList() {
        Array.prototype.forEach.call(document.querySelectorAll('.detail'), function (detail) {
            detail.hidden = true;
        });
        app.classList.remove('show-detail');
        fitHeight();
        if (lastOpener) { scrollTo(lastOpener, 'center'); }
    }

    document.addEventListener('click', function (event) {
        var opener = event.target.closest('[data-open]');
        if (opener) {
            event.preventDefault();
            lastOpener = opener;
            showDetail(opener.getAttribute('data-open'), true);
            return;
        }
        if (event.target.closest('[data-back]')) {
            showList();
        }
    });

    if (window.ResizeObserver && app) { new ResizeObserver(fitHeight).observe(app); }
    window.addEventListener('load', fitHeight);

    var initial = app.getAttribute('data-initial');
    if (initial) { showDetail(initial, false); }
    fitHeight();
})();
"""

# The frame runs with allow-scripts and allow-same-origin, so it is NOT a security boundary.
# This Content-Security-Policy (set through a <meta> tag in the frame document) is the second
# line of defence: only the exact script above may run, and nothing can be loaded from or sent
# to another host, even if an escaping bug ever let markup through.
_FRAME_JS_HASH = base64.b64encode(hashlib.sha256(FRAME_JS.encode("utf-8")).digest()).decode("ascii")
FRAME_CSP = (
    "default-src 'none'; "
    f"script-src 'sha256-{_FRAME_JS_HASH}'; "
    "style-src 'unsafe-inline'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

# ---- Constants ----------------------------------------------------------------

logger = logging.getLogger("scpiscreen")
SEARCH_MAX_LENGTH = 80
MAX_WARNINGS_DISPLAYED = 8
MARKDOWN_SPECIAL_CHARACTERS = re.compile(r"([\\`*_\[\]<>|#~])")

PAGE_RANKING = "Classement"
PAGE_METHOD = "Méthode de classement"
PAGE_OPTIONS = [PAGE_RANKING, PAGE_METHOD]
PAGE_QUERY_METHOD = "methode"

VIEW_CARDS_LABEL = "Cartes"
VIEW_TABLE_LABEL = "Tableau"

# (key, header label, sort type). Column order matches the mobile sort select below.
TABLE_HEADERS = (
    ("place", "Place", "num"),
    ("scpi", "SCPI", "str"),
    ("score", "Note / 100", "num"),
    ("discount", "Décote", "num"),
    ("pga", "PGA", "num"),
    ("tri", "TRI", "num"),
    ("sectors", "Secteurs<br>majoritaires", "str"),
    ("regions", "Régions<br>majoritaires", "str"),
    ("tri_np", "TRI Max<br>Nue Propriété", "num"),
    ("duree_np", "Durée TRI Max<br>Nue Propriété", "num"),
    ("bonus", "Bonus Louve<br>(cashback)", "num"),
)
# (label, column index, direction) for the mobile select; the first entry is the default order.
MOBILE_SORT_CHOICES = (
    ("Note / 100", 2, "desc"),
    ("Décote", 3, "desc"),
    ("PGA", 4, "desc"),
    ("TRI", 5, "desc"),
    ("TRI Max Nue Propriété", 8, "desc"),
)

# Initial height of the ranking frame (pixels). The frame then resizes itself to its content;
# this value is only used for the first paint, or when the browser forbids the resize.
FRAME_INITIAL_HEIGHT_PX = 900


# ---- Helpers ------------------------------------------------------------------

def current_theme() -> str:
    """Streamlit theme type when exposed by the version in use, otherwise an empty string."""
    theme_type = getattr(getattr(st.context, "theme", None), "type", None)
    return theme_type if theme_type in ("light", "dark") else ""


def md_text(value: str) -> str:
    """Escape text coming from the sheet before showing it as Markdown."""
    return MARKDOWN_SPECIAL_CHARACTERS.sub(r"\\\1", html.escape(str(value), quote=False))


def display_percent(value: str) -> str:
    """A percentage as shown in the table; empty and "NA"-like values become a dash."""
    text = str(value).strip()
    if is_missing_value(text):
        return "—"
    text = text.replace("%", "").strip()
    return f"{text}%" if text else "—"


def safe_float(value: Any) -> float | None:
    """Sort value of a sheet cell; None when the cell has no usable number."""
    return parse_number(value)


def format_score(score: float) -> str:
    """Score with a decimal comma, as everywhere else in the French interface."""
    return f"{score:.1f}".replace(".", ",")


def category_class(score: float) -> str:
    return (
        "category-green-vivid" if score >= 70 else
        "category-green-pale" if score >= 60 else
        "category-yellow" if score >= 50 else
        "category-orange" if score >= 40 else
        "category-red"
    )


def fact_fields(item: dict[str, Any]) -> list[tuple[str, str]]:
    percent_fields = {
        "Bonus Louve", "TRI Max Nue-Propriété", "Taux de liquidité", "Taux d'endettement",
        "Décote", "Part de secteur majoritaire", "Part de logements hors de France",
        "Part de région majoritaire", "TOF", "TOP", "TOF-TOP", "Frais d'entrée",
        "Frais de gestion", "PGA", "TRI", "Revalorisation annuelle moyenne historique",
        "Revalorisation annuelle moyenne récente",
    }

    def format_value(label: str, value: Any) -> str:
        value = str(value or "").strip()
        if is_missing_value(value):
            return "—"
        if label in percent_fields and not value.endswith("%"):
            value = f"{value}%"
        if label in ("WALB", "Durée TRI Max Nue-Propriété") and not value.lower().endswith("ans"):
            return f"{value} ans"
        if label == "Capitalisation (€)" and not value.lower().endswith("millions"):
            return f"{value} millions"
        return value

    rows = [
        ("Bonus Louve", item.get("bonus_louve", "")),
        ("TRI Max Nue-Propriété", item.get("tri_demembrement", "")),
        ("Durée TRI Max Nue-Propriété", item.get("duree_tri_demembrement", "")),
        ("Souscriptions", item.get("souscriptions", "")),
        ("Retraits", item.get("retraits", "")),
        ("Taux de liquidité", item.get("liquidity_rate", "")),
        ("Taux d'endettement", item.get("debt_rate", "")),
        ("Valeur de souscription", item.get("valeur_souscription", "")),
        ("Valeur de reconstitution", item.get("valeur_reconstitution", "")),
        ("Décote", item.get("discount", "")),
        ("Secteurs majoritaires", item.get("main_sectors", "")),
        ("Part de secteur majoritaire", item.get("part_de_secteur_majoritaire", "")),
        ("Part de logements hors de France", item.get("part_logements_hors_france", "")),
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
    return [(label, format_value(label, value)) for label, value in rows]


# ---- Profile picker ---------------------------------------------------------
# Two widgets show the same choice: a segmented control (wide screens) and wrapping pills
# (narrow screens); CSS hides one of them. The canonical value is a plain session key, which
# also survives the pages where the widgets are not rendered.

PROFILE_VALUE_KEY = "profile_value"
PROFILE_SEGMENTED_KEY = "profile_segmented"
PROFILE_PILLS_KEY = "profile_pills"


def sync_profile_widgets() -> str:
    """Return the chosen profile and make both widgets show it (call before creating them)."""
    current = st.session_state.get(PROFILE_VALUE_KEY)
    if current not in PROFILE_ORDER:
        current = PROFILE_ORDER[0]
    st.session_state[PROFILE_VALUE_KEY] = current
    st.session_state[PROFILE_SEGMENTED_KEY] = current
    st.session_state[PROFILE_PILLS_KEY] = current
    return current


def handle_profile_change(widget_key: str) -> None:
    """Callback of both widgets. Deselecting (None) is ignored: the next run restores the choice."""
    value = st.session_state.get(widget_key)
    if value in PROFILE_ORDER:
        st.session_state[PROFILE_VALUE_KEY] = value


# ---- Ranking frame builders ---------------------------------------------------

def _sort_attribute(sort_value: float | None) -> str:
    """Content of data-sort: empty for a missing value, which the frame always sorts last."""
    return "" if sort_value is None else str(sort_value)


def cell_badge(label: str, text: str, sort_value: float | None, extra_class: str = "") -> str:
    return (
        f"<td class='cell-badge{extra_class}' data-label='{label}' data-sort='{_sort_attribute(sort_value)}'>"
        f"<span class='discount-value'>{html.escape(text)}</span></td>"
    )


def chip_cell(label: str, text: str, sort_text: str) -> str:
    return (
        f"<td class='cell-badge' data-label='{label}' data-sort='{html.escape(sort_text.lower(), quote=True)}'>"
        f"<span class='data-chip'>{html.escape(text or '—')}</span></td>"
    )


def render_row(item: dict[str, Any], profile: str) -> str:
    rank = item.get("original_rank")  # None when the SCPI is not ranked for this profile
    score = item["profile_scores"].get(profile)
    sort_rank = item.get("sort_rank", rank or 0)
    name = str(item.get("name", "SCPI"))
    scpi_id = html.escape(str(item.get("scpi_id", "")), quote=True)
    duree = str(item.get("duree_tri_demembrement", "")).strip()
    if is_missing_value(duree):
        duree = ""
    elif not duree.lower().endswith("ans"):
        duree = f"{duree} ans"
    rank_class = " rank-first" if rank == 1 else ""
    place_text = f"{rank:02}" if rank is not None else "—"
    if score is not None:
        score_cell = (
            f"<td class='cell-score' data-label='Note / 100' data-sort='{score}'>"
            f"<span class='score-pill {category_class(score)}'>{format_score(score)}</span></td>"
        )
    else:
        score_cell = (
            "<td class='cell-score' data-label='Note / 100' data-sort=''>"
            "<span class='score-pill category-unavailable'>—</span></td>"
        )
    cells = [
        f"<td class='rank-number{rank_class} cell-place' data-label='Place' data-sort='{sort_rank}'>{place_text}</td>",
        f"<td class='cell-scpi' data-label='SCPI' data-sort='{html.escape(name.lower(), quote=True)}'>"
        f"<a class='company-link' href='#' data-open='{scpi_id}'>{html.escape(name)}</a></td>",
        score_cell,
        cell_badge("Décote", display_percent(item.get("discount", "")), safe_float(item.get("discount"))),
        cell_badge("PGA", display_percent(item.get("pga", "")), safe_float(item.get("pga"))),
        cell_badge("TRI", display_percent(item.get("tri", "")), safe_float(item.get("tri"))),
        chip_cell("Secteurs majoritaires", item.get("main_sectors", ""), item.get("main_sectors", "")),
        chip_cell("Régions majoritaires", item.get("main_regions", ""), item.get("main_regions", "")),
        cell_badge(
            "TRI Max Nue Propriété",
            display_percent(item.get("tri_demembrement", "")),
            safe_float(item.get("tri_demembrement")),
            " cell-np",
        ),
        cell_badge(
            "Durée TRI Max Nue Propriété",
            duree or "—",
            safe_float(item.get("duree_tri_demembrement")),
            " cell-np",
        ),
        cell_badge("Bonus Louve (cashback)", display_percent(item.get("bonus_louve", "")), safe_float(item.get("bonus_louve"))),
    ]
    # data-name: normalized name used by the in-frame search (accents and case ignored).
    return f"<tr data-name='{html.escape(text_key(name), quote=True)}'>" + "".join(cells) + "</tr>"


def render_detail(item: dict[str, Any], profile: str) -> str:
    rank = item.get("original_rank")
    scpi_id = html.escape(str(item.get("scpi_id", "")), quote=True)
    name = html.escape(str(item.get("name", "SCPI")))
    category_markup = []
    for category in CATEGORY_ORDER:
        score = item["category_scores"].get(category)
        if score is None:
            score_text, score_class = "—/100", "category-unavailable"
        else:
            score_text, score_class = f"{score:.0f}/100", category_class(score)
        category_markup.append(
            f"<div class='category-item'><span class='category-dot {score_class}'>{score_text}</span>"
            f"<span class='category-label'>{html.escape(category)}</span></div>"
        )
    fact_cells = "".join(
        f"<div class='fact-cell'><div class='fact-label'>{html.escape(label)}</div>"
        f"<div class='fact-value'>{html.escape(value)}</div></div>"
        for label, value in fact_fields(item)
    )
    if rank is not None:
        ordinal = "1re" if rank == 1 else f"{rank}e"
        meta = f"{ordinal} place · Profil {html.escape(profile)}"
    elif not item.get("has_scores"):
        meta = "Non classée · notes absentes de l’onglet Scores"
    else:
        meta = f"Non classée pour le profil {html.escape(profile)} · note absente ou invalide"
    return (
        f"<section class='detail' data-id='{scpi_id}' hidden>"
        "<button type='button' class='back' data-back>← Retour au classement</button>"
        "<article class='scpi-card'>"
        f"<div class='card-top'><div><div class='card-name'>{name}</div>"
        f"<div class='card-meta'>{meta}</div></div></div>"
        f"<div class='category-grid'>{''.join(category_markup)}</div>"
        f"<div class='fact-grid'>{fact_cells}</div>"
        "</article></section>"
    )


def build_mobile_select() -> str:
    options = "".join(
        f"<option value='{column}' data-dir='{direction}' data-type='num'>{html.escape(label)}</option>"
        for label, column, direction in MOBILE_SORT_CHOICES
    )
    return f"<div class='mobile-sort'><label for='mobile-sort'>Trier par :</label><select id='mobile-sort'>{options}</select></div>"


def build_toolbar() -> str:
    """Search field, plus the cards / table switch (only visible on narrow screens)."""
    return (
        "<div class='toolbar'>"
        "<div class='view-toggle' role='group' aria-label='Affichage'>"
        f"<button type='button' data-view='cards' aria-pressed='true'>{VIEW_CARDS_LABEL}</button>"
        f"<button type='button' data-view='table' aria-pressed='false'>{VIEW_TABLE_LABEL}</button>"
        "</div>"
        "<div class='search-field'>"
        f"<input id='search' type='search' maxlength='{SEARCH_MAX_LENGTH}' autocomplete='off' "
        "placeholder='🔍 Rechercher une SCPI...' aria-label='Rechercher une SCPI'>"
        "<button type='button' id='search-clear' aria-label='Effacer la recherche' hidden>✕</button>"
        "</div></div>"
    )


def build_ranking_document(
    rows_html: list[str],
    details_html: str,
    *,
    theme: str,
    initial_detail_id: str | None,
) -> str:
    """Assemble the frame document from rows and detail sheets that are already rendered."""
    header_cells = "".join(
        f"<th class='sort-btn' tabindex='0' aria-sort='none' data-type='{sort_type}' data-dir='none'>{label} "
        "<span class='icon' style='opacity: 0.3;'>↕</span></th>"
        for _, label, sort_type in TABLE_HEADERS
    )
    empty_row = (
        f"<tr id='empty-row'{' hidden' if rows_html else ''}>"
        f"<td colspan='{len(TABLE_HEADERS)}' class='empty'>Aucune SCPI ne correspond à votre recherche.</td></tr>"
    )
    initial = html.escape(initial_detail_id or "", quote=True)
    detail_class = " show-detail" if initial_detail_id else ""
    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{FRAME_CSP}\">"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{FRAME_CSS}</style></head>"
        f"<body class='{theme}'>"
        f"<div class='app view-cards{detail_class}' data-initial='{initial}'>"
        "<div class='list'>"
        f"{build_toolbar()}{build_mobile_select()}"
        "<div class='scroll'><table class='ranking-table' id='ranking'>"
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{''.join(rows_html)}{empty_row}</tbody>"
        "</table></div></div>"
        f"{details_html}</div>"
        f"<script>{FRAME_JS}</script>"
        "</body></html>"
    )


def render_ranking_frame(
    rows_html: list[str],
    details_html: str,
    *,
    theme: str,
    initial_detail_id: str | None,
) -> None:
    document = build_ranking_document(
        rows_html,
        details_html,
        theme=theme,
        initial_detail_id=initial_detail_id,
    )
    render_html_frame(document, height=FRAME_INITIAL_HEIGHT_PX, scrolling=True)


# st.cache_resource returns the very same object on every call (no copy of several megabytes
# per rerun, unlike st.cache_data). The cached values are shared by all sessions: read-only.
@st.cache_resource(ttl=SHEET_CACHE_TTL_SECONDS, max_entries=16, show_spinner=False)
def build_profile_view(version: int, profile: str, _data: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """Ranking of one profile, rendered once per data version.

    `version` and `profile` are the cache key; `_data` is not hashed (leading underscore).
    A SCPI without a valid score for this profile comes last and is not numbered.
    """
    rankings = build_rankings(_data)
    for item in rankings:
        item["ranked"] = item["profile_scores"].get(profile) is not None
    rankings.sort(
        key=lambda item: (item["ranked"], item["profile_scores"].get(profile) or 0.0),
        reverse=True,
    )
    rank = 0
    for position, item in enumerate(rankings, start=1):
        if item["ranked"]:
            rank += 1
            item["original_rank"] = rank
        else:
            item["original_rank"] = None
        item["sort_rank"] = position
    return {
        "rows_html": [render_row(item, profile) for item in rankings],
        "ids": frozenset(item["scpi_id"] for item in rankings),
        "details_html": "".join(render_detail(item, profile) for item in rankings),
        "warnings": validate_scores(_data) + validate_join(_data),
    }


@st.cache_resource(ttl=SHEET_CACHE_TTL_SECONDS, max_entries=4, show_spinner=False)
def build_criteria_warnings(
    data_version: int,
    weights_version: int,
    _data: dict[str, list[dict[str, str]]],
    _weight_rows: list[list[str]],
) -> list[str]:
    """Warnings about columns that are almost entirely "NA".

    `data_version` and `weights_version` are the cache key; the underscore arguments are not hashed.
    Without usable weights, the weighted-criteria layer is skipped but every column is still checked.
    """
    matrix: dict[str, Any] = {"profiles": [], "criteria": []}
    if _weight_rows:
        try:
            matrix = build_weight_matrix(_weight_rows)
        except SheetLoadError:
            logger.warning("Weights tab unusable: the missing-data check ignores the weights")
    return validate_missing_criteria(_data, matrix)


# ---- Access gate --------------------------------------------------------------
# Everything below this line is only reached by a visitor who passed the gate.

require_access()


# ---- Data loading -----------------------------------------------------------

load_error = ""
data_version = 0
data: dict[str, list[dict[str, str]]] = {"data": [], "scores": []}
try:
    data_result = DATA_STORE.get()
    data, data_version = data_result.value, data_result.version
    # data_result.stale (older data served after a failed refresh) is logged by the store.
except SheetLoadError as error:
    load_error = str(error)
except Exception:
    logger.exception("Unexpected error while loading the Google Sheet")
    load_error = "Erreur interne lors du chargement des données."

st.markdown(
    """
    <header class="masthead">
        <div class="brand-line"><span class="brand-mark">S</span><span>SCPIScreen</span>
            <span class="brand-divider"></span></div>
        <div class="masthead-body"><div>
            <div class="masthead-title">Classement des SCPI</div>
            <div class="masthead-lede">Selon plusieurs profils d’investissement</div>
        </div></div>
    </header>
    """,
    unsafe_allow_html=True,
)


def render_footer() -> None:
    """Render the license and liability notice."""
    st.markdown(
        "**Licence Open Source (GNU GPL v3) :** Cet outil est distribué sous licence libre GNU GPL v3. "
        "Toute redistribution de ce projet ou de ses œuvres dérivées doit être effectuée sous cette même licence, "
        "conserver l'accès libre au code source et aux formules, et maintenir les crédits d'origine. "
        "Les données publiques de marché utilisées proviennent notamment de [Louve Invest](https://www.louveinvest.com/scpi/liste-scpi).\n\n"
        "**Avertissement de responsabilité :** Cet outil est un support quantitatif d'analyse et de décision fondé sur des données publiques et des modèles mathématiques de pondération pour calculer des scores et établir un classement. "
        "Il ne constitue en aucun cas un conseil en investissement ni une activité de démarchage financier au sens de la réglementation de l'AMF. "
        "Il n'a pas vocation à fournir un conseil personnalisé. Les performances passées ne préjugent pas des performances futures."
    )


def render_method_page() -> None:
    """Render the ranking methodology page from the weights tab."""
    try:
        matrix = build_weight_matrix(WEIGHTS_STORE.get().value)
        page_html = build_method_page_html(matrix)
    except SheetLoadError as error:
        st.error(md_text(f"Les pondérations du Google Sheet ne sont pas accessibles : {error}"))
        return
    except Exception:
        logger.exception("Unexpected error while building the method page")
        st.error("Les pondérations ne peuvent pas être affichées.")
        return
    st.markdown(page_html, unsafe_allow_html=True)


def handle_page_change() -> None:
    """Keep the URL in sync with the page selector and leave any SCPI detail view."""
    selected = st.session_state.get("page_selector")
    if selected is None:
        # Clicking the active page again deselects it: restore it (acts as "back").
        selected = st.session_state.get("current_page", PAGE_RANKING)
        st.session_state["page_selector"] = selected
    st.session_state["current_page"] = selected
    st.query_params.clear()
    if selected == PAGE_METHOD:
        st.query_params["page"] = PAGE_QUERY_METHOD


# The URL is the source of truth, so deep links and the browser back button work.
requested_page = PAGE_METHOD if st.query_params.get("page") == PAGE_QUERY_METHOD else PAGE_RANKING
if st.session_state.get("current_page") != requested_page:
    st.session_state["current_page"] = requested_page
    st.session_state["page_selector"] = requested_page

with st.container(key="page_navigation"):
    st.segmented_control(
        "Page",
        PAGE_OPTIONS,
        key="page_selector",
        label_visibility="collapsed",
        on_change=handle_page_change,
    )

if requested_page == PAGE_METHOD:
    render_method_page()
    render_footer()
    st.stop()

profile = sync_profile_widgets()
profile_column, _unused_column = st.columns([1.7, 0.3])
with profile_column:
    st.markdown('<div class="profile-label">Profil d’investissement</div>', unsafe_allow_html=True)
    st.segmented_control(
        "Profil d’investissement",
        PROFILE_ORDER,
        label_visibility="collapsed",
        key=PROFILE_SEGMENTED_KEY,
        on_change=handle_profile_change,
        args=(PROFILE_SEGMENTED_KEY,),
        width="stretch",
    )
    st.pills(
        "Profil d’investissement (mobile)",
        PROFILE_ORDER,
        label_visibility="collapsed",
        key=PROFILE_PILLS_KEY,
        on_change=handle_profile_change,
        args=(PROFILE_PILLS_KEY,),
        # Streamlit 1.63+: a control placed directly in st.columns does not wrap by default.
        wrap=True,
    )

view: dict[str, Any] = {"rows_html": [], "ids": frozenset(), "details_html": "", "warnings": []}
if not load_error:
    try:
        view = build_profile_view(data_version, profile, data)
    except Exception:
        logger.exception("Unexpected error while building the rankings")
        load_error = "Erreur interne lors de la préparation des données."

if load_error:
    st.error(f"Les données du Google Sheet ne sont pas accessibles : {md_text(load_error)}")
    render_footer()
    st.stop()

criteria_warnings: list[str] = []
weights_version, weight_rows = 0, []
try:
    weights_result = WEIGHTS_STORE.get()
    weights_version, weight_rows = weights_result.version, weights_result.value
except SheetLoadError:
    pass  # An unreachable weights tab is reported by the method page; the check goes on without it.
except Exception:
    logger.exception("Unexpected error while loading the weights for the missing-data check")
try:
    criteria_warnings = build_criteria_warnings(data_version, weights_version, data, weight_rows)
except Exception:
    logger.exception("Unexpected error while checking the data for missing values")

# Missing-data warnings come first: they can distort the scores themselves.
data_warnings = [*criteria_warnings, *view["warnings"]]
for message in data_warnings[:MAX_WARNINGS_DISPLAYED]:
    st.warning(md_text(message))
if len(data_warnings) > MAX_WARNINGS_DISPLAYED:
    remaining_warnings = len(data_warnings) - MAX_WARNINGS_DISPLAYED
    st.warning(md_text(f"{remaining_warnings} autre(s) avertissement(s) dans le Google Sheet."))

# Deep link to a SCPI sheet: applied (and, if unknown, reported) on the first render of a session
# only, so later reruns keep the table and show no repeated error, while the URL stays shareable.
requested_scpi = str(st.query_params.get("scpi", "") or "")
initial_detail_id: str | None = None
if requested_scpi and "deep_link_consumed" not in st.session_state:
    if requested_scpi in view["ids"]:
        initial_detail_id = requested_scpi
    else:
        st.error("Cette fiche SCPI est introuvable dans la source de données.")
st.session_state["deep_link_consumed"] = True

st.markdown(
    f"<div class='section-heading' style='margin-top: 0.5rem; margin-bottom: 0.5rem;'><div><div class='section-kicker'>{html.escape(profile)}</div>"
    "<div class='section-title'>Classement complet</div></div></div>",
    unsafe_allow_html=True,
)

render_ranking_frame(
    view["rows_html"],
    view["details_html"],
    theme=current_theme(),
    initial_detail_id=initial_detail_id,
)

render_footer()
