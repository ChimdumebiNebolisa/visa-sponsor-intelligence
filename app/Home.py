"""Decision-ready starting point for the sponsorship evidence explorer."""

import streamlit as st
from components.decision import explicit_unknowns, render_detail_navigation
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Sponsorship Intelligence Explorer")
status = service.get_status()
overview = service.get_overview()

st.title("Sponsorship Intelligence Explorer")
st.caption("Historical employer and institution evidence from official U.S. sources")
st.info(status.message)

st.subheader("Start here")
st.markdown(
    "1. Open **Research Institutions** to begin with evidence-readiness tiers, not R&D volume.  "
    "\n2. Filter reviewed research-staff policy, H-1B history, and green-card history.  "
    "\n3. Open an organization to verify legal entities, counts, dates, and excerpts.  "
    "\n4. Compare up to five organizations, then export the exact filtered evidence."
)
st.caption(
    "Historical sponsorship evidence, STEM OPT readiness, possible cap exemption, official "
    "policy, and research strength are separate signals. None is legal advice or a promise."
)

first = st.columns(5)
first[0].metric("Legal entities", f"{overview.legal_entity_count:,}")
first[1].metric("Parent organizations", f"{overview.parent_organization_count:,}")
first[2].metric("Institutions", f"{overview.institution_count:,}")
first[3].metric("Tier 1 reviewed", f"{overview.tier_1_reviewed_institution_count:,}")
first[4].metric("Entity review queue", f"{overview.unresolved_entity_match_count:,}")
second = st.columns(4)
second[0].metric("Relevant H-1B LCA", f"{overview.relevant_lca_count:,}")
second[1].metric("Relevant certified PERM", f"{overview.relevant_certified_perm_count:,}")
second[2].metric("Any reviewed policy", f"{overview.reviewed_policy_institution_count:,}")
second[3].metric(
    "Complete core-policy review", f"{overview.complete_core_policy_institution_count:,}"
)

st.subheader("Highest-ranked research-institution evidence profiles")
leaders = service.list_institutions(limit=10)
if leaders.is_empty():
    st.warning("No verified institution metrics are available in this build.")
else:
    columns = [
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
    ]
    st.dataframe(
        explicit_unknowns(leaders.select(columns)).to_arrow(),
        width="stretch",
        hide_index=True,
    )
    render_detail_navigation(
        leaders,
        label_column="official_name",
        key="home-organization-detail",
    )
    st.page_link(
        "pages/2_Research_Institutions.py",
        label="Open the full research-institution explorer",
        icon="🏛️",
    )

release_bits = [f"Build ID: {status.build_id}"]
if status.release_tag:
    release_bits.append(f"release: {status.release_tag}")
if status.build_date:
    release_bits.append(f"built: {status.build_date}")
if status.latest_complete_fiscal_year is not None:
    release_bits.append(f"latest complete FY: {status.latest_complete_fiscal_year}")
st.caption(" · ".join(release_bits))
if status.current_partial_fiscal_year is not None:
    period = f"FY{status.current_partial_fiscal_year}"
    if status.current_partial_quarter is not None:
        period += f" Q{status.current_partial_quarter}"
    st.warning(f"{period} is partial and is not directly comparable with complete years.")

st.subheader("Source coverage and freshness")
st.dataframe(overview.source_coverage.to_arrow(), width="stretch", hide_index=True)
render_evidence_notice()
st.warning(status.disclaimer)
