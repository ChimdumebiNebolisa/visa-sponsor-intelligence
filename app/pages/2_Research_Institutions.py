"""Product A universities and research-institutions explorer."""

from typing import cast

import streamlit as st
from components.decision import (
    explicit_unknowns,
    render_detail_navigation,
    render_rating_reason,
)
from components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import INSTITUTION_SORT_LABELS, InstitutionFilters, InstitutionSort

service = configure_page("Universities and Research Institutions")
facets = service.institution_facets()

st.title("Universities and Research Institutions")
st.caption(
    "Default ordering uses observed sponsorship history. Research Scale and higher-education "
    "context are separate and never change sponsorship ratings."
)

sort_labels = {label: key for key, label in INSTITUTION_SORT_LABELS.items()}
with st.sidebar:
    st.subheader("Institution filters")
    sort_label = st.selectbox("Sort", list(sort_labels))
    search = st.text_input("Institution, legal employer, or parent name")
    controls = st.multiselect("Public/private control", facets.get("controls", []))
    states = st.multiselect("State", facets.get("states", []))
    everify = st.multiselect("E-Verify status (supplemental)", facets.get("everify_statuses", []))
    minimum_lca = st.number_input("Minimum certified technical H-1B LCA", min_value=0, value=0)
    minimum_perm = st.number_input("Minimum certified technical PERM", min_value=0, value=0)
    minimum_year = st.selectbox("Latest observed year", ["Any", *range(2022, 2101)])
    minimum_overall = st.selectbox("Minimum Overall Sponsorship stars", ["Any", 1, 2, 3, 4, 5])
    minimum_green_card = st.selectbox(
        "Minimum Green Card Sponsorship History stars", ["Any", 1, 2, 3, 4, 5]
    )
    minimum_h1b = st.selectbox("Minimum H-1B History stars", ["Any", 1, 2, 3, 4, 5])

filters = InstitutionFilters(
    search=search,
    controls=tuple(controls),
    states=tuple(states),
    everify_statuses=tuple(everify),
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
    minimum_h1b_stars=None if minimum_h1b == "Any" else int(minimum_h1b),
    minimum_green_card_stars=(None if minimum_green_card == "Any" else int(minimum_green_card)),
    minimum_overall_stars=None if minimum_overall == "Any" else int(minimum_overall),
    minimum_last_activity_year=None if minimum_year == "Any" else int(minimum_year),
    sort_by=cast(InstitutionSort, sort_labels[sort_label]),
)
institutions = service.list_institutions(filters, limit=500)

if institutions.is_empty():
    st.info("No institution evidence profiles match these filters.")
else:
    st.write(f"Showing {institutions.height:,} matching institutions (maximum 500 on screen).")
    primary_columns = [
        "official_name",
        "legal_employer_name",
        "parent_organization_name",
        "control",
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
        "higher_education_context",
        "research_scale_stars",
        "research_scale_star_label",
        "overall_sponsorship_coverage",
    ]
    st.dataframe(
        explicit_unknowns(institutions.select(primary_columns)).to_arrow(),
        width="stretch",
        hide_index=True,
        height=620,
    )
    explanation_labels = {
        f"{row['official_name']} · {row['institution_id']}": row for row in institutions.to_dicts()
    }
    selected_label = st.selectbox("Explain an institution rating", list(explanation_labels))
    selected = explanation_labels[selected_label]
    render_rating_reason(selected, "overall_sponsorship", "Overall Sponsorship")
    render_rating_reason(selected, "research_scale", "Research Scale (separate context)")
    render_detail_navigation(
        institutions,
        label_column="official_name",
        key="institution-organization-detail",
    )

st.info(
    "Higher-education institution; exact cap-exempt status requires verification. Research "
    "spending, E-Verify, OPT evidence, and institution type do not establish sponsorship policy."
)
render_evidence_notice()

with st.expander("Export the full filtered result"):
    prepare = st.checkbox("Prepare institution CSV and Parquet downloads")
    if prepare:
        csv_data = service.export_institutions(filters, "csv")
        parquet_data = service.export_institutions(filters, "parquet")
        left, right = st.columns(2)
        left.download_button(
            "Download CSV", csv_data, file_name="institutions-product-a.csv", mime="text/csv"
        )
        right.download_button(
            "Download Parquet",
            parquet_data,
            file_name="institutions-product-a.parquet",
            mime="application/vnd.apache.parquet",
        )
