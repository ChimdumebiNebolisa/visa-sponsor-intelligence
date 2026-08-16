"""Decision-readiness display helpers shared by Streamlit pages."""

from __future__ import annotations

from urllib.parse import quote

import polars as pl
import streamlit as st


def _as_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"Expected a numeric display value, received {type(value).__name__}")


def explicit_unknowns(frame: pl.DataFrame) -> pl.DataFrame:
    """Render null table cells explicitly without altering exported evidence types."""

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
        "Inspect legal entities, history, titles, and policy excerpts",
        organization_detail_url(labels[selected]),
    )


def rank_explanation(record: dict[str, object]) -> str:
    """Return the stored row-specific explanation for a ranking result."""

    explanations = [
        record.get("decision_readiness_explanation"),
        record.get("research_pathway_explanation"),
        record.get("sponsorship_history_explanation"),
    ]
    return " ".join(str(value) for value in explanations if value)


def unknown_explanation(record: dict[str, object]) -> str:
    """Describe material evidence gaps without converting them into negative findings."""

    unknowns: list[str] = []
    for label, score, coverage in (
        (
            "H-1B history",
            record.get("h1b_history_score"),
            record.get("h1b_history_coverage"),
        ),
        (
            "green-card history",
            record.get("green_card_history_score"),
            record.get("green_card_history_coverage"),
        ),
        (
            "research strength",
            record.get("research_strength_score"),
            record.get("research_strength_coverage"),
        ),
    ):
        if score is None:
            unknowns.append(f"{label} has no score")
        elif coverage is not None and _as_float(coverage) < 1:
            unknowns.append(f"{label} is only {_as_float(coverage):.0%} covered")
    review_coverage = record.get("core_policy_review_coverage")
    if review_coverage is not None and _as_float(review_coverage) < 1:
        unknowns.append(f"core policy review is {_as_float(review_coverage):.0%} complete")
    for label, field in (
        ("research-staff H-1B policy", "research_staff_h1b_policy"),
        ("research-staff permanent-residence policy", "research_staff_permanent_residence_policy"),
        ("PERM support", "perm_support"),
        ("EB-1B support", "eb1b_support"),
        ("E-Verify", "everify_status"),
    ):
        value = record.get(field)
        if value in (None, "UNKNOWN"):
            unknowns.append(f"{label} is unknown")
        elif value == "NOT_STATED":
            unknowns.append(f"{label} was reviewed but is not stated in the official source")
    if not unknowns:
        return "No material gap is visible in the selected fields; inspect provenance and dates."
    return "; ".join(unknowns) + ". Missing evidence is not a negative conclusion."
