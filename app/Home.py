"""Coverage and freshness overview for the sponsorship evidence database."""

import streamlit as st

from app.components.explorer import configure_page, render_evidence_notice

service = configure_page("Sponsorship Intelligence Explorer")
status = service.get_status()
overview = service.get_overview()

st.title("Sponsorship Intelligence Explorer")
st.caption("Historical employer and institution evidence from official U.S. sources")
st.info(status.message)

first = st.columns(4)
first[0].metric("Legal entities", f"{overview.legal_entity_count:,}")
first[1].metric("Parent organizations", f"{overview.parent_organization_count:,}")
first[2].metric("Institutions", f"{overview.institution_count:,}")
first[3].metric("Unresolved entity matches", f"{overview.unresolved_entity_match_count:,}")
second = st.columns(3)
second[0].metric("Relevant H-1B LCA records", f"{overview.relevant_lca_count:,}")
second[1].metric("Relevant certified PERM records", f"{overview.relevant_certified_perm_count:,}")
second[2].metric(
    "Institutions with reviewed policy", f"{overview.reviewed_policy_institution_count:,}"
)

st.subheader("Source coverage and freshness")
st.dataframe(overview.source_coverage.to_arrow(), width="stretch", hide_index=True)
render_evidence_notice()
st.warning(status.disclaimer)
