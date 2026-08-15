"""Filter, inspect, and export the raw employer metrics layer."""

import streamlit as st
from app.components.explorer import configure_page, render_evidence_notice

from sponsor_intel.services import EmployerFilters

service = configure_page("All Employers")
facets = service.employer_facets()

st.title("All Employers")
st.caption(
    "Raw evidence remains visible beside nullable, versioned evidence-strength scores. "
    "UNKNOWN is not scored as zero."
)

with st.sidebar:
    st.subheader("Employer filters")
    search = st.text_input("Employer name or alias")
    organization_types = st.multiselect("Organization type", facets.get("organization_types", []))
    states = st.multiselect("State", facets.get("states", []))
    role_family = st.selectbox("Technical role family", ["", *facets.get("role_families", [])])
    everify = st.multiselect("E-Verify status", facets.get("everify_statuses", []))
    opt = st.multiselect("Known OPT status", facets.get("opt_statuses", []))
    cap = st.multiselect("Cap-exemption status", facets.get("cap_exemption_statuses", []))
    confidence = st.multiselect("Evidence confidence", facets.get("evidence_confidences", []))
    minimum_lca = st.number_input("Minimum relevant LCA records", min_value=0, value=0)
    minimum_perm = st.number_input("Minimum relevant certified PERM", min_value=0, value=0)
    minimum_approvals = st.number_input("Minimum USCIS initial approvals", min_value=0, value=0)
    minimum_year = st.selectbox(
        "Last observed activity year",
        ["Any", *range(2022, 2101)],
    )
    exclude_staffing = st.checkbox("Exclude known staffing/consulting organizations")

filters = EmployerFilters(
    search=search,
    organization_types=tuple(organization_types),
    states=tuple(states),
    everify_statuses=tuple(everify),
    opt_statuses=tuple(opt),
    cap_exemption_statuses=tuple(cap),
    evidence_confidences=tuple(confidence),
    role_family=role_family or None,
    minimum_relevant_lca=int(minimum_lca),
    minimum_relevant_perm=int(minimum_perm),
    minimum_initial_approvals=int(minimum_approvals),
    minimum_last_activity_year=None if minimum_year == "Any" else int(minimum_year),
    exclude_known_staffing_consulting=exclude_staffing,
)
employers = service.list_employers(filters, limit=500)

if employers.is_empty():
    st.info("No employer metrics match these filters. UNKNOWN evidence was not treated as NO.")
else:
    st.write(f"Showing {employers.height:,} matching rows (maximum 500 on screen).")
    st.dataframe(employers.to_arrow(), width="stretch", hide_index=True, height=620)

st.info(
    "E-Verify, OPT, and score fields remain UNKNOWN or unscored until their approved phases. "
    "A certified LCA is not an approved H-1B petition, and historical PERM is not a promise."
)
render_evidence_notice()

with st.expander("Export the full filtered result"):
    prepare = st.checkbox("Prepare CSV and Parquet downloads")
    if prepare:
        csv_data = service.export_employers(filters, "csv")
        parquet_data = service.export_employers(filters, "parquet")
        left, right = st.columns(2)
        left.download_button(
            "Download CSV",
            csv_data,
            file_name="employer_metrics_filtered.csv",
            mime="text/csv",
        )
        right.download_button(
            "Download Parquet",
            parquet_data,
            file_name="employer_metrics_filtered.parquet",
            mime="application/vnd.apache.parquet",
        )
