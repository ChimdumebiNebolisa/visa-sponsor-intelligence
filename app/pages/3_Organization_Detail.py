"""Product A evidence drilldown for one legal entity or separate parent rollup."""

import streamlit as st
from components.decision import explicit_unknowns, render_rating_reason
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Organization Detail")
st.title("Organization Detail")

requested_id = st.query_params.get("organization_id")
selected_id = str(requested_id) if requested_id else None
if selected_id is None:
    search = st.text_input(
        "Find an organization", placeholder="Enter a parent, legal name, or observed alias"
    )
    results = service.search_organizations(search, limit=50) if search.strip() else None
    if results is None or results.is_empty():
        st.info(
            "Search for an organization to inspect its legal identity, annual history, raw "
            "titles, and provenance."
        )
        st.stop()
    labels = {
        f"{row['organization_name']} · {row['identity_scope']} · "
        f"{row['state'] or 'state unknown'} · {row['organization_id']}": row["organization_id"]
        for row in results.to_dicts()
    }
    selected_label = st.selectbox("Matching organizations", list(labels))
    selected_id = labels[selected_label]
else:
    st.caption(f"Opened from a ranked result: `{selected_id}`")
    if st.button("Choose a different organization"):
        st.query_params.clear()
        st.rerun()

assert selected_id is not None
detail = service.get_organization_detail(selected_id)
if detail is None:
    st.error("The selected organization is no longer available in this build.")
    st.stop()

summary = detail.summary.to_dicts()[0]
st.subheader(summary["organization_name"])
identity = st.columns(4)
identity[0].metric("Identity scope", summary["identity_scope"])
identity[1].metric("Legal entities", f"{summary['legal_entity_count']:,}")
identity[2].metric("Organization type", summary["organization_type"])
identity[3].metric("State", summary["state"] or "UNKNOWN")

activity = st.columns(4)
activity[0].metric("Certified technical H-1B LCA", f"{summary['relevant_lca_count']:,}")
activity[1].metric("Employer-level H-1B initial approvals", f"{summary['initial_approvals']:,}")
activity[2].metric("Certified technical PERM", f"{summary['relevant_certified_perm_count']:,}")
coverage = summary.get("source_coverage_ratio")
activity[3].metric("Source coverage", "UNKNOWN" if coverage is None else f"{coverage:.0%}")

if summary["identity_scope"] == "PARENT_ROLLUP":
    st.warning(
        "This is a separate parent rollup. Immigration records remain attached to the "
        "petitioning legal entities listed on the Identity tab."
    )
coverage_state = summary.get("entity_coverage_state", "UNRESOLVED_IDENTITY")
if coverage_state == "PARTIAL_ENTITY_COVERAGE":
    st.warning("Rating is based on confirmed records. Additional ambiguous records were excluded.")
elif coverage_state == "UNRESOLVED_IDENTITY":
    st.warning("Employer identity is unresolved; confirmed evidence is insufficient to score.")
if summary["has_partial_period"]:
    period = f"FY{summary['current_partial_fiscal_year']}"
    if summary.get("current_partial_quarter") is not None:
        period += f" Q{summary['current_partial_quarter']}"
    st.warning(f"{period} is partial and is not directly comparable with a complete fiscal year.")

ratings_tab, identity_tab, history_tab, evidence_tab, context_tab, provenance_tab = st.tabs(
    [
        "Ratings",
        "Identity",
        "Annual history",
        "Titles, statuses & wages",
        "Institution & supplemental context",
        "Provenance",
    ]
)
with ratings_tab:
    render_rating_reason(summary, "overall_sponsorship", "Overall Sponsorship")
    render_rating_reason(summary, "h1b_history", "H-1B History")
    render_rating_reason(
        summary,
        "green_card_history",
        "Green Card Sponsorship History (observed employer-sponsored PERM history)",
    )
    st.caption(f"Score version: {summary['score_version']}")
with identity_tab:
    st.write("Petitioning legal entities")
    st.dataframe(detail.legal_entities.to_arrow(), width="stretch", hide_index=True)
    st.write("Observed aliases and match decisions")
    st.dataframe(detail.aliases.to_arrow(), width="stretch", hide_index=True)
    st.caption(
        "Legal-employer identity is resolved from employer name and legal address. Worksite "
        "location is evidence context, not an identity key."
    )
