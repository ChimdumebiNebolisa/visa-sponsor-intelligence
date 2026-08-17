"""Product A qualifying-evidence and identity-scope aggregation tests."""

from __future__ import annotations

import polars as pl
import pytest

from sponsor_intel.case_status import canonical_case_status
from sponsor_intel.metrics.pipeline import (
    _latest_herd_context,
    _product_a_program_metrics,
    _validate_herd_institution_year_grain,
)


def _lca_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "organization_id": ["legal:a"] * 6 + ["legal:b"],
            "legal_entity_id": ["legal:a"] * 6 + ["legal:b"],
            "parent_organization_id": ["parent:p"] * 7,
            "case_status": [
                "CERTIFIED",
                "Certified - Withdrawn",
                "CERTIFIED",
                "CERTIFIED",
                "DENIED",
                "CERTIFIED",
                "CERTIFIED",
            ],
            "visa_class": ["H-1B", "H-1B", "H-1B1", "E-3", "H-1B", "H-1B", "H-1B"],
            "technical_role": [True, True, True, True, True, False, True],
            "fiscal_year": [2025, 2026, 2025, 2025, 2025, 2025, 2025],
            "fiscal_quarter": [4, 2, 4, 4, 4, 4, 4],
            "is_partial_period": [False, True, False, False, False, False, False],
            "role_family": [
                "software_engineering",
                "machine_learning",
                "software_engineering",
                "software_engineering",
                "software_engineering",
                "faculty",
                "data_engineering",
            ],
            "job_title_raw": [
                "Software Engineer",
                "Machine Learning Engineer",
                "Software Engineer",
                "Software Engineer",
                "Software Engineer",
                "Assistant Professor",
                "Data Engineer",
            ],
            "worksite_state": ["CA", "WA", "CA", "CA", "CA", "IL", "TX"],
        }
    )


def test_h1b_metrics_exclude_other_visas_unsuccessful_and_nontechnical_rows() -> None:
    metrics = _product_a_program_metrics(_lca_rows(), program="lca")
    legal = metrics.filter(pl.col("organization_id") == "legal:a")

    assert legal["lca_case_count"].item() == 6
    assert legal["relevant_certified_lca_count"].item() == 1
    assert legal["relevant_certified_withdrawn_lca_count"].item() == 1
    assert legal["weighted_relevant_lca_count"].item() == 1.5
    assert legal["lca_complete_active_years"].item() == 1
    assert legal["lca_active_years"].item() == 2
    assert legal["last_relevant_lca_activity_year"].item() == 2026
    assert legal["lca_relevant_job_family_count"].item() == 2


def test_parent_rollup_is_additive_without_discarding_legal_entities() -> None:
    metrics = _product_a_program_metrics(_lca_rows(), program="lca")
    parent = metrics.filter(pl.col("organization_id") == "parent:p")
    legal_a = metrics.filter(pl.col("organization_id") == "legal:a")
    legal_b = metrics.filter(pl.col("organization_id") == "legal:b")

    assert legal_a.height == legal_b.height == parent.height == 1
    assert legal_a["weighted_relevant_lca_count"].item() == 1.5
    assert legal_b["weighted_relevant_lca_count"].item() == 1
    assert parent["weighted_relevant_lca_count"].item() == 2.5
    assert parent["relevant_certified_lca_count"].item() == 2


