"""Shared Streamlit helpers for evidence-first explorer pages."""

from __future__ import annotations

import streamlit as st

from sponsor_intel.services import ExplorerService, get_explorer_service


@st.cache_resource
def explorer_service() -> ExplorerService:
    """Reuse one read-only DuckDB connection across Streamlit reruns."""

    return get_explorer_service()


def configure_page(title: str) -> ExplorerService:
    """Apply consistent page framing and stop honestly when data is unavailable."""

    st.set_page_config(
        page_title=f"{title} · Sponsorship Intelligence Explorer",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    service = explorer_service()
    status = service.get_status()
    with st.sidebar:
        st.header("Evidence build")
        st.write(status.phase)
        st.caption(f"Build: {status.build_id}")
        if status.latest_complete_fiscal_year is not None:
            st.caption(f"Latest complete FY: {status.latest_complete_fiscal_year}")
        if status.current_partial_fiscal_year is not None:
            period = f"FY{status.current_partial_fiscal_year}"
            if status.current_partial_quarter is not None:
                period += f" Q{status.current_partial_quarter}"
            st.warning(f"{period} is partial.")
    if not status.data_available:
        st.title(title)
        st.info(status.message)
        st.warning(status.disclaimer)
        st.stop()
    return service


def render_evidence_notice() -> None:
    """Keep evidence meaning and product limitations visible."""

    st.caption(
        "Evidence classes: OBSERVED_GOVERNMENT_RECORD is a source observation; "
        "DERIVED_METRIC is calculated from those records. UNKNOWN is not NO."
    )
