"""Gold-set metrics for the deterministic role classifier."""

from __future__ import annotations

import csv
from pathlib import Path

from sponsor_intel.role_classification.classifier import RoleClassifier
from sponsor_intel.role_classification.models import (
    RoleTaxonomyConfig,
    RoleValidationResult,
)
from sponsor_intel.sources.manifests import write_json_atomic


def _boolean(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid gold boolean value: {value!r}")


def validate_role_gold(
    gold_path: Path,
    config: RoleTaxonomyConfig,
    *,
    report_path: Path | None = None,
) -> RoleValidationResult:
    with gold_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    required = {
        "source_id",
        "fiscal_year",
        "employer_type",
        "job_title_raw",
        "soc_code",
        "expected_technical",
        "expected_family",
        "expected_review",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Role gold set is empty or missing columns: {sorted(required)}")

    classifier = RoleClassifier(config)
    true_positive = false_positive = false_negative = 0
    expected_family_count = correct_family_count = 0
    expected_review_count = routed_review_count = 0
    for row in rows:
        expected = _boolean(row["expected_technical"])
        expected_review = _boolean(row["expected_review"])
        result = classifier.classify(row["job_title_raw"], row["soc_code"])
        if result.technical_role is True and expected is True:
            true_positive += 1
        elif result.technical_role is True and expected is not True:
            false_positive += 1
        elif expected is True and result.technical_role is not True:
            false_negative += 1
        if expected is True:
            expected_family_count += 1
            if result.role_family == row["expected_family"]:
                correct_family_count += 1
        if expected_review is True:
            expected_review_count += 1
            if result.review_status == "NEEDS_REVIEW":
                routed_review_count += 1

    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0
    )
    family_accuracy = correct_family_count / expected_family_count if expected_family_count else 0
    routed_rate = routed_review_count / expected_review_count if expected_review_count else 1.0
    years = {row["fiscal_year"] for row in rows}
    employer_types = {row["employer_type"] for row in rows}
    result = RoleValidationResult(
        row_count=len(rows),
        source_year_count=len(years),
        employer_type_count=len(employer_types),
        true_positive_count=true_positive,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        precision=precision,
        recall=recall,
        family_accuracy=family_accuracy,
        low_confidence_routed_rate=routed_rate,
        passed=(
            len(rows) >= 750
            and len(years) >= 5
            and len(employer_types) >= 6
            and precision >= 0.95
            and recall >= 0.90
            and family_accuracy >= 0.90
            and routed_rate == 1.0
        ),
    )
    if report_path is not None:
        write_json_atomic(report_path, result.model_dump(mode="json"))
    return result