with history_tab:
    st.write("H-1B LCA and employer-level USCIS activity")
    st.dataframe(detail.h1b_trends.to_arrow(), width="stretch", hide_index=True)
    if not detail.h1b_trends.is_empty() and "fiscal_year" in detail.h1b_trends.columns:
        chart_columns = [
            column
            for column in ("relevant_lca_count", "initial_approvals")
            if column in detail.h1b_trends.columns
        ]
        if chart_columns:
            st.line_chart(
                detail.h1b_trends.select("fiscal_year", *chart_columns).to_pandas(),
                x="fiscal_year",
            )
    st.caption(
        "Employer-level H-1B initial approvals are corroborating totals and are not "
        "job-title-specific."
    )
    st.write("Observed employer-sponsored PERM history")
    st.dataframe(detail.perm_trends.to_arrow(), width="stretch", hide_index=True)
with evidence_tab:
    st.write("Exact rating-supporting DOL cases")
    if detail.rating_supporting_cases.is_empty():
        st.info("No qualifying technical H-1B LCA or PERM case rows support these ratings.")
    else:
        st.dataframe(
            explicit_unknowns(detail.rating_supporting_cases).to_arrow(),
            width="stretch",
            hide_index=True,
        )
    st.caption(
        "This bounded table includes only technical H-1B LCA rows with Certified or "
        "Certified-Withdrawn status and technical PERM rows with Certified or "
        "Certified-Expired status. These are historical source records, not sponsorship promises."
    )
    st.write("Raw relevant titles and normalized job families")
    st.dataframe(detail.relevant_titles.to_arrow(), width="stretch", hide_index=True)
    st.write("Broader case-status context (includes records not counted in ratings)")
    st.dataframe(detail.case_statuses.to_arrow(), width="stretch", hide_index=True)
    st.write("Broader worksite context (not legal-employer addresses)")
    st.dataframe(detail.worksite_states.to_arrow(), width="stretch", hide_index=True)
    st.write("Broader wage context by official source and unit")
    st.dataframe(detail.wage_summary.to_arrow(), width="stretch", hide_index=True)
with context_tab:
    if detail.institutions.is_empty():
        st.info("No IPEDS institution identity is linked to this organization.")
    else:
        institution_record = detail.institutions.to_dicts()[0]
        institution_context_columns = [
            "official_name",
            "institution_id",
            "legal_employer_name",
            "parent_organization_name",
            "state",
            "control",
            "sector",
            "higher_education_context",
            "overall_sponsorship_stars",
            "overall_sponsorship_star_label",
            "green_card_history_stars",
            "green_card_history_star_label",
            "h1b_history_stars",
            "h1b_history_star_label",
            "relevant_certified_perm_count",
            "relevant_lca_count",
            "initial_approvals",
            "everify_status",
            "known_opt_observation",
            "research_scale_stars",
            "research_scale_star_label",
            "latest_herd_year",
            "computing_rd",
            "engineering_rd",
            "total_rd",
        ]
        st.dataframe(
            explicit_unknowns(detail.institutions.select(institution_context_columns)).to_arrow(),
            width="stretch",
            hide_index=True,
        )
        render_rating_reason(
            institution_record,
            "research_scale",
            "Research Scale (separate context)",
        )
    st.write("E-Verify evidence (supplemental; not used in ratings)")
    if detail.everify_evidence.is_empty():
        st.info("This organization has not been checked or has no confirmed E-Verify observation.")
    else:
        st.dataframe(detail.everify_evidence.to_arrow(), width="stretch", hide_index=True)
    st.write("Positive-only OPT evidence (supplemental; not used in ratings)")
    if detail.opt_evidence.is_empty():
        st.info("No linked positive report observation; absence remains UNKNOWN.")
    else:
        st.dataframe(detail.opt_evidence.to_arrow(), width="stretch", hide_index=True)
    st.write("Retained policy evidence — Supplemental · Incomplete · Not used in ratings")
    if detail.policy_evidence.is_empty():
        st.info("No reviewed supplemental policy facts are available.")
    else:
        st.dataframe(detail.policy_evidence.to_arrow(), width="stretch", hide_index=True)
with provenance_tab:
    st.dataframe(detail.provenance.to_arrow(), width="stretch", hide_index=True)
    render_evidence_notice()
