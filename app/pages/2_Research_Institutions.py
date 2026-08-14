"""IPEDS, HERD, and immigration metrics for research institutions."""

import streamlit as st
from app.components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import InstitutionFilters

service = configure_page("Universities and Research Institutions")
facets = service.institution_facets()

st.title("Universities and Research Institutions")
st.caption("IPEDS identity, HERD research measures, and immigration records remain distinct.")

with st.sidebar:
    st.subheader("Institution filters")
    search = st.text_input("Institution or system name")
    controls = st.multiselect("Public/private control", facets.get("controls", []))
    states = st.multiselect("State", facets.get("states", []))
    cap = st.multiselect("Cap-exemption status", facets.get("cap_exemption_statuses", []))
    minimum_total_rd = st.number_input(
        "Minimum total R&D ($)", min_value=0, value=0, step=1_000_000
    )
    minimum_computing_rd = st.number_input(
        "Minimum computing R&D ($)", min_value=0, value=0, step=100_000
    )
    minimum_engineering_rd = st.number_input(
        "Minimum engineering R&D ($)", min_value=0, value=0, step=100_000
    )
    minimum_lca = st.number_input("Minimum relevant LCA records", min_value=0, value=0)
    minimum_perm = st.number_input("Minimum relevant certified PERM", min_value=0, value=0)

filters = InstitutionFilters(
    search=search,
    controls=tuple(controls),
    states=tuple(states),
    cap_exemption_statuses=tuple(cap),
    minimum_total_rd=int(minimum_total_rd),
    minimum_computing_rd=int(minimum_computing_rd),
    minimum_engineering_rd=int(minimum_engineering_rd),
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
)
institutions = service.list_institutions(filters, limit=500)

if institutions.is_empty():
    st.info("No institution metrics match these filters.")
else:
    st.write(f"Showing {institutions.height:,} matching institutions (maximum 500 on screen).")
    st.dataframe(institutions.to_arrow(), width="stretch", hide_index=True, height=620)

st.info(
    "IPEDS presence supports a potential higher-education cap-exemption label, not a verified "
    "legal conclusion. Policy fields remain UNKNOWN until reviewed evidence exists; E-Verify "
    "is shown only where a prioritized exact lookup confirms it."
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
