"""Deterministic institution candidate ranking for policy enrichment."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from sponsor_intel.evidence.io import write_parquet_atomic


def _percentile(column: str) -> pl.Expr:
    return (
        pl.col(column)
        .fill_null(0)
        .cast(pl.Float64)
        .rank(method="average")
        .truediv(pl.len())
        .fill_nan(0.0)
    )


def rank_policy_candidates(
    institution_metrics: pl.DataFrame,
    *,
    limit: int = 200,
    manual_priorities: Iterable[str] = (),
) -> pl.DataFrame:
    """Rank 150-250 institutions for enrichment without creating a product score."""

    if not 150 <= limit <= 250:
        raise ValueError("Policy candidate limit must be between 150 and 250")
    required = {
        "institution_id",
        "official_name",
        "official_domain",
        "system_name",
        "organization_id",
        "state",
        "control",
        "relevant_lca_count",
        "relevant_certified_perm_count",
        "last_lca_activity_year",
        "last_perm_activity_year",
        "last_uscis_activity_year",
        "total_rd",
        "computing_rd",
        "engineering_rd",
        "known_opt_observation",
        "everify_status",
    }
    missing = required - set(institution_metrics.columns)
    if missing:
        raise ValueError(f"Institution metrics are missing candidate fields: {sorted(missing)}")

    priorities = {value.casefold().strip() for value in manual_priorities if value.strip()}
    eligible = institution_metrics.filter(
        pl.col("official_domain").fill_null("").str.strip_chars().ne("")
        & (
            (pl.col("total_rd").fill_null(0) > 0)
            | (pl.col("relevant_lca_count").fill_null(0) > 0)
            | (pl.col("relevant_certified_perm_count").fill_null(0) > 0)
        )
    )
    if eligible.height < limit:
        raise ValueError(
            f"Only {eligible.height} eligible institutions are available for {limit} slots"
        )

    ranked = (
        eligible.with_columns(
            _percentile("relevant_lca_count").alias("relevant_lca_component"),
            _percentile("relevant_certified_perm_count").alias("relevant_perm_component"),
            _percentile("total_rd").alias("total_rd_component"),
            _percentile("computing_rd").alias("computing_rd_component"),
            _percentile("engineering_rd").alias("engineering_rd_component"),
            pl.max_horizontal(
                "last_lca_activity_year",
                "last_perm_activity_year",
                "last_uscis_activity_year",
            )
            .fill_null(0)
            .cast(pl.Float64)
            .rank(method="average")
            .truediv(pl.len())
            .alias("recent_activity_component"),
            pl.when(pl.col("known_opt_observation") == "OBSERVED_POSITIVE")
            .then(1.0)
            .otherwise(0.0)
            .alias("opt_component"),
            pl.when(pl.col("everify_status") == "CONFIRMED_ACTIVE")
            .then(1.0)
            .otherwise(0.0)
            .alias("everify_component"),
            pl.when(
                pl.col("control")
                .fill_null("")
                .str.contains(r"(?i)public|private.*not.?for.?profit|private nonprofit")
            )
            .then(1.0)
            .otherwise(0.25)
            .alias("institution_type_component"),
            pl.col("official_name")
            .str.to_lowercase()
            .is_in(priorities)
            .cast(pl.Float64)
            .alias("manual_priority_component"),
        )
        .with_columns(
            (
                pl.col("relevant_lca_component") * 0.20
                + pl.col("relevant_perm_component") * 0.10
                + pl.col("recent_activity_component") * 0.10
                + pl.col("total_rd_component") * 0.20
                + pl.col("computing_rd_component") * 0.10
                + pl.col("engineering_rd_component") * 0.10
                + pl.col("opt_component") * 0.07
                + pl.col("everify_component") * 0.03
                + pl.col("institution_type_component") * 0.05
                + pl.col("manual_priority_component") * 0.05
            )
            .clip(0.0, 1.0)
            .alias("candidate_score")
        )
        .sort(
            ["candidate_score", "total_rd", "relevant_lca_count", "official_name"],
            descending=[True, True, True, False],
        )
        .head(limit)
        .with_row_index("candidate_rank", offset=1)
        .select(
            "candidate_rank",
            "institution_id",
            "official_name",
            "official_domain",
            "system_name",
            "organization_id",
            "state",
            "control",
            "candidate_score",
            "relevant_lca_component",
            "relevant_perm_component",
            "recent_activity_component",
            "total_rd_component",
            "computing_rd_component",
            "engineering_rd_component",
            "opt_component",
            "everify_component",
            "institution_type_component",
            "manual_priority_component",
        )
    )
    expected = list(range(1, limit + 1))
    if ranked["candidate_rank"].to_list() != expected:
        raise ValueError("Candidate ranks must be unique and contiguous")
    return ranked


def build_policy_candidates(
    *,
    data_root: Path = Path("data"),
    limit: int = 200,
    manual_priorities: Iterable[str] = (),
) -> pl.DataFrame:
    """Rank candidates from processed metrics and persist the exact selection."""

    metrics_path = data_root / "processed" / "institution_metrics.parquet"
    if not metrics_path.is_file():
        raise ValueError(f"Institution metrics are unavailable: {metrics_path}")
    candidates = rank_policy_candidates(
        pl.read_parquet(metrics_path),
        limit=limit,
        manual_priorities=manual_priorities,
    )
    write_parquet_atomic(candidates, data_root / "processed" / "policy_candidates.parquet")
    return candidates
