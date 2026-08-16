"""Unit coverage for versioned nullable Phase 8 evidence scores."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
import yaml

from sponsor_intel.scoring import (
    ScoringConfig,
    ScoringV2Config,
    score_employers,
    score_employers_v2,
    score_institutions,
    score_institutions_v2,
)


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


def _v2_institutions(history: pl.DataFrame, institution_ids: list[str]) -> pl.DataFrame:
    row_count = len(institution_ids)
    return history.head(row_count).with_columns(
        pl.Series("institution_id", institution_ids),
        pl.Series("ipeds_unitid", [str(index + 1) for index in range(row_count)]),
        pl.Series("active_status", ["ACTIVE"] * row_count),
        pl.Series("legal_entity_id", [f"legal:{index + 1}" for index in range(row_count)]),
        pl.Series("organization_id", [f"org:{index + 1}" for index in range(row_count)]),
        pl.Series("total_rd", [500_000_000 - index * 10_000_000 for index in range(row_count)]),
        pl.Series("federal_rd", [300_000_000 - index * 10_000_000 for index in range(row_count)]),
        pl.Series("computing_rd", [50_000_000 - index * 1_000_000 for index in range(row_count)]),
        pl.Series(
            "engineering_rd", [100_000_000 - index * 1_000_000 for index in range(row_count)]
        ),
        pl.Series("has_total_rd_data", [True] * row_count),
        pl.Series("has_federal_rd_data", [True] * row_count),
        pl.Series("has_computing_rd_data", [True] * row_count),
        pl.Series("has_engineering_rd_data", [True] * row_count),
    )


def _policy_facts(rows: list[tuple[str, str, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "institution_id": [row[0] for row in rows],
            "fact_type": [row[1] for row in rows],
            "fact_value": [row[2] for row in rows],
            "human_review_status": [row[3] for row in rows],
            "exact_excerpt_verified": [row[2] != "NOT_STATED" for row in rows],
            "is_current": [True] * len(rows),
            "valid_to": [None] * len(rows),
            "source_url": ["https://one.example.edu/policy"] * len(rows),
            "valid_from": ["2026-08-15T00:00:00+00:00"] * len(rows),
        }
    )


def test_v2_config_and_sponsorship_history_are_versioned_and_everify_independent() -> None:
    config = ScoringV2Config.from_yaml()
    assert config.version == "evidence_scores_v2_2026_08"
    assert config.sponsorship_history.weights == {
        "h1b_history": 0.4,
        "green_card_history": 0.6,
    }
    assert config.research_pathway.weights == {
        "sponsorship_history": 0.5,
        "policy_support": 0.3,
        "research_strength": 0.2,
    }

    histories = pl.concat([_history_rows().head(1), _history_rows().head(1)]).with_columns(
        pl.Series("organization_id", ["active", "unknown"]),
        pl.Series("everify_status", ["CONFIRMED_ACTIVE", "UNKNOWN"]),
        pl.Series("known_opt_observation", ["OBSERVED_POSITIVE", "UNKNOWN"]),
    )
    scored = score_employers_v2(histories, config)
    assert scored["sponsorship_history_score"].n_unique() == 1
    assert scored["sponsorship_history_status"].to_list() == ["COMPLETE", "COMPLETE"]
    assert scored["sponsorship_history_grade"].null_count() == 0
    assert scored["score_version"].unique().to_list() == [config.version]


def test_v2_partial_scores_expose_expected_weight_coverage_without_a_grade() -> None:
    config = ScoringV2Config.from_yaml()
    h1b_only = (
        _history_rows()
        .head(1)
        .with_columns(
            pl.lit(0).alias("perm_case_count"),
            pl.lit(0).alias("relevant_certified_perm_count"),
            pl.lit(0).alias("perm_active_years"),
            pl.lit(None, dtype=pl.Int64).alias("last_perm_activity_year"),
            pl.lit(None, dtype=pl.Int64).alias("top_perm_technical_title_count"),
        )
    )
    scored = score_employers_v2(h1b_only, config)
    assert scored["sponsorship_history_score"].item() == scored["h1b_history_score"].item()
    assert scored["sponsorship_history_coverage"].item() == 0.4
    assert scored["sponsorship_history_status"].item() == "PARTIAL"
    assert scored["sponsorship_history_grade"].item() is None
    assert scored["green_card_history_score"].item() is None

    lca_only = h1b_only.with_columns(
        pl.lit(0).alias("uscis_employer_year_rows"),
        pl.lit(0).alias("initial_approvals"),
        pl.lit(0).alias("initial_denials"),
        pl.lit(0).alias("uscis_active_years"),
        pl.lit(None, dtype=pl.Int64).alias("last_uscis_activity_year"),
    )
    partial_h1b = score_employers_v2(lca_only, config)
    assert partial_h1b["h1b_history_status"].item() == "PARTIAL"
    assert partial_h1b["h1b_history_grade"].item() is None


def test_v2_core_policy_review_and_evidence_coverage_remain_distinct() -> None:
    config = ScoringV2Config.from_yaml()
    institutions = _v2_institutions(_history_rows(), ["ipeds:1", "ipeds:2"])
    facts = _policy_facts(
        [
            ("ipeds:1", "h1b_research_staff_eligible", "YES", "REVIEWED_ACCEPTED"),
            ("ipeds:1", "pr_research_staff_eligible", "NOT_STATED", "REVIEWED_NOT_STATED"),
            ("ipeds:1", "perm_supported", "NO", "REVIEWED_ACCEPTED"),
            ("ipeds:1", "eb1b_supported", "YES", "NEEDS_REVIEW"),
        ]
    )
    scored = score_institutions_v2(institutions, facts, config)
    reviewed = scored.filter(pl.col("institution_id") == "ipeds:1")
    missing = scored.filter(pl.col("institution_id") == "ipeds:2")

    assert reviewed["core_policy_review_coverage"].item() == 0.75
    assert reviewed["core_policy_evidence_coverage"].item() == 0.5
    assert reviewed["pr_research_staff_eligible_review_state"].item() == ("REVIEWED_NOT_STATED")
    assert reviewed["eb1b_supported_review_state"].item() == "REVIEW_PENDING"
    assert reviewed["research_pathway_status"].item() == "INCOMPLETE_EVIDENCE"
    assert reviewed["research_pathway_grade"].item() is None
    assert missing["core_policy_review_coverage"].item() == 0
    assert missing["core_policy_evidence_coverage"].item() == 0


def test_v2_research_pathway_grade_gate_and_reviewed_pr_blocker() -> None:
    config = ScoringV2Config.from_yaml()
    histories = pl.concat([_history_rows().head(1), _history_rows().head(1)]).with_columns(
        pl.Series("organization_id", ["complete", "blocked"])
    )
    institutions = _v2_institutions(histories, ["ipeds:complete", "ipeds:blocked"])
    fact_types = [
        "h1b_research_staff_eligible",
        "pr_research_staff_eligible",
        "perm_supported",
        "eb1b_supported",
    ]
    facts = _policy_facts(
        [
            (
                institution_id,
                fact_type,
                "NO"
                if institution_id.endswith("blocked") and fact_type == "pr_research_staff_eligible"
                else "YES",
                "REVIEWED_ACCEPTED",
            )
            for institution_id in ("ipeds:complete", "ipeds:blocked")
            for fact_type in fact_types
        ]
    )
    scored = score_institutions_v2(institutions, facts, config)
    complete = scored.filter(pl.col("institution_id") == "ipeds:complete")
    blocked = scored.filter(pl.col("institution_id") == "ipeds:blocked")

    assert complete["research_pathway_status"].item() == "COMPLETE"
    assert complete["research_pathway_grade"].item() is not None
    assert complete["decision_readiness_evidence_tier"].item() == "TIER_1_REVIEWED"
    assert complete["decision_readiness_prerequisite_status"].item() == ("PENDING_QUALITY_GATE")
    assert complete["decision_readiness_tier_is_final"].item() is False
    assert blocked["research_pathway_status"].item() == "POLICY_BLOCKED"
    assert blocked["research_pathway_evidence_score"].item() is not None
    assert blocked["research_pathway_score"].item() is None
    assert blocked["research_pathway_grade"].item() is None


def test_v2_decision_readiness_evidence_tiers_are_deterministic() -> None:
    config = ScoringV2Config.from_yaml()
    complete = _history_rows().head(1)
    tier2 = complete.with_columns(pl.lit("tier2").alias("organization_id"))
    tier3 = complete.with_columns(
        pl.lit("tier3").alias("organization_id"),
        pl.lit(0).alias("perm_case_count"),
        pl.lit(0).alias("relevant_certified_perm_count"),
        pl.lit(0).alias("perm_active_years"),
        pl.lit(None, dtype=pl.Int64).alias("last_perm_activity_year"),
        pl.lit(None, dtype=pl.Int64).alias("top_perm_technical_title_count"),
    )
    tier4 = _history_rows().filter(pl.col("organization_id") == "missing")
    histories = pl.concat([complete, tier2, tier3, tier4], how="vertical_relaxed")
    institutions = _v2_institutions(
        histories,
        ["ipeds:tier1", "ipeds:tier2", "ipeds:tier3", "ipeds:tier4"],
    )
    facts = _policy_facts(
        [
            ("ipeds:tier1", fact_type, "YES", "REVIEWED_ACCEPTED")
            for fact_type in config.core_policy.required_fact_types
        ]
    )

    scored = score_institutions_v2(institutions, facts, config)
    assert scored["decision_readiness_evidence_tier"].to_list() == [
        "TIER_1_REVIEWED",
        "TIER_2_STRONG_HISTORY_POLICY_INCOMPLETE",
        "TIER_3_PARTIAL_HISTORY",
        "TIER_4_INSUFFICIENT_EVIDENCE",
    ]
    assert (
        scored["decision_readiness_tier"].to_list()
        == scored["decision_readiness_evidence_tier"].to_list()
    )
