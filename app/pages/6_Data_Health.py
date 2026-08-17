"""Product A source selection, provenance, coverage, and quality health."""

import polars as pl
import streamlit as st
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Data Health")
status = service.get_status()
health = service.get_data_health()

st.title("Data Health")
st.caption(
    "Selected artifacts, official URLs, checksums, periods, complete/partial state, row counts, "
    "schema versions, identity/classifier/rating coverage, build ID, and score version."
)

checks = health.quality_checks
if checks.is_empty():
    st.warning(
        "No persisted quality report is in this database. Run `sponsor-intel quality report` "
        "and rebuild the presentation database before publication."
    )
else:
    failures = checks.filter(pl.col("status") == "FAIL")
    warnings = checks.filter(pl.col("status") == "WARN")
    build_ids = checks["build_id"].drop_nulls().unique().to_list()
    metrics = st.columns(5)
    metrics[0].metric("Publication gate", "PASS" if failures.is_empty() else "FAIL")
    metrics[1].metric("Critical failures", f"{failures.filter(pl.col('critical')).height:,}")
    metrics[2].metric("Warnings", f"{warnings.height:,}")
    metrics[3].metric("Build ID", build_ids[0] if len(build_ids) == 1 else status.build_id)
    metrics[4].metric("Score version", status.score_version or "UNKNOWN")

st.subheader("Selected source artifacts")
if health.source_artifacts.is_empty():
    st.warning(
        "Artifact-level provenance is unavailable in this database build. Source-level health "
        "is shown below, but official URLs and checksums cannot be audited here."
    )
else:
    st.dataframe(health.source_artifacts.to_arrow(), width="stretch", hide_index=True)

st.subheader("Source coverage, freshness, and row counts")
st.dataframe(health.source_coverage.to_arrow(), width="stretch", hide_index=True)
if status.current_partial_fiscal_year is not None:
    period = f"FY{status.current_partial_fiscal_year}"
    if status.current_partial_quarter is not None:
        period += f" Q{status.current_partial_quarter}"
    st.warning(f"{period} is partial and must not be compared directly with a complete year.")

st.subheader("Quality and publication checks")
if checks.is_empty():
    st.info("Quality checks are unavailable in this database build.")
else:
    selected_statuses = st.multiselect(
        "Status",
        checks["status"].unique().sort().to_list(),
        default=checks["status"].unique().sort().to_list(),
    )
    visible = checks.filter(pl.col("status").is_in(selected_statuses))
    st.dataframe(visible.to_arrow(), width="stretch", hide_index=True, height=520)

st.info(
    "Product A publication gates cover source selection, schema/provenance, duplicate handling, "
    "entity and role coverage, rating contracts, independence, partial-period semantics, "
    "nonzero outputs, and freshness. Policy completeness is not a gate."
)
render_evidence_notice()
