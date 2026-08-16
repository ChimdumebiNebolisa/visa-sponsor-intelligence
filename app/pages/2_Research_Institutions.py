"""Decision-ready research-institution ranking and evidence filters."""

from typing import cast

import streamlit as st
from components.decision import (
    explicit_unknowns,
    rank_explanation,
    render_detail_navigation,
    unknown_explanation,
)
from components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import INSTITUTION_SORT_LABELS, InstitutionFilters, InstitutionSort

service = configure_page("Research Institutions")
facets = service.institution_facets()

st.title("Research Institutions")
st.caption(
    "Evidence-readiness tier, technical H-1B and green-card history, reviewed research-staff "
    "policy, and research strength are ranked separately and explained."
)

sort_labels = {label: key for key, label in INSTITUTION_SORT_LABELS.items()}
with st.sidebar:
    st.subheader("Institution filters")
    sort_label = st.selectbox("Sort", list(sort_labels))
    search = st.text_input("Institution or system name")
    tiers = st.multiselect("Decision-readiness tier", facets.get("decision_readiness_tiers", []))
    controls = st.multiselect("Public/private control", facets.get("controls", []))
    states = st.multiselect("State", facets.get("states", []))
    h1b_policy = st.multiselect(
        "Research-staff H-1B policy", facets.get("research_staff_h1b_policies", [])
    )
    pr_policy = st.multiselect(
        "Research-staff permanent-residence policy",
        facets.get("research_staff_pr_policies", []),
    )
    perm_policy = st.multiselect("PERM policy support", facets.get("perm_support_policies", []))
    eb1b_policy = st.multiselect("EB-1B policy support", facets.get("eb1b_support_policies", []))
    confidence = st.multiselect("Score confidence", facets.get("score_confidences", []))
    cap = st.multiselect(
        "Potential/verified cap exemption", facets.get("cap_exemption_statuses", [])
    )
    everify = st.multiselect(
        "E-Verify (separate STEM OPT signal)", facets.get("everify_statuses", [])
    )
    require_policy_coverage = st.checkbox("Apply minimum core-policy review coverage")
    policy_coverage = st.slider("Core-policy review coverage", 0.0, 1.0, 0.0, 0.25)
    use_minimum_scores = st.checkbox("Apply minimum evidence scores")
    minimum_h1b = st.number_input("Minimum H-1B score", 0.0, 100.0, 0.0)
    minimum_green_card = st.number_input("Minimum green-card score", 0.0, 100.0, 0.0)
    minimum_sponsorship = st.number_input("Minimum sponsorship score", 0.0, 100.0, 0.0)
    minimum_pathway = st.number_input("Minimum research-pathway score", 0.0, 100.0, 0.0)
    minimum_lca = st.number_input("Minimum relevant technical LCA", min_value=0, value=0)
    minimum_perm = st.number_input(
        "Minimum relevant certified technical PERM", min_value=0, value=0
    )
    minimum_computing_rd = st.number_input(
        "Minimum computing R&D ($)", min_value=0, value=0, step=100_000
    )
    minimum_engineering_rd = st.number_input(
        "Minimum engineering R&D ($)", min_value=0, value=0, step=100_000
    )

filters = InstitutionFilters(
    search=search,
    controls=tuple(controls),
    states=tuple(states),
    decision_readiness_tiers=tuple(tiers),
    everify_statuses=tuple(everify),
    cap_exemption_statuses=tuple(cap),
    score_confidences=tuple(confidence),
    research_staff_h1b_policies=tuple(h1b_policy),
    research_staff_pr_policies=tuple(pr_policy),
    perm_support_policies=tuple(perm_policy),
    eb1b_support_policies=tuple(eb1b_policy),
    minimum_computing_rd=int(minimum_computing_rd),
    minimum_engineering_rd=int(minimum_engineering_rd),
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
    minimum_core_policy_review_coverage=float(policy_coverage) if require_policy_coverage else None,
    minimum_h1b_score=float(minimum_h1b) if use_minimum_scores else None,
    minimum_green_card_score=float(minimum_green_card) if use_minimum_scores else None,
    minimum_sponsorship_score=float(minimum_sponsorship) if use_minimum_scores else None,
    minimum_research_pathway_score=float(minimum_pathway) if use_minimum_scores else None,
    sort_by=cast(InstitutionSort, sort_labels[sort_label]),
)
institutions = service.list_institutions(filters, limit=500)

if institutions.is_empty():
    st.info("No institution evidence profiles match these filters.")
else:
    st.write(f"Showing {institutions.height:,} matching institutions (maximum 500 on screen).")
    decision_columns = [
        "official_name",
        "decision_readiness_tier",
        "research_pathway_score",
        "score_coverage",
        "green_card_history_score",
        "h1b_history_score",
        "research_staff_permanent_residence_policy",
        "perm_support",
        "eb1b_support",
        "core_policy_review_coverage",
        "everify_status",
        "cap_exemption_status",
        "relevant_certified_perm_count",
        "relevant_lca_count",
        "last_observed_activity_year",
        "research_strength_score",
    ]
    st.dataframe(
        explicit_unknowns(institutions.select(decision_columns)).to_arrow(),
        width="stretch",
        hide_index=True,
        height=620,
    )
    explanation_labels = {
        f"{row['official_name']} · {row['institution_id']}": row for row in institutions.to_dicts()
    }
    selected_label = st.selectbox("Explain a ranking", list(explanation_labels))
    selected = explanation_labels[selected_label]
    with st.container(border=True):
        st.markdown("**Why this ranks here**")
        st.write(rank_explanation(selected))
        st.markdown("**What remains unknown**")
        st.write(unknown_explanation(selected))
    render_detail_navigation(
        institutions,
        label_column="official_name",
        key="institution-organization-detail",
    )

st.info(
    "Research spending cannot make an institution immigration-friendly by itself. E-Verify is "
    "a separate STEM OPT signal, and potential cap exemption is not a verified legal conclusion."
)
render_evidence_notice()

with st.expander("Export the full filtered result"):
    prepare = st.checkbox("Prepare institution CSV and Parquet downloads")
    if prepare:
        csv_data = service.export_institutions(filters, "csv")
        parquet_data = service.export_institutions(filters, "parquet")
        left, right = st.columns(2)
        left.download_button(
            "Download CSV",
            csv_data,
            file_name="institution_metrics_filtered.csv",
            mime="text/csv",
        )
        right.download_button(
            "Download Parquet",
            parquet_data,
            file_name="institution_metrics_filtered.parquet",
            mime="application/vnd.apache.parquet",
        )
