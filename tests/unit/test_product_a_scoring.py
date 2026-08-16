"""Product A deterministic score and whole-star contracts."""

from __future__ import annotations

import polars as pl
import pytest

from sponsor_intel.scoring import (
    ProductAScoringConfig,
    score_employers_product_a,
    score_institutions_product_a,
)
from sponsor_intel.scoring.engine import _product_a_star_rating


def _employers() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "organization_id": ["observed", "zero", "unrated"],
            "identity_scope": ["LEGAL_ENTITY"] * 3,
            "entity_resolution_valid": [True, True, False],
            "lca_source_valid": [True, True, True],
            "perm_source_valid": [True, True, True],
            "uscis_source_valid": [True, True, True],
            "weighted_relevant_lca_count": [100.0, 0.0, 10.0],
            "relevant_certified_lca_count": [90, 0, 10],
            "relevant_certified_withdrawn_lca_count": [20, 0, 0],
            "lca_complete_active_years": [4, 0, 1],
            "lca_relevant_job_family_count": [5, 0, 1],
            "last_relevant_lca_activity_year": [2026, None, 2025],
            "weighted_relevant_perm_count": [50.0, 0.0, 5.0],
            "relevant_certified_perm_count": [45, 0, 5],
            "relevant_certified_expired_perm_count": [10, 0, 0],
            "perm_complete_active_years": [4, 0, 1],
            "perm_relevant_job_family_count": [5, 0, 1],
            "last_relevant_perm_activity_year": [2025, None, 2024],
            "initial_approvals": [80, 0, 5],
            "lca_complete_fiscal_year_count": [4, 4, 4],
            "perm_complete_fiscal_year_count": [4, 4, 4],
            "latest_complete_immigration_fiscal_year": [2025, 2025, 2025],
            "current_partial_immigration_fiscal_year": [2026, 2026, 2026],
            "everify_status": ["CONFIRMED_ACTIVE", "UNKNOWN", "UNKNOWN"],
            "known_opt_observation": ["OBSERVED_POSITIVE", "UNKNOWN", "UNKNOWN"],
            "policy_support_score": [100.0, 0.0, 50.0],
        }
    )


def test_product_a_config_matches_authoritative_weights() -> None:
    config = ProductAScoringConfig.from_yaml()

    assert config.version == "product_a_scores_v1"
    assert config.metrics_version == "product_a_metrics_v1"
    assert config.h1b_history.weights == {
        "volume": 0.45,
        "consistency": 0.25,
        "recency": 0.15,
        "breadth": 0.10,
        "uscis_initial_approvals": 0.05,
    }
    assert config.green_card_history.weights == {
        "volume": 0.45,
        "consistency": 0.25,
        "recency": 0.15,
        "breadth": 0.15,
    }
    assert config.overall_sponsorship.weights == {
        "h1b_history": 0.40,
        "green_card_history": 0.60,
    }


def test_star_mapping_is_whole_star_accessible_and_never_zero_star() -> None:
    config = ProductAScoringConfig.from_yaml()
    values = pl.DataFrame({"score": [100.0, 80.0, 79.999, 65.0, 45.0, 25.0, 0.1, 0.0, None]})

    mapped = values.with_columns(_product_a_star_rating(pl.col("score"), config).alias("rating"))

    assert mapped["rating"].to_list() == [5, 5, 4, 4, 3, 2, 1, None, None]


def test_zero_observed_history_is_distinct_from_unrated() -> None:
    scored = score_employers_product_a(_employers(), ProductAScoringConfig.from_yaml())
    zero = scored.filter(pl.col("organization_id") == "zero")
    unrated = scored.filter(pl.col("organization_id") == "unrated")

    assert zero["h1b_history_score"].item() == 0
    assert zero["h1b_history_status"].item() == "NO_OBSERVED_HISTORY"
    assert zero["h1b_history_star_rating"].item() is None
    assert zero["h1b_history_stars"].item() == "No observed technical H-1B history"
    assert zero["green_card_history_stars"].item() == "No observed technical PERM history"
    assert zero["overall_sponsorship_stars"].item() == ("No observed technical sponsorship history")
    assert unrated["h1b_history_score"].item() is None
    assert unrated["h1b_history_status"].item() == "UNRATED"
    assert unrated["h1b_history_stars"].item() == "Unrated"
    assert unrated["overall_sponsorship_score"].item() is None


