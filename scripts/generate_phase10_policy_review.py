"""Build the bounded top-50 core-policy human-review packet."""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

CORE_FACT_TYPES = (
    "h1b_research_staff_eligible",
    "pr_research_staff_eligible",
    "perm_supported",
    "eb1b_supported",
)
COMPLETED_REVIEW_STATUSES = ("REVIEWED_ACCEPTED", "REVIEWED_NOT_STATED")


def _optional_column(frame: pl.DataFrame, name: str, dtype: pl.DataType) -> pl.DataFrame:
    if name in frame.columns:
        return frame
    return frame.with_columns(pl.lit(None, dtype=dtype).alias(name))


def _legal_employers(data_root: Path) -> pl.DataFrame:
    legal = pl.read_parquet(data_root / "processed" / "legal_entities.parquet")
    return (
        legal.filter(pl.col("institution_id").is_not_null())
        .group_by("institution_id")
        .agg(pl.col("legal_name").sort().unique().str.join("; ").alias("legal_employer"))
    )


def build_packet(data_root: Path, *, limit: int = 50) -> tuple[pl.DataFrame, dict[str, int]]:
    """Return exactly four review rows for each deterministic priority institution."""

    metrics = pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")
    ranked = (
        metrics.filter(pl.col("institution_id").is_not_null())
        .sort(
            [
                "sponsorship_history_coverage",
                "sponsorship_history_score",
                "relevant_certified_perm_count",
                "h1b_history_score",
                "research_strength_score",
                "official_name",
            ],
            descending=[True, True, True, True, True, False],
            nulls_last=True,
        )
        .head(limit)
        .with_row_index("priority_rank", offset=1)
        .select(
            "priority_rank",
            "institution_id",
            pl.col("official_name").alias("institution"),
            pl.col("parent_system").alias("parent_system"),
            "sponsorship_history_score",
            "research_strength_score",
        )
        .join(_legal_employers(data_root), on="institution_id", how="left")
    )

    facts = pl.read_parquet(data_root / "processed" / "policy_facts.parquet")
    documents = pl.read_parquet(data_root / "processed" / "policy_documents.parquet")
    for name, dtype in (
        ("reviewer_id", pl.String),
        ("reviewed_at", pl.String),
        ("reviewer_note", pl.String),
        ("section_or_page", pl.String),
    ):
        facts = _optional_column(facts, name, dtype)

    selected_facts = (
        facts.filter(
            pl.col("institution_id").is_in(ranked["institution_id"].to_list())
            & pl.col("fact_type").is_in(CORE_FACT_TYPES)
            & pl.col("is_current")
            & pl.col("valid_to").is_null()
        )
        .with_columns(
            pl.when(pl.col("human_review_status") == "REVIEWED_ACCEPTED")
            .then(pl.lit(0))
            .when(pl.col("human_review_status") == "REVIEWED_NOT_STATED")
            .then(pl.lit(1))
            .when(pl.col("human_review_status") == "NEEDS_REVIEW")
            .then(pl.lit(2))
            .otherwise(pl.lit(3))
            .alias("_review_priority")
        )
        .sort(
            [
                "institution_id",
                "fact_type",
                "_review_priority",
                "exact_excerpt_verified",
                "confidence",
                "retrieved_at",
            ],
            descending=[False, False, False, True, True, True],
        )
        .group_by("institution_id", "fact_type", maintain_order=True)
        .first()
        .join(
            documents.select(
                "institution_id",
                "policy_document_id",
                pl.col("title").alias("page_title"),
            ),
            on=["institution_id", "policy_document_id"],
            how="left",
        )
    )

    required = pl.DataFrame(
        {
            "institution_id": [
                institution_id
                for institution_id in ranked["institution_id"].to_list()
                for _ in CORE_FACT_TYPES
            ],
            "fact_type": list(CORE_FACT_TYPES) * ranked.height,
        }
    )
    packet = (
        required.join(ranked, on="institution_id", how="left")
        .join(selected_facts, on=["institution_id", "fact_type"], how="left")
        .with_columns(
            pl.col("legal_employer").fill_null("UNKNOWN"),
            pl.col("parent_system").fill_null("NONE_RECORDED"),
            pl.col("fact_value").fill_null("UNKNOWN").alias("extracted_value"),
            pl.col("source_url").fill_null(""),
            pl.col("supporting_excerpt").fill_null(""),
            pl.col("page_title").fill_null(""),
            pl.col("retrieved_at").cast(pl.String).fill_null(""),
            pl.lit("UNKNOWN_SCOPE_REVIEW_REQUIRED").alias("campus_system_scope"),
            pl.col("confidence").alias("extractor_confidence"),
            pl.col("human_review_status").fill_null("MISSING_CANDIDATE").alias("review_status"),
            pl.col("reviewer_id").fill_null("").alias("reviewer"),
            pl.col("reviewer_note").fill_null(""),
        )
        .sort("priority_rank", "fact_type")
        .select(
            "priority_rank",
            "institution",
            "legal_employer",
            "parent_system",
            "fact_type",
            "extracted_value",
            "source_url",
            "supporting_excerpt",
            "page_title",
            "retrieved_at",
            "campus_system_scope",
            "extractor_confidence",
            "review_status",
            "reviewer",
            "reviewer_note",
        )
    )
    completed = (
        packet.filter(pl.col("review_status").is_in(COMPLETED_REVIEW_STATUSES))
        .group_by("priority_rank")
        .agg(pl.col("fact_type").n_unique().alias("reviewed_count"))
        .filter(pl.col("reviewed_count") == len(CORE_FACT_TYPES))
        .height
    )
    summary = {
        "priority_institution_count": ranked.height,
        "review_row_count": packet.height,
        "complete_institution_count": completed,
        "remaining_institution_count": ranked.height - completed,
        "remaining_fact_count": packet.filter(
            ~pl.col("review_status").is_in(COMPLETED_REVIEW_STATUSES)
        ).height,
    }
    return packet, summary


