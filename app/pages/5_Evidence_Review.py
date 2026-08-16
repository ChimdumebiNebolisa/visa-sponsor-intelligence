"""Evidence review queues for decisions that must not be guessed."""

import streamlit as st
from components.explorer import configure_page, render_evidence_notice

service = configure_page("Evidence Review")
queues = service.get_evidence_review(limit=500)

st.title("Evidence Review")
st.caption("Ambiguous or unresolved evidence stays out of confirmed product signals.")

entity_tab, everify_tab, opt_tab, policy_tab = st.tabs(
    ["Entity matches", "E-Verify", "OPT employer names", "Policy facts"]
)
for tab, frame, empty_message in (
    (entity_tab, queues.entity, "No entity-review rows are available."),
    (everify_tab, queues.everify, "No E-Verify matches currently require review."),
    (opt_tab, queues.opt, "No OPT employer names currently require review."),
    (policy_tab, queues.policy, "No policy facts currently require review."),
):
    with tab:
        if frame.is_empty():
            st.info(empty_message)
        else:
            st.dataframe(frame.to_arrow(), width="stretch", hide_index=True)

st.info(
    "E-Verify NO_MATCH is not a negative finding, and absence from a positive-only OPT report "
    "is not evidence of zero participation. Both remain UNKNOWN in explorer filters."
)
render_evidence_notice()
