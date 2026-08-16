"""Fail-closed Product A handling for qualifying evidence on unresolved aliases."""

from __future__ import annotations

import polars as pl
import pytest

from sponsor_intel.metrics.pipeline import _unresolved_candidate_evidence_flags
from sponsor_intel.scoring import ProductAScoringConfig, score_employers_product_a


def test_missing_candidate_alias_schema_fails_closed() -> None:
    with pytest.raises(ValueError, match="refusing to validate zero sponsorship history"):
        _unresolved_candidate_evidence_flags(
            pl.DataFrame({"legal_entity_id": []}, schema={"legal_entity_id": pl.String}),
            pl.DataFrame(),
            pl.DataFrame(),
            pl.DataFrame(),
            pl.DataFrame(),
        )


def test_only_qualifying_dol_candidate_evidence_sets_program_specific_flags() -> None:
    aliases = pl.DataFrame(
        {
            "legal_entity_id": [
                "source_lca",
                "source_perm",
                "source_uscis",
                "source_nontechnical",
                "source_denied",
                "source_resolved",
            ],
            "candidate_legal_entity_id": [
                "target_h1b",
                "target_perm",
                "target_uscis",
                "target_nontechnical",
                "target_denied",
                "target_resolved",
            ],
            "source_id": [
                "dol_lca",
                "dol_perm",
                "uscis_h1b",
                "dol_lca",
                "dol_perm",
                "dol_lca",
            ],
            "match_status": ["REVIEW_REQUIRED"] * 5 + ["DETERMINISTIC"],
            "review_status": ["REVIEW_REQUIRED"] * 5 + ["DETERMINISTIC"],
        }
    )
    legal_entities = pl.DataFrame(
        {
            "legal_entity_id": [
                "target_h1b",
                "target_perm",
                "target_uscis",
                "target_nontechnical",
                "target_denied",
                "target_resolved",
            ],
            "parent_organization_id": [
                "parent_h1b",
                "parent_unreviewed",
                None,
                None,
                None,
                None,
            ],
            "review_status": ["DETERMINISTIC"] * 6,
        }
    )
    parents = pl.DataFrame(
        {
            "parent_organization_id": ["parent_h1b", "parent_unreviewed"],
            "review_status": ["MANUAL_OVERRIDE", "REVIEW_REQUIRED"],
        }
    )
    lca = pl.DataFrame(
        {
            "legal_entity_id": ["source_lca", "source_nontechnical", "source_resolved"],
            "technical_role": [True, False, True],
            "visa_class": ["H-1B", "H-1B", "H-1B"],
            "case_status": ["CERTIFIED", "CERTIFIED", "CERTIFIED"],
        }
    )
    perm = pl.DataFrame(
        {
            "legal_entity_id": ["source_perm", "source_denied"],
            "technical_role": [True, True],
            "case_status": ["CERTIFIED-EXPIRED", "DENIED"],
        }
    )

    flags = _unresolved_candidate_evidence_flags(
        aliases,
        legal_entities,
        parents,
        lca,
        perm,
    )

    assert flags.to_dicts() == [
        {
            "organization_id": "parent_h1b",
            "has_unresolved_h1b_candidate_evidence": True,
            "has_unresolved_perm_candidate_evidence": False,
        },
        {
            "organization_id": "target_h1b",
            "has_unresolved_h1b_candidate_evidence": True,
            "has_unresolved_perm_candidate_evidence": False,
        },
        {
            "organization_id": "target_perm",
            "has_unresolved_h1b_candidate_evidence": False,
            "has_unresolved_perm_candidate_evidence": True,
        },
    ]


def _zero_history_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "organization_id": ["h1b_candidate", "perm_candidate", "uscis_only", "resolved"],
            "identity_scope": ["LEGAL_ENTITY"] * 4,
            "entity_resolution_valid": [True] * 4,
            "h1b_entity_resolution_valid": [False, True, True, True],
            "perm_entity_resolution_valid": [True, False, True, True],
            "has_unresolved_h1b_candidate_evidence": [True, False, False, False],
            "has_unresolved_perm_candidate_evidence": [False, True, False, False],
            "lca_source_valid": [True] * 4,
            "perm_source_valid": [True] * 4,
            "uscis_source_valid": [True] * 4,
            "weighted_relevant_lca_count": [0.0] * 4,
            "relevant_certified_lca_count": [0] * 4,
            "relevant_certified_withdrawn_lca_count": [0] * 4,
            "lca_complete_active_years": [0] * 4,
            "lca_relevant_job_family_count": [0] * 4,
            "last_relevant_lca_activity_year": [None] * 4,
            "weighted_relevant_perm_count": [0.0] * 4,
            "relevant_certified_perm_count": [0] * 4,
            "relevant_certified_expired_perm_count": [0] * 4,
            "perm_complete_active_years": [0] * 4,
            "perm_relevant_job_family_count": [0] * 4,
            "last_relevant_perm_activity_year": [None] * 4,
            "initial_approvals": [0, 0, 25, 0],
            "lca_complete_fiscal_year_count": [4] * 4,
            "perm_complete_fiscal_year_count": [4] * 4,
            "latest_complete_immigration_fiscal_year": [2025] * 4,
            "current_partial_immigration_fiscal_year": [2026] * 4,
        }
    )


def test_program_flags_make_only_the_affected_component_unrated() -> None:
    scored = score_employers_product_a(
        _zero_history_rows(),
        ProductAScoringConfig.from_yaml(),
    )
    rows = {row["organization_id"]: row for row in scored.to_dicts()}

    assert rows["h1b_candidate"]["h1b_history_status"] == "UNRATED"
    assert rows["h1b_candidate"]["green_card_history_status"] == "NO_OBSERVED_HISTORY"
    assert rows["h1b_candidate"]["overall_sponsorship_status"] == "UNRATED"
    assert "review-required employer alias" in rows["h1b_candidate"]["h1b_history_explanation"]

    assert rows["perm_candidate"]["h1b_history_status"] == "NO_OBSERVED_HISTORY"
    assert rows["perm_candidate"]["green_card_history_status"] == "UNRATED"
    assert rows["perm_candidate"]["overall_sponsorship_status"] == "UNRATED"
    assert (
        "review-required employer alias" in rows["perm_candidate"]["green_card_history_explanation"]
    )

    for organization_id in ("uscis_only", "resolved"):
        assert rows[organization_id]["h1b_history_status"] == "NO_OBSERVED_HISTORY"
        assert rows[organization_id]["green_card_history_status"] == "NO_OBSERVED_HISTORY"
        assert rows[organization_id]["overall_sponsorship_status"] == "NO_OBSERVED_HISTORY"
