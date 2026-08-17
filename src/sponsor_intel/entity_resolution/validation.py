"""Gold-set validation for conservative legal-entity matching."""

from __future__ import annotations

import csv
from pathlib import Path

from sponsor_intel.entity_resolution.models import (
    EntityResolutionConfig,
    GoldValidationResult,
)
from sponsor_intel.entity_resolution.normalization import (
    core_name,
    legal_suffix,
    normalize_city,
    normalize_name,
    normalize_state,
)
from sponsor_intel.entity_resolution.resolver import score_pair
from sponsor_intel.sources.manifests import write_json_atomic

_MINIMUM_CATEGORY_COUNTS = {
    "tech": 50,
    "universities": 50,
    "systems": 25,
    "hospitals_medical": 25,
    "research_labs": 25,
    "staffing_consulting": 25,
}


def _truth(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid boolean value in gold set: {value!r}")


def validate_gold_dataset(
    gold_path: Path,
    config: EntityResolutionConfig,
    *,
    report_path: Path | None = None,
) -> GoldValidationResult:
    """Measure auto-match precision and parent/legal safeguards on a gold CSV."""

    with gold_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    required = {
        "category",
        "left_name",
        "right_name",
        "left_city",
        "right_city",
        "left_state",
        "right_state",
        "expected_match",
        "ambiguous",
        "parent_legal_pair",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Gold set is empty or missing columns: {sorted(required)}")

    category_counts: dict[str, int] = {}
    auto_accepted = 0
    correct_auto = 0
    false_auto = 0
    parent_legal_collapses = 0
    ambiguous_routed = 0
    ambiguous_total = 0
    for row in rows:
        category = row["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
        expected_match = _truth(row["expected_match"])
        ambiguous = _truth(row["ambiguous"])
        parent_legal_pair = _truth(row["parent_legal_pair"])
        left = normalize_name(row["left_name"], config)
        right = normalize_name(row["right_name"], config)
        left_city = normalize_city(row["left_city"])
        right_city = normalize_city(row["right_city"])
        left_state = normalize_state(row["left_state"])
        right_state = normalize_state(row["right_state"])
        exact = bool(left and left == right)
        features = score_pair(
            core_name(left, config),
            core_name(right, config),
            left_city=left_city,
            right_city=right_city,
            left_state=left_state,
            right_state=right_state,
        )
        state_conflict = bool(left_state and right_state and left_state != right_state)
        city_conflict = bool(
            not state_conflict
            and left_state
            and right_state
            and left_state == right_state
            and left_city
            and right_city
            and left_city != right_city
        )
        exact_with_compatible_location = exact and not (state_conflict or city_conflict)
        fuzzy_auto = (
            features.score >= config.high_confidence_threshold
            and features.location_agreement
            and not (
                legal_suffix(left, config)
                and legal_suffix(right, config)
                and legal_suffix(left, config) != legal_suffix(right, config)
            )
        )
        accepted = exact_with_compatible_location or fuzzy_auto
        if accepted:
            auto_accepted += 1
            if expected_match:
                correct_auto += 1
            else:
                false_auto += 1
        if parent_legal_pair and accepted:
            parent_legal_collapses += 1
        if ambiguous:
            ambiguous_total += 1
            if not accepted:
                ambiguous_routed += 1

    missing = {
        category: minimum - category_counts.get(category, 0)
        for category, minimum in _MINIMUM_CATEGORY_COUNTS.items()
        if category_counts.get(category, 0) < minimum
    }
    auto_precision = correct_auto / auto_accepted if auto_accepted else 0.0
    passed = (
        not missing
        and auto_precision >= 0.99
        and false_auto == 0
        and parent_legal_collapses == 0
        and ambiguous_routed == ambiguous_total
    )
    result = GoldValidationResult(
        row_count=len(rows),
        category_counts=category_counts,
        auto_accepted_count=auto_accepted,
        auto_precision=auto_precision,
        false_auto_accept_count=false_auto,
        parent_legal_collapse_count=parent_legal_collapses,
        ambiguous_routed_count=ambiguous_routed,
        ambiguous_total_count=ambiguous_total,
        passed=passed,
    )
    if report_path is not None:
        payload = result.model_dump(mode="json")
        payload["minimum_category_counts"] = _MINIMUM_CATEGORY_COUNTS
        payload["missing_category_rows"] = missing
        write_json_atomic(report_path, payload)
    return result