def test_perm_certified_expired_is_half_weight_and_unsuccessful_is_zero() -> None:
    frame = (
        _lca_rows()
        .head(4)
        .with_columns(
            pl.Series(
                "case_status",
                ["CERTIFIED", "Certified - Expired", "DENIED", "WITHDRAWN"],
            ),
            pl.lit(True).alias("technical_role"),
            pl.Series("fiscal_year", [2025, 2026, 2025, 2025]),
            pl.Series("is_partial_period", [False, True, False, False]),
        )
    )

    metrics = _product_a_program_metrics(frame, program="perm")
    legal = metrics.filter(pl.col("organization_id") == "legal:a")

    assert legal["relevant_certified_perm_count"].item() == 1
    assert legal["relevant_certified_expired_perm_count"].item() == 1
    assert legal["weighted_relevant_perm_count"].item() == 1.5
    assert legal["perm_complete_active_years"].item() == 1
    assert legal["last_relevant_perm_activity_year"].item() == 2026


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("Certified - Withdrawn", "CERTIFIED-WITHDRAWN"),
        (" certified-withdrawn ", "CERTIFIED-WITHDRAWN"),
        ("CeRtIfIeD- ExPiReD", "CERTIFIED-EXPIRED"),
        ("Certified - Expired", "CERTIFIED-EXPIRED"),
        ("Withdrawn", "WITHDRAWN"),
        ("Denied", "DENIED"),
        ("Certified Withdrawn", "CERTIFIED WITHDRAWN"),
        ("Not Certified - Withdrawn", "NOT CERTIFIED-WITHDRAWN"),
    ],
)
def test_case_status_normalization_only_folds_case_and_hyphen_spacing(
    raw_status: str, expected: str
) -> None:
    normalized = pl.DataFrame({"case_status": [raw_status]}).select(
        canonical_case_status().alias("normalized")
    )

    assert normalized["normalized"].item() == expected


@pytest.mark.parametrize(
    ("program", "secondary", "half_column"),
    [
        ("lca", "withdrawn", "relevant_certified_withdrawn_lca_count"),
        ("perm", "expired", "relevant_certified_expired_perm_count"),
    ],
)
def test_program_metrics_accept_only_exact_positive_status_variants(
    program: str, secondary: str, half_column: str
) -> None:
    statuses = [
        " certified ",
        f"certified-{secondary}",
        f"Certified - {secondary.title()}",
        "WITHDRAWN",
        "DENIED",
        f"CERTIFIED {secondary.upper()}",
        f"NOT CERTIFIED - {secondary.upper()}",
    ]
    base = _lca_rows().head(1)
    frame = pl.concat([base] * len(statuses)).with_columns(
        pl.Series("case_status", statuses),
        pl.lit("H-1B").alias("visa_class"),
        pl.lit(True).alias("technical_role"),
        pl.lit(False).alias("is_partial_period"),
    )

    metrics = _product_a_program_metrics(frame, program=program)
    legal = metrics.filter(pl.col("organization_id") == "legal:a")

    assert legal[half_column].item() == 2
    assert legal[f"weighted_relevant_{program}_count"].item() == 2.0
    assert legal[f"{program}_active_years"].item() == 1


def test_research_scale_context_uses_one_shared_latest_herd_year() -> None:
    herd = pl.DataFrame(
        {
            "institution_id": ["older-only", "current", "current"],
            "survey_year": [2023, 2023, 2024],
            "total_rd": [1_000.0, 500.0, 100.0],
            "federal_rd": [800.0, 400.0, 80.0],
            "computing_rd": [700.0, 300.0, 70.0],
            "engineering_rd": [600.0, 200.0, 60.0],
            "rd_personnel": [50, 40, 30],
            "survey_form": ["standard", "standard", "standard"],
        }
    )

    latest = _latest_herd_context(herd)

    assert latest["survey_year"].unique().to_list() == [2024]
    assert latest["institution_id"].to_list() == ["current"]


def test_herd_grain_allows_distinct_unmatched_rows_in_the_same_year() -> None:
    herd = pl.DataFrame(
        {
            "inst_id": ["herd:one", "herd:two", "herd:matched"],
            "institution_id": [None, None, "ipeds:100001"],
            "survey_year": [2024, 2024, 2024],
            "survey_form": ["standard", "short", "standard"],
        }
    )

    _validate_herd_institution_year_grain(herd)


def test_herd_grain_rejects_a_true_matched_institution_year_duplicate() -> None:
    herd = pl.DataFrame(
        {
            "inst_id": ["herd:full", "herd:short"],
            "institution_id": ["ipeds:100001", "ipeds:100001"],
            "survey_year": [2024, 2024],
            "survey_form": ["standard", "short"],
        }
    )

    with pytest.raises(ValueError, match="duplicate institution-year rows"):
        _validate_herd_institution_year_grain(herd)
