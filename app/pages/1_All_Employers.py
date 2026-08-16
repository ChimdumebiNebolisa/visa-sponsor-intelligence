"""Filter, rank, inspect, and export Product A employer evidence."""

from typing import cast

import streamlit as st
from components.decision import explicit_unknowns, render_detail_navigation
from components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import EMPLOYER_SORT_LABELS, EmployerFilters, EmployerSort

service = configure_page("All Employers")
facets = service.employer_facets()

st.title("All Employers")
st.caption(
    "Default order is Overall Sponsorship, Green Card Sponsorship History, H-1B History, latest "
    "observed year, then employer name. Ratings are displayed as whole stars, not probabilities."
)

sort_labels = {label: key for key, label in EMPLOYER_SORT_LABELS.items()}
with st.sidebar:
    st.subheader("Employer filters")
    sort_label = st.selectbox("Sort", list(sort_labels))
    search = st.text_input("Employer name or alias")
    organization_types = st.multiselect("Organization type", facets.get("organization_types", []))
    states = st.multiselect("State", facets.get("states", []))
    role_family = st.selectbox("Technical job family", ["", *facets.get("role_families", [])])
    everify = st.multiselect("E-Verify status (supplemental)", facets.get("everify_statuses", []))
    minimum_lca = st.number_input("Minimum certified technical H-1B LCA", min_value=0, value=0)
    minimum_perm = st.number_input("Minimum certified technical PERM", min_value=0, value=0)
    minimum_approvals = st.number_input(
        "Minimum employer-level H-1B initial approvals", min_value=0, value=0
    )
    minimum_year = st.selectbox("Latest observed year", ["Any", *range(2022, 2101)])
    minimum_overall = st.selectbox("Minimum Overall Sponsorship stars", ["Any", 1, 2, 3, 4, 5])
    minimum_green_card = st.selectbox(
        "Minimum Green Card Sponsorship History stars", ["Any", 1, 2, 3, 4, 5]
    )
    minimum_h1b = st.selectbox("Minimum H-1B History stars", ["Any", 1, 2, 3, 4, 5])
    exclude_staffing = st.checkbox("Exclude known staffing/consulting organizations")

filters = EmployerFilters(
    search=search,
    organization_types=tuple(organization_types),
    states=tuple(states),
    everify_statuses=tuple(everify),
    role_family=role_family or None,
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
    minimum_initial_approvals=int(minimum_approvals),
    minimum_h1b_stars=None if minimum_h1b == "Any" else int(minimum_h1b),
    minimum_green_card_stars=(None if minimum_green_card == "Any" else int(minimum_green_card)),
    minimum_overall_stars=None if minimum_overall == "Any" else int(minimum_overall),
    minimum_last_activity_year=None if minimum_year == "Any" else int(minimum_year),
    exclude_known_staffing_consulting=exclude_staffing,
    sort_by=cast(EmployerSort, sort_labels[sort_label]),
)
employers = service.list_employers(filters, limit=500)

if employers.is_empty():
    st.info("No employer evidence matches these filters. Missing evidence was not treated as NO.")
else:
    st.write(f"Showing {employers.height:,} matching rows (maximum 500 on screen).")
    primary_columns = [
        "organization_name",
        "identity_scope",
        "overall_sponsorship_stars",
        "overall_sponsorship_star_label",
        "green_card_history_stars",
        "green_card_history_star_label",
        "h1b_history_stars",
        "h1b_history_star_label",
        "relevant_certified_perm_count",
        "relevant_lca_count",
        "initial_approvals",
        "last_observed_activity_year",
        "everify_status",
        "organization_type",
        "state",
    ]
    st.dataframe(
        explicit_unknowns(employers.select(primary_columns)).to_arrow(),
        width="stretch",
        hide_index=True,
        height=620,
    )
    st.caption(
        "Initial approvals are employer-level USCIS counts and are not specific to technical roles."
    )
    render_detail_navigation(
        employers,
        label_column="organization_name",
        key="employer-organization-detail",
    )

st.info(
    "A certified LCA is not an approved H-1B petition. Observed employer-sponsored PERM history "
    "is not a green-card approval or sponsorship promise. E-Verify never changes the ratings."
)
render_evidence_notice()

with st.expander("Export the full filtered result"):
    prepare = st.checkbox("Prepare CSV and Parquet downloads")
    if prepare:
        csv_data = service.export_employers(filters, "csv")
        parquet_data = service.export_employers(filters, "parquet")
        left, right = st.columns(2)
        left.download_button(
            "Download CSV", csv_data, file_name="employers-product-a.csv", mime="text/csv"
        )
        right.download_button(
            "Download Parquet",
            parquet_data,
            file_name="employers-product-a.parquet",
            mime="application/vnd.apache.parquet",
        )
