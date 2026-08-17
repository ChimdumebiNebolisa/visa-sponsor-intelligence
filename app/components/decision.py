"""Product A rating display helpers shared by Streamlit pages."""

from __future__ import annotations

from urllib.parse import quote

import polars as pl
import streamlit as st


def explicit_unknowns(frame: pl.DataFrame) -> pl.DataFrame:
    """Render null table cells explicitly without changing exported evidence types."""

    return frame.select(
        *[
            pl.when(pl.col(column).is_null())
            .then(pl.lit("UNKNOWN"))
            .otherwise(pl.col(column).cast(pl.String))
            .alias(column)
            for column in frame.columns
        ]
    )


def organization_detail_url(organization_id: str) -> str:
    """Build a deployment-relative organization-detail link."""

    return f"./Organization_Detail?organization_id={quote(organization_id, safe='')}"


def render_detail_navigation(
    frame: pl.DataFrame,
    *,
    label_column: str,
    key: str,
) -> None:
    """Offer a clear path from a ranked result to its organization detail."""

    if frame.is_empty() or "organization_id" not in frame.columns:
        return
    candidates = [
        row
        for row in frame.select("organization_id", label_column)
        .unique(maintain_order=True)
        .to_dicts()
        if row["organization_id"]
    ]
    if not candidates:
        return
    labels = {
        f"{row[label_column]} · {row['organization_id']}": row["organization_id"]
        for row in candidates
    }
    selected = st.selectbox("Open organization detail", list(labels), key=key)
    st.link_button(
        "Inspect identity, annual evidence, titles, and provenance",
        organization_detail_url(labels[selected]),
    )


def rating_display(record: dict[str, object], prefix: str) -> str:
    """Combine the primary star display with its accessible text label."""

    stars = record.get(f"{prefix}_stars") or "Unrated"
    label = record.get(f"{prefix}_star_label") or "Unrated"
    if str(stars) == str(label):
        return str(stars)
    return f"{stars} ({label})"


def rating_explanation(record: dict[str, object], prefix: str) -> str:
    """Return the stored evidence-based explanation for a Product A rating."""

    explanation = record.get(f"{prefix}_explanation")
    return (
        str(explanation)
        if explanation
        else "This rating is Unrated because required evidence is unavailable."
    )


def does_not_prove(prefix: str) -> str:
    """Return the mandatory limitation text for a rating or context signal."""

    messages = {
        "h1b_history": (
            "An LCA is not an approved H-1B petition, and historical filings do not prove that "
            "a current opening will be sponsored."
        ),
        "green_card_history": (
            "A certified PERM filing is not a green-card approval or a promise that the employer "
            "will support a future case."
        ),
        "overall_sponsorship": (
            "Historical H-1B and PERM evidence does not establish eligibility, current employer "
            "policy, or the outcome of any future case."
        ),
        "research_scale": (
            "Research expenditure describes institutional scale. It does not establish "
            "sponsorship willingness, job eligibility, or cap-exempt status."
        ),
    }
    return messages[prefix]


def render_rating_reason(record: dict[str, object], prefix: str, label: str) -> None:
    """Render a star rating with its explanation and explicit limitation."""

    with st.container(border=True):
        st.markdown(f"**{label}: {rating_display(record, prefix)}**")
        st.markdown("**Why this rating**")
        st.write(rating_explanation(record, prefix))
        st.markdown("**What this does not prove**")
        st.write(does_not_prove(prefix))
