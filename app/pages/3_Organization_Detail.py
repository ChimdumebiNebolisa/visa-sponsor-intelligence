"""Evidence drilldown for one parent organization or legal entity."""

import streamlit as st
from app.components.explorer import configure_page, render_evidence_notice

service = configure_page("Organization Detail")
st.title("Organization Detail")
search = st.text_input("Find an organization", placeholder="Enter a parent, legal name, or alias")
results = service.search_organizations(search, limit=50) if search.strip() else None

if results is None or results.is_empty():
    st.info("Search for an organization to inspect its identity, trends, titles, and provenance.")
    st.stop()

rows = results.to_dicts()
labels = {
    (
        f"{row['organization_name']} · {row['state'] or 'state unknown'} · {row['organization_id']}"
    ): row["organization_id"]
    for row in rows
}
selected_label = st.selectbox("Matching organizations", list(labels))
detail = service.get_organization_detail(labels[selected_label])
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
activity[0].metric("Relevant LCA", f"{summary['relevant_lca_count']:,}")
activity[1].metric("USCIS initial approvals", f"{summary['initial_approvals']:,}")
activity[2].metric("Relevant certified PERM", f"{summary['relevant_certified_perm_count']:,}")
activity[3].metric("Source coverage", f"{summary['source_coverage_ratio']:.0%}")

if summary["has_partial_period"]:
    st.warning(
        f"FY{summary['current_partial_fiscal_year']} contains partial-period evidence and is "
        "not directly comparable to a complete fiscal year."
    )

identity_tab, history_tab, roles_tab, research_tab, signals_tab, provenance_tab = st.tabs(
    [
        "Identity",
        "Immigration history",
        "Technical roles",
        "Research data",
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
    st.write("Worksite states")
    st.dataframe(detail.worksite_states.to_arrow(), width="stretch", hide_index=True)
    st.write("Wage distributions by source and unit")
    st.dataframe(detail.wage_summary.to_arrow(), width="stretch", hide_index=True)
with roles_tab:
    st.dataframe(detail.relevant_titles.to_arrow(), width="stretch", hide_index=True)
with research_tab:
    if detail.institutions.is_empty():
        st.info("No IPEDS institution identity is linked to this organization.")
    else:
        st.dataframe(detail.institutions.to_arrow(), width="stretch", hide_index=True)
        st.caption(
            "IPEDS and HERD observations describe institution identity and research activity; "
            "they do not establish sponsorship eligibility."
        )
    st.write("Reviewed official policy evidence")
    if detail.policy_evidence.is_empty():
        st.info("No reviewed official policy facts are published for this organization.")
    else:
        st.dataframe(detail.policy_evidence.to_arrow(), width="stretch", hide_index=True)
        st.caption(
            "Only human-reviewed facts with an official URL and an exact supporting excerpt "
            "are shown here."
        )
with signals_tab:
    st.write(
        {
            "E-Verify": summary["everify_status"],
            "Known OPT observation": summary["known_opt_observation"],
            "Cap exemption": summary["cap_exemption_status"],
            "Evidence confidence": summary["evidence_confidence"],
            "H-1B activity score": summary["h1b_activity_score"],
            "Immigration evidence score": summary["immigration_evidence_score"],
        }
    )
    st.caption(
        "An E-Verify NO_MATCH or ambiguous lookup is displayed as UNKNOWN. OPT absence is also "
        "UNKNOWN because the official report contains positive Top 200 observations only."
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
    st.info("Composite scores remain unscored until Phase 8.")
with provenance_tab:
    st.dataframe(detail.provenance.to_arrow(), width="stretch", hide_index=True)
    render_evidence_notice()

st.warning(service.get_status().disclaimer)
