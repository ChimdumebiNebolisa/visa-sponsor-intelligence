"""Product A starting point for the historical sponsorship explorer."""

import streamlit as st
from components.decision import explicit_unknowns, render_detail_navigation
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Sponsorship Intelligence Explorer")
status = service.get_status()
overview = service.get_overview()

st.title("Sponsorship Intelligence Explorer")
st.caption("Observed technical H-1B and PERM sponsorship history from authoritative U.S. sources")
st.info(status.message)

st.subheader("What this explorer can tell you")
st.markdown(
    "Use the employer and institution rankings to find repeated, recent, and broad technical "
    "sponsorship history. Open a detail page to verify the petitioning legal entity, annual "
    "records, raw titles, statuses, locations, wages, and source provenance."
)
st.caption(
    "E-Verify, positive-only OPT evidence, institution type, possible cap-exemption context, "
    "and Research Scale are supplemental context. They never change sponsorship ratings."
)

counts = st.columns(6)
counts[0].metric("Legal entities", f"{overview.legal_entity_count:,}")
counts[1].metric("Parent organizations", f"{overview.parent_organization_count:,}")
counts[2].metric("Institutions", f"{overview.institution_count:,}")
counts[3].metric("Certified technical H-1B LCA", f"{overview.relevant_lca_count:,}")
counts[4].metric("Certified technical PERM", f"{overview.relevant_certified_perm_count:,}")
counts[5].metric("Entity review queue", f"{overview.unresolved_entity_match_count:,}")

release_bits = [f"Build ID: {status.build_id}"]
if status.score_version:
    release_bits.append(f"ratings: {status.score_version}")
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

employer_tab, institution_tab = st.tabs(["Top observed employers", "Top observed institutions"])
with employer_tab:
    employers = service.list_employers(limit=10)
    if employers.is_empty():
        st.info("No rated employer rows are available in this build.")
    else:
        st.dataframe(
            explicit_unknowns(
                employers.select(
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
                    "last_observed_activity_year",
                )
            ).to_arrow(),
            width="stretch",
            hide_index=True,
        )
        render_detail_navigation(
            employers,
            label_column="organization_name",
            key="home-employer-detail",
        )
with institution_tab:
    institutions = service.list_institutions(limit=10)
    if institutions.is_empty():
        st.info("No matched institution rows are available in this build.")
    else:
        st.dataframe(
            explicit_unknowns(
                institutions.select(
                    "official_name",
                    "legal_employer_name",
                    "overall_sponsorship_stars",
                    "overall_sponsorship_star_label",
                    "green_card_history_stars",
                    "green_card_history_star_label",
                    "h1b_history_stars",
                    "h1b_history_star_label",
                    "research_scale_stars",
                    "research_scale_star_label",
                    "relevant_certified_perm_count",
                    "relevant_lca_count",
                )
            ).to_arrow(),
            width="stretch",
            hide_index=True,
        )
        render_detail_navigation(
            institutions,
            label_column="official_name",
            key="home-institution-detail",
        )

st.subheader("Methodology")
st.markdown(
    "H-1B History uses qualifying H-1B LCA volume, complete-year consistency, recency, job-family "
    "breadth, and limited employer-level USCIS initial-approval corroboration. Green Card "
    "Sponsorship History uses qualifying PERM volume, consistency, recency, and breadth. Overall "
    "Sponsorship combines both only when both components are resolved. The tables display whole "
    "stars; hidden deterministic scores are used only for ordering and audit."
)

st.subheader("Source coverage and freshness")
st.dataframe(overview.source_coverage.to_arrow(), width="stretch", hide_index=True)
render_evidence_notice()
