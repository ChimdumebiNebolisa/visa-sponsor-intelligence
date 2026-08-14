"""Shared renderer for the Phase 0 Streamlit shell."""

from __future__ import annotations

import streamlit as st

from sponsor_intel.services import get_explorer_service


def render_foundation_page(title: str, description: str) -> None:
    """Render a page without bypassing the explorer service boundary."""

    st.set_page_config(
        page_title=f"{title} · Sponsorship Intelligence Explorer",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    status = get_explorer_service().get_status()

    with st.sidebar:
        st.header("Build status")
        st.write(status.phase)
        st.caption(f"Build ID: {status.build_id}")

    st.title(title)
    st.caption(description)
    st.info(status.message)

    first, second, third = st.columns(3)
    first.metric("Evidence status", status.evidence_status)
    second.metric("Source data", "Available" if status.data_available else "Not loaded")
    third.metric("Current phase", status.phase)

    st.warning(status.disclaimer)
