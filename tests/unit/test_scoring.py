"""Unit coverage for versioned nullable Phase 8 evidence scores."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import yaml

from sponsor_intel.scoring import ScoringConfig, score_employers, score_institutions


def _history_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "organization_id": ["complete", "missing", "inactive"],
            "everify_status": ["CONFIRMED_ACTIVE", "UNKNOWN", "CONFIRMED_INACTIVE"],
            "known_opt_observation": ["OBSERVED_POSITIVE", "UNKNOWN", "UNKNOWN"],
            "lca_case_count": [200, 0, 20],
            "relevant_lca_count": [150, 0, 10],
            "lca_active_years": [5, 0, 3],
            "last_lca_activity_year": [2026, None, 2025],
            "uscis_employer_year_rows": [5, 0, 3],
            "initial_approvals": [90, 0, 18],
            "initial_denials": [10, 0, 2],
            "uscis_active_years": [5, 0, 3],
            "last_uscis_activity_year": [2026, None, 2025],
            "perm_case_count": [50, 0, 10],
            "relevant_certified_perm_count": [30, 0, 4],
            "perm_active_years": [5, 0, 2],
            "last_perm_activity_year": [2025, None, 2024],
            "top_perm_technical_title_count": [12, None, 2],
        }
    )


def test_scoring_config_is_versioned_and_rejects_invalid_weights(tmp_path: Path) -> None:
    config = ScoringConfig.from_yaml()
    assert config.version == "evidence_scores_v1_2026_08"

    values = yaml.safe_load(Path("configs/scoring.yaml").read_text(encoding="utf-8"))
    values["h1b_history"]["weights"]["recency"] = 0.25
    invalid = tmp_path / "invalid-scoring.yaml"
    invalid.write_text(yaml.safe_dump(values), encoding="utf-8")
    with pytest.raises(ValueError, match=r"weights must sum to 1\.0"):
        ScoringConfig.from_yaml(invalid)


def test_employer_scores_preserve_missing_evidence_and_explicit_blockers() -> None:
    config = ScoringConfig.from_yaml()
    scored = score_employers(_history_rows(), config)
    complete = scored.filter(pl.col("organization_id") == "complete")
    missing = scored.filter(pl.col("organization_id") == "missing")
    inactive = scored.filter(pl.col("organization_id") == "inactive")

    assert complete["stem_opt_readiness_score"].item() == 100
    assert complete["immigration_evidence_score"].item() is not None
    assert complete["immigration_evidence_coverage"].item() == 1
    assert complete["score_version"].item() == config.version
    assert missing["stem_opt_readiness_score"].item() is None
    assert missing["h1b_history_score"].item() is None
    assert missing["green_card_history_score"].item() is None
    assert missing["immigration_evidence_score"].item() is None
    assert missing["immigration_evidence_grade"].item() == "UNKNOWN"
    assert inactive["stem_opt_readiness_score"].item() == 0
    assert inactive["stem_opt_readiness_status"].item() == "EXPLICIT_BLOCKER"


def test_institution_scores_expose_research_and_policy_coverage() -> None:
    config = ScoringConfig.from_yaml()
    institutions = (
        _history_rows()
        .head(2)
        .with_columns(
            pl.Series("institution_id", ["ipeds:1", "ipeds:2"]),
            pl.Series("total_rd", [500_000_000, 0]),
            pl.Series("federal_rd", [300_000_000, 0]),
            pl.Series("computing_rd", [0, 0]),
            pl.Series("engineering_rd", [0, 0]),
            pl.Series("has_total_rd_data", [True, False]),
            pl.Series("has_federal_rd_data", [True, False]),
            pl.Series("has_computing_rd_data", [False, False]),
            pl.Series("has_engineering_rd_data", [False, False]),
        )
    )
    facts = pl.DataFrame(
        {
            "institution_id": ["ipeds:1", "ipeds:2"],
            "fact_type": ["h1b_research_staff_eligible", "h1b_research_staff_eligible"],
            "fact_value": ["YES", "YES"],
            "human_review_status": ["REVIEWED_ACCEPTED", "NEEDS_REVIEW"],
            "exact_excerpt_verified": [True, True],
            "is_current": [True, True],
            "valid_to": [None, None],
            "source_url": ["https://one.example.edu/policy", "https://two.example.edu/policy"],
            "valid_from": ["2026-08-15T00:00:00+00:00"] * 2,
        }
    )

    scored = score_institutions(institutions, facts, config)
    complete = scored.filter(pl.col("institution_id") == "ipeds:1")
    missing = scored.filter(pl.col("institution_id") == "ipeds:2")

    assert complete["research_strength_score"].item() == 100
    assert complete["research_strength_coverage"].item() == 0.55
    assert complete["policy_support_score"].item() == 100
    assert complete["policy_support_coverage"].item() == 0.18
    assert complete["research_pathway_score"].item() is not None
    assert complete["research_pathway_coverage"].item() == 0.6415
    assert missing["research_strength_score"].item() is None
    assert missing["policy_support_score"].item() is None
    assert missing["research_pathway_score"].item() is None

    replayed = score_institutions(institutions, facts, config)
    assert scored.equals(replayed)