def _markdown(packet: pl.DataFrame, summary: dict[str, int]) -> str:
    lines = [
        "# Top-50 core-policy review packet",
        "",
        "This packet is operator input. Model-extracted rows remain pending until a human checks "
        "the official domain, current page, campus/system scope, value, and exact excerpt.",
        "",
        f"- Priority institutions: {summary['priority_institution_count']}",
        f"- Complete four-question profiles: {summary['complete_institution_count']}",
        f"- Institutions still requiring review: {summary['remaining_institution_count']}",
        f"- Facts still requiring review: {summary['remaining_fact_count']}",
        "",
        "`REVIEWED_NOT_STATED` means the reviewer completed the question and the official "
        "material did not state a substantive answer. It is not NO, UNKNOWN, or an extraction "
        "failure.",
        "",
        "| Rank | Institution | Fact | Value | Review | Official URL | Exact excerpt | Scope |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in packet.to_dicts():
        escaped = {
            key: str(value).replace("|", "\\|").replace("\n", " ") for key, value in row.items()
        }
        lines.append(
            f"| {escaped['priority_rank']} | {escaped['institution']} | "
            f"`{escaped['fact_type']}` | `{escaped['extracted_value']}` | "
            f"`{escaped['review_status']}` | {escaped['source_url']} | "
            f"{escaped['supporting_excerpt']} | `{escaped['campus_system_scope']}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/review"))
    parser.add_argument("--limit", type=int, default=50)
    arguments = parser.parse_args()
    if arguments.limit != 50:
        raise ValueError("Phase 10 publication review requires exactly 50 institutions")
    packet, summary = build_packet(arguments.data_root, limit=arguments.limit)
    arguments.output_root.mkdir(parents=True, exist_ok=True)
    packet.write_csv(arguments.output_root / "core_policy_top50.csv")
    (arguments.output_root / "core_policy_top50.md").write_text(
        _markdown(packet, summary), encoding="utf-8"
    )
    print(summary)


if __name__ == "__main__":
    main()
