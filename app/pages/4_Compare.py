"""Product A comparison for up to five organizations."""

from __future__ import annotations

from typing import Literal

import polars as pl
import streamlit as st
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Compare Organizations")

st.title("Compare Organizations")
st.caption(
    "Compare up to five legal entities or separately labeled parent rollups across sponsorship "
    "ratings, annual/raw evidence, job families, supplemental context, and coverage."
)

search = st.text_input("Find organizations", placeholder="Search a parent, legal name, or alias")
candidates = service.search_organizations(search, limit=100)
if candidates.is_empty():
    st.info("No organizations match this search.")
    st.stop()

labels = {
    f"{row['organization_name']} · {row['identity_scope']} · "
    f"{row['state'] or 'state unknown'} · {row['organization_id']}": row["organization_id"]
    for row in candidates.to_dicts()
}
selected_labels = st.multiselect(
    "Organizations", list(labels), max_selections=5, placeholder="Choose one to five organizations"
)
if not selected_labels:
    st.info("Choose at least one organization to build the comparison.")
    st.stop()

comparison = service.compare_organizations(tuple(labels[label] for label in selected_labels))
records = comparison.to_dicts()


def display(value: object, kind: Literal["text", "count", "money", "coverage"]) -> str:
    """Format values without disguising missing evidence."""

    if value is None:
        return "UNKNOWN"
    if kind == "text":
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "None observed"
        return str(value)
    numeric_value = float(value) if isinstance(value, (int, float, str)) else 0.0
    if kind == "count":
        return f"{int(numeric_value):,}"
    if kind == "money":
        return f"${int(numeric_value):,}"
    return f"{numeric_value:.0%}"


def comparison_table(
    metrics: list[tuple[str, str, Literal["text", "count", "money", "coverage"]]],
) -> list[dict[str, str]]:
    """Transpose organization records into a compact side-by-side table."""

    columns = [f"{row['organization_name']} · {row['organization_id']}" for row in records]
    rows: list[dict[str, str]] = []
    for label, field, kind in metrics:
        row = {"Metric": label}
        row.update(
            {
                column: display(record.get(field), kind)
                for column, record in zip(columns, records, strict=True)
            }
        )
        rows.append(row)
    return rows


st.subheader("Sponsorship ratings")
st.dataframe(
    comparison_table(
        [
            ("Overall Sponsorship", "overall_sponsorship_stars", "text"),
            ("Accessible Overall label", "overall_sponsorship_star_label", "text"),
            ("Overall coverage", "overall_sponsorship_coverage", "coverage"),
            ("H-1B History", "h1b_history_stars", "text"),
            ("Accessible H-1B label", "h1b_history_star_label", "text"),
            ("H-1B coverage", "h1b_history_coverage", "coverage"),
            ("Green Card Sponsorship History", "green_card_history_stars", "text"),
            ("Accessible PERM label", "green_card_history_star_label", "text"),
            ("PERM coverage", "green_card_history_coverage", "coverage"),
            ("Score version", "score_version", "text"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

st.subheader("Observed evidence and normalized families")
st.dataframe(
    comparison_table(
        [
            ("Identity scope", "identity_scope", "text"),
            ("Organization type", "organization_type", "text"),
            ("State", "state", "text"),
            ("Certified technical H-1B LCA", "relevant_lca_count", "count"),
            (
                "Certified-withdrawn technical H-1B LCA",
                "relevant_certified_withdrawn_lca_count",
                "count",
            ),
            ("H-1B technical job families", "lca_role_families", "text"),
            ("Employer-level H-1B initial approvals", "initial_approvals", "count"),
            ("Certified technical PERM", "relevant_certified_perm_count", "count"),
            ("Certified-expired technical PERM", "relevant_certified_expired_perm_count", "count"),
            ("PERM technical job families", "perm_role_families", "text"),
            ("Latest observed year", "last_observed_activity_year", "count"),
            ("Partial-period evidence", "has_partial_period", "text"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

st.subheader("Supplemental institution and operational context")
st.dataframe(
    comparison_table(
        [
            ("E-Verify", "everify_status", "text"),
            ("Positive-only OPT observation", "known_opt_observation", "text"),
            ("Research institution", "research_institution", "text"),
            ("Higher-education context", "higher_education_context", "text"),
            ("Research Scale", "research_scale_stars", "text"),
            ("Accessible Research Scale label", "research_scale_star_label", "text"),
            ("Latest HERD year", "latest_herd_year", "count"),
            ("Computing R&D", "computing_rd", "money"),
            ("Engineering R&D", "engineering_rd", "money"),
            ("Total R&D", "total_rd", "money"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

with st.expander("Why these ratings and what they do not prove"):
    st.dataframe(
        comparison_table(
            [
                ("Overall — why this rating", "overall_sponsorship_explanation", "text"),
                ("H-1B — why this rating", "h1b_history_explanation", "text"),
                ("PERM — why this rating", "green_card_history_explanation", "text"),
                ("Research Scale explanation", "research_scale_explanation", "text"),
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.write(
        "These records do not prove that a current opening sponsors, that an LCA is an approved "
        "petition, that PERM certification is a green card, or that a university position is "
        "cap-exempt."
    )

st.subheader("Annual and raw evidence")
annual_frames: list[pl.DataFrame] = []
title_frames: list[pl.DataFrame] = []
for record in records:
    detail = service.get_organization_detail(str(record["organization_id"]))
    if detail is None:
        continue
    name = str(record["organization_name"])
    if not detail.h1b_trends.is_empty():
        annual_frames.append(
            detail.h1b_trends.with_columns(
                pl.lit(name).alias("organization_name"), pl.lit("H-1B / USCIS").alias("program")
            )
        )
    if not detail.perm_trends.is_empty():
        annual_frames.append(
            detail.perm_trends.with_columns(
                pl.lit(name).alias("organization_name"), pl.lit("PERM").alias("program")
            )
        )
    if not detail.relevant_titles.is_empty():
        title_frames.append(
            detail.relevant_titles.with_columns(pl.lit(name).alias("organization_name"))
        )
if annual_frames:
    st.dataframe(
        pl.concat(annual_frames, how="diagonal_relaxed").to_arrow(),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No annual evidence rows are available for the selected organizations.")
if title_frames:
    st.dataframe(
        pl.concat(title_frames, how="diagonal_relaxed").to_arrow(), width="stretch", hide_index=True
    )
else:
    st.info("No relevant raw-title rows are available for the selected organizations.")

render_evidence_notice()
