"""Shared Streamlit helpers for Product A explorer pages."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from sponsor_intel.config import DeploymentMode, load_settings
from sponsor_intel.deployment import ReleaseBootstrapError, bootstrap_release
from sponsor_intel.services import DuckDBExplorerService, ExplorerService, get_explorer_service


@st.cache_resource
def explorer_service() -> ExplorerService:
    """Reuse one read-only presentation-database connection across Streamlit reruns."""

    settings = load_settings()
    if settings.deployment_mode is DeploymentMode.RELEASE:
        runtime = bootstrap_release(settings)
        return DuckDBExplorerService(
            runtime.database_path,
            release_tag=runtime.release_tag,
            build_id=runtime.build_id,
            build_date=runtime.generated_at,
        )
    return get_explorer_service(database_path=settings.db_path)


def configure_page(title: str) -> ExplorerService:
    """Apply consistent framing and stop honestly when data is unavailable."""

    st.set_page_config(
        page_title=f"{title} · Sponsorship Intelligence Explorer",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    try:
        service = explorer_service()
    except ValidationError:
        st.title(title)
        st.error("Unable to load a verified quality-approved data release.")
        st.caption(
            "Deployment configuration is incomplete or invalid. Verify the private app secrets."
        )
        st.stop()
    except ReleaseBootstrapError as error:
        st.title(title)
        st.error("Unable to load a verified quality-approved data release.")
        st.caption(str(error))
        st.stop()
    status = service.get_status()
    with st.sidebar:
        st.header("Evidence build")
        st.write(status.phase)
        st.caption(f"Build: {status.build_id}")
        if status.score_version:
            st.caption(f"Ratings: {status.score_version}")
        if status.release_tag:
            st.caption(f"Release: {status.release_tag}")
        if status.build_date:
            st.caption(f"Built: {status.build_date}")
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
    st.warning(status.disclaimer)
    return service


def render_evidence_notice() -> None:
    """Keep evidence meaning and product limitations visible."""

    st.caption(
        "Evidence classes: source observations come from official records; derived metrics are "
        "calculated from those records. Missing evidence is UNKNOWN/Unrated, never an unsupported "
        "negative conclusion."
    )