def test_overall_is_exact_40_60_and_excludes_supplemental_evidence() -> None:
    config = ProductAScoringConfig.from_yaml()
    baseline = score_employers_product_a(_employers(), config)
    changed_supplemental = score_employers_product_a(
        _employers().with_columns(
            pl.lit("CONFIRMED_INACTIVE").alias("everify_status"),
            pl.lit("UNKNOWN").alias("known_opt_observation"),
            pl.lit(-999.0).alias("policy_support_score"),
        ),
        config,
    )
    observed = baseline.filter(pl.col("organization_id") == "observed")

    assert observed["overall_sponsorship_score"].item() == pytest.approx(
        observed["h1b_history_score"].item() * 0.4
        + observed["green_card_history_score"].item() * 0.6,
        abs=0.01,
    )
    assert baseline.select(
        "organization_id",
        "h1b_history_score",
        "green_card_history_score",
        "overall_sponsorship_score",
    ).equals(
        changed_supplemental.select(
            "organization_id",
            "h1b_history_score",
            "green_card_history_score",
            "overall_sponsorship_score",
        )
    )
    assert "out of 5 stars" in observed["overall_sponsorship_star_label"].item()
    assert observed["score_version"].item() == "product_a_scores_v1"
    assert observed["h1b_volume_p95_cap"].item() == 100


def test_parent_rollups_do_not_duplicate_the_legal_entity_cap_population() -> None:
    config = ProductAScoringConfig.from_yaml()
    legal_entities = _employers()
    parent = legal_entities.head(1).with_columns(
        pl.lit("parent-rollup").alias("organization_id"),
        pl.lit("PARENT_ROLLUP").alias("identity_scope"),
        pl.lit(1_000_000.0).alias("weighted_relevant_lca_count"),
        pl.lit(1_000_000.0).alias("weighted_relevant_perm_count"),
        pl.lit(1_000_000).alias("initial_approvals"),
    )

    scored = score_employers_product_a(
        pl.concat([legal_entities, parent], how="vertical_relaxed"), config
    )

    assert scored["h1b_volume_p95_cap"].unique().to_list() == [100.0]
    assert scored["green_card_volume_p95_cap"].unique().to_list() == [50.0]
    assert scored["uscis_initial_approvals_p95_cap"].unique().to_list() == [80.0]


def test_missing_uscis_is_not_converted_to_zero_and_dol_remains_rateable() -> None:
    config = ProductAScoringConfig.from_yaml()
    no_uscis = _employers().with_columns(pl.lit(False).alias("uscis_source_valid"))
    scored = score_employers_product_a(no_uscis, config)
    observed = scored.filter(pl.col("organization_id") == "observed")

    assert observed["h1b_history_status"].item() == "RATED"
    assert observed["h1b_history_coverage"].item() == 0.95


def test_research_scale_uses_herd_context_without_changing_sponsorship() -> None:
    config = ProductAScoringConfig.from_yaml()
    institutions = (
        _employers()
        .head(2)
        .with_columns(
            pl.Series("institution_id", ["ipeds:1", "ipeds:2"]),
            pl.Series("computing_rd", [100.0, 10.0]),
            pl.Series("engineering_rd", [500.0, 50.0]),
            pl.Series("total_rd", [1_000.0, 100.0]),
            pl.Series("has_computing_rd_data", [True, True]),
            pl.Series("has_engineering_rd_data", [True, True]),
            pl.Series("has_total_rd_data", [True, True]),
        )
    )
    scored = score_institutions_product_a(institutions, config)
    changed_herd = score_institutions_product_a(
        institutions.with_columns(
            pl.Series("computing_rd", [1.0, 1_000_000.0]),
            pl.Series("engineering_rd", [1.0, 1_000_000.0]),
            pl.Series("total_rd", [1.0, 1_000_000.0]),
        ),
        config,
    )

    assert scored["research_scale_status"].to_list() == ["RATED", "RATED"]
    assert scored["research_scale_score"].to_list() == [100.0, 50.0]
    assert scored.select("institution_id", "h1b_history_score", "overall_sponsorship_score").equals(
        changed_herd.select("institution_id", "h1b_history_score", "overall_sponsorship_score")
    )
    assert (
        scored["research_scale_explanation"]
        .str.contains("does not affect sponsorship ratings")
        .all()
    )
