"""Single entry point to embed an HTML document in an iframe.

`st.components.v1.html` is deprecated in recent Streamlit versions and will be removed.
Every page of the app goes through render_html_frame(), so that the day it disappears only
this function has to change: it falls back to `st.iframe` when `components.html` is missing
or fails.

The frames of this app need scripts AND the same origin as the page (the cookie setter
writes the page cookie, the ranking frame resizes itself). If you ever see the session
cookie not being written, or the ranking frame keeping a fixed height, check the sandbox
attributes of the iframe produced by the Streamlit version in use.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger("scpiscreen")


def render_html_frame(document: str, *, height: int, scrolling: bool = True) -> None:
    """Render a complete HTML document in an iframe of the given initial height (pixels)."""
    try:
        import streamlit.components.v1 as components

        components.html(document, height=height, scrolling=scrolling)
        return
    except Exception:
        logger.warning("components.html is unavailable: falling back to st.iframe", exc_info=True)
    st.iframe(document, height=max(height, 1))
