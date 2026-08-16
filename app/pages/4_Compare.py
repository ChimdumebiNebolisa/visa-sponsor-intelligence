"""Evidence-first comparison for up to five organizations."""

from __future__ import annotations

from typing import Literal

import streamlit as st
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Compare Organizations")

st.title("Compare Organizations")
st.caption(
    "Compare raw immigration, research, and reviewed-policy evidence beside nullable "
    "evidence-strength scores. Select up to five organizations."
)

search = st.text_input("Find organizations", placeholder="Search a parent, legal name, or alias")
candidates = service.search_organizations(search, limit=100)
if candidates.is_empty():
    st.info("No organizations match this search.")
    st.stop()

labels = {
    f"{row['organization_name']} · {row['state'] or 'state unknown'} · {row['organization_id']}": (
        row["organization_id"]
    )
    for row in candidates.to_dicts()
}
selected_labels = st.multiselect(
    "Organizations",
    list(labels),
    max_selections=5,
    placeholder="Choose one to five organizations",
)
if not selected_labels:
    st.info("Choose at least one organization to build the comparison.")
    st.stop()

comparison = service.compare_organizations(tuple(labels[label] for label in selected_labels))
records = comparison.to_dicts()


def display(value: object, kind: Literal["text", "count", "money", "score", "coverage"]) -> str:
    """Format values without disguising missing evidence."""

    if value is None:
        return "UNKNOWN"
    if kind == "text":
        return str(value)
    numeric_value = float(value) if isinstance(value, (int, float, str)) else 0.0
    if kind == "count":
        return f"{int(numeric_value):,}"
    if kind == "money":
        return f"${int(numeric_value):,}"
    if kind == "score":
        return f"{numeric_value:.1f}"
    if kind == "coverage":
        return f"{numeric_value:.0%}"
    return str(value)


def comparison_table(
    metrics: list[tuple[str, str, Literal["text", "count", "money", "score", "coverage"]]],
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


st.subheader("Observed evidence")
st.dataframe(
    comparison_table(
        [
            ("Organization type", "organization_type", "text"),
            ("State", "state", "text"),
            ("E-Verify", "everify_status", "text"),
            ("Positive OPT observation", "known_opt_observation", "text"),
            ("OPT report year", "opt_report_year", "count"),
            ("OPT reported count", "opt_reported_count", "count"),
            ("All LCA records", "lca_case_count", "count"),
            ("Relevant LCA records", "relevant_lca_count", "count"),
            ("LCA active years", "lca_active_years", "count"),
            ("USCIS initial approvals", "initial_approvals", "count"),
            ("USCIS initial denials", "initial_denials", "count"),
            ("USCIS active years", "uscis_active_years", "count"),
            ("All PERM records", "perm_case_count", "count"),
            ("Relevant certified PERM", "relevant_certified_perm_count", "count"),
            ("PERM active years", "perm_active_years", "count"),
            ("Top repeated technical PERM title", "top_perm_technical_title", "text"),
            ("Top technical PERM title count", "top_perm_technical_title_count", "count"),
            ("Cap-exemption status", "cap_exemption_status", "text"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

st.subheader("Research and reviewed policy evidence")
st.dataframe(
    comparison_table(
        [
            ("Research institution", "research_institution", "text"),
            ("Decision-readiness tier", "decision_readiness_tier", "text"),
            ("Total R&D", "total_rd", "money"),
            ("Computing R&D", "computing_rd", "money"),
            ("Engineering R&D", "engineering_rd", "money"),
            ("Federal R&D", "federal_rd", "money"),
            ("Research-staff H-1B policy", "research_staff_h1b_policy", "text"),
            (
                "Research-staff permanent-residence policy",
                "research_staff_permanent_residence_policy",
                "text",
            ),
            ("PERM policy support", "perm_support", "text"),
            ("EB-1B policy support", "eb1b_support", "text"),
            ("Policy review status", "policy_review_status", "text"),
            ("Core-policy review coverage", "core_policy_review_coverage", "coverage"),
            ("Core-policy evidence coverage", "core_policy_evidence_coverage", "coverage"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

st.subheader("Evidence-strength scores")
st.dataframe(
    comparison_table(
        [
            ("Sponsorship history score", "sponsorship_history_score", "score"),
            ("Sponsorship history status", "sponsorship_history_status", "text"),
            ("Sponsorship history coverage", "sponsorship_history_coverage", "coverage"),
            ("Sponsorship history grade", "sponsorship_history_grade", "text"),
            ("STEM OPT readiness score", "stem_opt_readiness_score", "score"),
            ("STEM OPT readiness status", "stem_opt_readiness_status", "text"),
            ("STEM OPT readiness coverage", "stem_opt_readiness_coverage", "coverage"),
            ("H-1B history score", "h1b_history_score", "score"),
            ("H-1B history grade", "h1b_history_grade", "text"),
            ("H-1B history coverage", "h1b_history_coverage", "coverage"),
            ("Green-card history score", "green_card_history_score", "score"),
            ("Green-card history grade", "green_card_history_grade", "text"),
            ("Green-card history coverage", "green_card_history_coverage", "coverage"),
            ("Immigration evidence score", "immigration_evidence_score", "score"),
            ("Immigration evidence grade", "immigration_evidence_grade", "text"),
            ("Immigration evidence coverage", "immigration_evidence_coverage", "coverage"),
            ("Immigration evidence confidence", "immigration_evidence_confidence", "coverage"),
            ("Research strength score", "research_strength_score", "score"),
            ("Research strength grade", "research_strength_grade", "text"),
            ("Research strength coverage", "research_strength_coverage", "coverage"),
            ("Policy support score", "policy_support_score", "score"),
            ("Policy support grade", "policy_support_grade", "text"),
            ("Policy support coverage", "policy_support_coverage", "coverage"),
            ("Research pathway score", "research_pathway_score", "score"),
            ("Research pathway status", "research_pathway_status", "text"),
            ("Research pathway grade", "research_pathway_grade", "text"),
            ("Research pathway coverage", "research_pathway_coverage", "coverage"),
            ("Score version", "score_version", "text"),
        ]
    ),
    width="stretch",
    hide_index=True,
)

with st.expander("Score explanations"):
    st.dataframe(
        comparison_table(
            [
                ("Decision readiness", "decision_readiness_explanation", "text"),
                ("Sponsorship history", "sponsorship_history_explanation", "text"),
                ("H-1B history", "h1b_history_explanation", "text"),
                ("Green-card history", "green_card_history_explanation", "text"),
                ("Immigration evidence", "immigration_evidence_explanation", "text"),
                ("Research strength", "research_strength_explanation", "text"),
                ("Policy support", "policy_support_explanation", "text"),
                ("Research pathway", "research_pathway_explanation", "text"),
            ]
        ),
        width="stretch",
        hide_index=True,
    )

st.warning(
    "Scores summarize historical evidence strength and coverage. They are not legal assessments "
    "or probabilities that an organization will sponsor a person or role."
)
render_evidence_notice()
