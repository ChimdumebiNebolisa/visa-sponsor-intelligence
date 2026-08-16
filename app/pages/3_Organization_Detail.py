"""Evidence drilldown for one parent organization or legal entity."""

import streamlit as st
from components.decision import explicit_unknowns, rank_explanation, unknown_explanation
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Organization Detail")
st.title("Organization Detail")

requested_id = st.query_params.get("organization_id")
selected_id = str(requested_id) if requested_id else None
if selected_id is None:
    search = st.text_input(
        "Find an organization", placeholder="Enter a parent, legal name, or alias"
    )
    results = service.search_organizations(search, limit=50) if search.strip() else None
    if results is None or results.is_empty():
        st.info(
            "Search for an organization to inspect its identity, trends, titles, and provenance."
        )
        st.stop()
    labels = {
        f"{row['organization_name']} · {row['state'] or 'state unknown'} · "
        f"{row['organization_id']}": row["organization_id"]
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
ranking_record = summary
if not detail.institutions.is_empty():
    ranking_record = summary | detail.institutions.to_dicts()[0]
st.subheader(summary["organization_name"])
identity = st.columns(4)
identity[0].metric("Identity scope", summary["identity_scope"])
identity[1].metric("Legal entities", f"{summary['legal_entity_count']:,}")
identity[2].metric("Organization type", summary["organization_type"])
identity[3].metric("State", summary["state"] or "UNKNOWN")

activity = st.columns(4)
activity[0].metric("Relevant LCA", f"{summary['relevant_lca_count']:,}")
activity[1].metric("USCIS initial approvals", f"{summary['initial_approvals']:,}")
activity[2].metric("Relevant certified PERM", f"{summary['relevant_certified_perm_count']:,}")
activity[3].metric("Source coverage", f"{summary['source_coverage_ratio']:.0%}")

if summary["has_partial_period"]:
    st.warning(
        f"FY{summary['current_partial_fiscal_year']} contains partial-period evidence and is "
        "not directly comparable to a complete fiscal year."
    )

with st.container(border=True):
    st.markdown("**Why this ranks here**")
    st.write(rank_explanation(ranking_record))
    st.markdown("**What remains unknown**")
    st.write(unknown_explanation(ranking_record))

identity_tab, history_tab, roles_tab, research_tab, signals_tab, provenance_tab = st.tabs(
    [
        "Identity",
        "Immigration history",
        "Technical roles",
        "Research & policy",
        "Signals & scores",
        "Provenance",
    ]
)
with identity_tab:
    st.write("Legal entities")
    st.dataframe(detail.legal_entities.to_arrow(), width="stretch", hide_index=True)
    st.write("Observed aliases")
    st.dataframe(detail.aliases.to_arrow(), width="stretch", hide_index=True)
with history_tab:
    st.write("H-1B LCA and USCIS petition trends")
    st.dataframe(detail.h1b_trends.to_arrow(), width="stretch", hide_index=True)
    if not detail.h1b_trends.is_empty():
        st.line_chart(
            detail.h1b_trends.select(
                "fiscal_year", "relevant_lca_count", "initial_approvals", "continuing_approvals"
            ).to_pandas(),
            x="fiscal_year",
        )
    st.write("PERM trends")
    st.dataframe(detail.perm_trends.to_arrow(), width="stretch", hide_index=True)
    st.write("Case statuses")
    st.dataframe(detail.case_statuses.to_arrow(), width="stretch", hide_index=True)
    st.write("Worksite states (not legal-employer addresses)")
    st.dataframe(detail.worksite_states.to_arrow(), width="stretch", hide_index=True)
    st.write("Wage distributions by source and unit")
    st.dataframe(detail.wage_summary.to_arrow(), width="stretch", hide_index=True)
with roles_tab:
    st.dataframe(detail.relevant_titles.to_arrow(), width="stretch", hide_index=True)
with research_tab:
    if detail.institutions.is_empty():
        st.info("No IPEDS institution identity is linked to this organization.")
    else:
        st.dataframe(
            explicit_unknowns(detail.institutions).to_arrow(), width="stretch", hide_index=True
        )
        st.caption(
            "IPEDS and HERD observations describe identity and research activity; they do not "
            "establish sponsorship eligibility."
        )
    st.write("Reviewed official policy evidence")
    if detail.policy_evidence.is_empty():
        st.info("No reviewed official policy facts are published for this organization.")
    else:
        st.dataframe(detail.policy_evidence.to_arrow(), width="stretch", hide_index=True)
        st.caption(
            "Each published row retains its official URL, exact excerpt, retrieval date, scope, "
            "review status, and reviewer. REVIEWED_NOT_STATED is distinct from NO and UNKNOWN."
        )
with signals_tab:
    score_rows = {
        "Component": [
            "Sponsorship history",
            "H-1B history",
            "Green-card history",
            "STEM OPT readiness",
            "V1 immigration composite (reproducibility)",
        ],
        "Score": [
            summary.get("sponsorship_history_score"),
            summary.get("h1b_history_score"),
            summary.get("green_card_history_score"),
            summary.get("stem_opt_readiness_score"),
            summary.get("immigration_evidence_score"),
        ],
        "Coverage": [
            summary.get("sponsorship_history_coverage"),
            summary.get("h1b_history_coverage"),
            summary.get("green_card_history_coverage"),
            summary.get("stem_opt_readiness_coverage"),
            summary.get("immigration_evidence_coverage"),
        ],
        "Grade/status": [
            summary.get("sponsorship_history_status"),
            summary.get("h1b_history_grade"),
            summary.get("green_card_history_grade"),
            summary.get("stem_opt_readiness_status"),
            summary.get("immigration_evidence_grade"),
        ],
        "Explanation": [
            summary.get("sponsorship_history_explanation"),
            summary.get("h1b_history_explanation"),
            summary.get("green_card_history_explanation"),
            summary.get("stem_opt_readiness_explanation"),
            summary.get("immigration_evidence_explanation"),
        ],
    }
    st.dataframe(score_rows, width="stretch", hide_index=True)
    st.caption(f"Score version: {summary['score_version']}")
    st.write(
        {
            "E-Verify": summary["everify_status"],
            "Known OPT observation": summary["known_opt_observation"],
            "Cap exemption": summary["cap_exemption_status"],
            "Score confidence": summary["evidence_confidence"],
        }
    )
    st.caption(
        "An E-Verify NO_MATCH or ambiguous lookup is UNKNOWN. OPT absence is UNKNOWN because "
        "the official report contains positive observations only."
    )
    st.write("E-Verify lookup evidence")
    if detail.everify_evidence.is_empty():
        st.info("This organization has not been checked in the prioritized E-Verify queue.")
    else:
        st.dataframe(detail.everify_evidence.to_arrow(), width="stretch", hide_index=True)
    st.write("Positive OPT report evidence")
    if detail.opt_evidence.is_empty():
        st.info("No linked positive report observation; status remains UNKNOWN.")
    else:
        st.dataframe(detail.opt_evidence.to_arrow(), width="stretch", hide_index=True)
    st.info(
        "Scores describe historical evidence strength and coverage. They are not legal "
        "assessments or probabilities of sponsorship."
    )
with provenance_tab:
    st.dataframe(detail.provenance.to_arrow(), width="stretch", hide_index=True)
    render_evidence_notice()

st.warning(service.get_status().disclaimer)
