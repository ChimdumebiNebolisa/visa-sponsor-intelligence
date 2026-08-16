"""Filter, rank, inspect, and export employer evidence."""

from typing import cast

import streamlit as st
from components.decision import explicit_unknowns, render_detail_navigation
from components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import EMPLOYER_SORT_LABELS, EmployerFilters, EmployerSort

service = configure_page("All Employers")
facets = service.employer_facets()

st.title("All Employers")
st.caption(
    "Default ordering uses H-1B and green-card history independently of E-Verify. Partial "
    "scores stay below fully covered scores; UNKNOWN is never converted to zero."
)

sort_labels = {label: key for key, label in EMPLOYER_SORT_LABELS.items()}
with st.sidebar:
    st.subheader("Employer filters")
    sort_label = st.selectbox("Sort", list(sort_labels))
    search = st.text_input("Employer name or alias")
    organization_types = st.multiselect("Organization type", facets.get("organization_types", []))
    states = st.multiselect("State", facets.get("states", []))
    role_family = st.selectbox("Technical role family", ["", *facets.get("role_families", [])])
    everify = st.multiselect(
        "E-Verify status (separate STEM OPT signal)", facets.get("everify_statuses", [])
    )
    opt = st.multiselect("Known positive OPT evidence", facets.get("opt_statuses", []))
    cap = st.multiselect("Cap-exemption status", facets.get("cap_exemption_statuses", []))
    confidence = st.multiselect("Score confidence", facets.get("evidence_confidences", []))
    minimum_lca = st.number_input("Minimum relevant LCA records", min_value=0, value=0)
    minimum_perm = st.number_input("Minimum relevant certified PERM", min_value=0, value=0)
    minimum_approvals = st.number_input("Minimum USCIS initial approvals", min_value=0, value=0)
    minimum_year = st.selectbox("Last observed activity year", ["Any", *range(2022, 2101)])
    use_minimum_scores = st.checkbox("Apply minimum evidence scores")
    minimum_h1b = st.number_input("Minimum H-1B score", 0.0, 100.0, 0.0)
    minimum_green_card = st.number_input("Minimum green-card score", 0.0, 100.0, 0.0)
    minimum_sponsorship = st.number_input("Minimum sponsorship score", 0.0, 100.0, 0.0)
    exclude_staffing = st.checkbox("Exclude known staffing/consulting organizations")

filters = EmployerFilters(
    search=search,
    organization_types=tuple(organization_types),
    states=tuple(states),
    everify_statuses=tuple(everify),
    opt_statuses=tuple(opt),
    cap_exemption_statuses=tuple(cap),
    evidence_confidences=tuple(confidence),
    role_family=role_family or None,
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
    minimum_initial_approvals=int(minimum_approvals),
    minimum_h1b_score=float(minimum_h1b) if use_minimum_scores else None,
    minimum_green_card_score=float(minimum_green_card) if use_minimum_scores else None,
    minimum_sponsorship_score=float(minimum_sponsorship) if use_minimum_scores else None,
    minimum_last_activity_year=None if minimum_year == "Any" else int(minimum_year),
    exclude_known_staffing_consulting=exclude_staffing,
    sort_by=cast(EmployerSort, sort_labels[sort_label]),
)
employers = service.list_employers(filters, limit=500)

if employers.is_empty():
    st.info("No employer metrics match these filters. Missing evidence was not treated as NO.")
else:
    st.write(f"Showing {employers.height:,} matching rows (maximum 500 on screen).")
    decision_columns = [
        "organization_name",
        "sponsorship_history_score",
        "sponsorship_history_coverage",
        "sponsorship_history_status",
        "green_card_history_score",
        "h1b_history_score",
        "stem_opt_readiness_score",
        "everify_status",
        "known_opt_observation",
        "relevant_certified_perm_count",
        "relevant_lca_count",
        "initial_approvals",
        "last_perm_activity_year",
        "last_lca_activity_year",
        "organization_type",
        "state",
    ]
    st.dataframe(
        explicit_unknowns(employers.select(decision_columns)).to_arrow(),
        width="stretch",
        hide_index=True,
        height=620,
    )
    render_detail_navigation(
        employers,
        label_column="organization_name",
        key="employer-organization-detail",
    )

st.info(
    "A certified LCA is not an approved H-1B petition. Historical PERM activity is not a "
    "promise. E-Verify and OPT remain separate operational signals and do not gate the "
    "sponsorship-history score."
)
render_evidence_notice()

with st.expander("Export the full filtered result"):
    prepare = st.checkbox("Prepare CSV and Parquet downloads")
    if prepare:
        csv_data = service.export_employers(filters, "csv")
        parquet_data = service.export_employers(filters, "parquet")
        left, right = st.columns(2)
        left.download_button(
            "Download CSV",
            csv_data,
            file_name="employer_metrics_filtered.csv",
            mime="text/csv",
        )
        right.download_button(
            "Download Parquet",
            parquet_data,
            file_name="employer_metrics_filtered.parquet",
            mime="application/vnd.apache.parquet",
        )
