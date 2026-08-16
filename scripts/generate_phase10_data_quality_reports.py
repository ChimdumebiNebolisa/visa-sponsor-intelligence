"""Generate reproducible Phase 10 entity and role-classification audit packets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from sponsor_intel.entity_resolution.normalization import normalize_state
from sponsor_intel.role_classification.classifier import RoleClassifier
from sponsor_intel.role_classification.models import RoleTaxonomyConfig

_CLASSIFICATION_FIELDS = (
    "technical_role",
    "role_family",
    "role_confidence",
    "classification_method",
    "classification_rule",
    "review_status",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_path_label(path: Path) -> str:
    """Keep repository-relative inputs useful without publishing machine-local temp paths."""

    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def _query_rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: Iterable[object] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, list(parameters))
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(fields) + " |"
    divider = "| " + " | ".join("---" for _ in fields) + " |"
    body = [
        "| " + " | ".join(_markdown_value(row.get(field)) for field in fields) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def _entity_candidates(
    connection: duckdb.DuckDBPyConnection,
    *,
    company_count: int,
    institution_count: int,
) -> list[dict[str, Any]]:
    companies = _query_rows(
        connection,
        """
        SELECT
            'COMPANY_OR_OTHER_EMPLOYER' AS audit_category,
            organization_id AS legal_entity_id,
            organization_name AS selection_name,
            coalesce(relevant_lca_count, 0) AS relevant_lca_count,
            coalesce(relevant_certified_perm_count, 0) AS relevant_perm_count,
            coalesce(initial_approvals, 0) + coalesce(continuing_approvals, 0)
                AS uscis_approval_count,
            0::BIGINT AS research_spending
        FROM employer_metrics
        WHERE identity_scope = 'LEGAL_ENTITY'
          AND coalesce(is_higher_education, false) = false
        ORDER BY
            coalesce(relevant_lca_count, 0) +
                coalesce(relevant_certified_perm_count, 0) DESC,
            coalesce(initial_approvals, 0) + coalesce(continuing_approvals, 0) DESC,
            organization_name,
            organization_id
        LIMIT ?
        """,
        [company_count],
    )
    institutions = _query_rows(
        connection,
        """
        SELECT
            'UNIVERSITY_OR_RESEARCH_INSTITUTION' AS audit_category,
            legal_entity_id,
            canonical_name AS selection_name,
            coalesce(relevant_lca_count, 0) AS relevant_lca_count,
            coalesce(relevant_certified_perm_count, 0) AS relevant_perm_count,
            coalesce(initial_approvals, 0) + coalesce(continuing_approvals, 0)
                AS uscis_approval_count,
            coalesce(computing_rd, 0) + coalesce(engineering_rd, 0) AS research_spending
        FROM institution_metrics
        WHERE legal_entity_id IS NOT NULL
        ORDER BY
            coalesce(relevant_lca_count, 0) +
                coalesce(relevant_certified_perm_count, 0) DESC,
            coalesce(computing_rd, 0) + coalesce(engineering_rd, 0) DESC,
            canonical_name,
            legal_entity_id
        LIMIT ?
        """,
        [institution_count],
    )
    return [*companies, *institutions]


def _entity_audit_row(
    connection: duckdb.DuckDBPyConnection, candidate: dict[str, Any]
) -> dict[str, Any]:
    legal_rows = _query_rows(
        connection,
        """
        SELECT
            legal.legal_entity_id,
            legal.legal_name,
            legal.city AS legal_city,
            legal.state AS legal_state,
            legal.created_by,
            legal.review_status AS legal_review_status,
            parent.canonical_name AS parent_organization
        FROM legal_entities AS legal
        LEFT JOIN parent_organizations AS parent
          ON parent.parent_organization_id = legal.parent_organization_id
        WHERE legal.legal_entity_id = ?
        """,
        [candidate["legal_entity_id"]],
    )
    if len(legal_rows) != 1:
        raise ValueError(
            f"Expected one legal entity for {candidate['legal_entity_id']}, found {len(legal_rows)}"
        )
    legal = legal_rows[0]
    aliases = _query_rows(
        connection,
        """
        SELECT
            alias_raw,
            source_id,
            city,
            state,
            occurrence_count,
            match_status,
            match_score,
            candidate_legal_entity_id,
            reviewed_by,
            reviewed_at
        FROM entity_aliases
        WHERE legal_entity_id = ?
        ORDER BY occurrence_count DESC, source_id, alias_raw, state, city
        """,
        [candidate["legal_entity_id"]],
    )
    legal_state = normalize_state(legal.get("legal_state"))
    legal_city = str(legal.get("legal_city") or "").strip().upper()
    conflict_count = 0
    conflict_rows = 0
    for alias in aliases:
        alias_state = normalize_state(alias.get("state"))
        alias_city = str(alias.get("city") or "").strip().upper()
        state_conflict = bool(alias_state and legal_state and alias_state != legal_state)
        city_conflict = bool(
            alias_state
            and legal_state
            and alias_state == legal_state
            and alias_city
            and legal_city
            and alias_city != legal_city
        )
        if state_conflict or city_conflict:
            conflict_count += 1
            conflict_rows += int(alias.get("occurrence_count") or 0)

    statuses = sorted({str(alias["match_status"]) for alias in aliases})
    scores = [float(alias["match_score"]) for alias in aliases if alias["match_score"] is not None]
    needs_review = any(status in {"REVIEW_REQUIRED", "UNRESOLVED"} for status in statuses)
    if conflict_count:
        confidence = "LOCATION_CONFLICT_REVIEW_REQUIRED"
    elif needs_review:
        confidence = "REVIEW_REQUIRED"
    elif "MANUAL_OVERRIDE" in statuses:
        confidence = "REVIEWED_OVERRIDE_PRESENT"
    elif statuses and set(statuses) <= {"DETERMINISTIC", "HIGH_CONFIDENCE_AUTO"}:
        confidence = "HIGH"
    else:
        confidence = "MIXED_OR_UNKNOWN"

    observed_names = list(dict.fromkeys(str(alias["alias_raw"]) for alias in aliases))
    observed_locations = list(
        dict.fromkeys(
            f"{alias['city'] or ''}, {alias['state'] or ''}".strip(" ,") for alias in aliases
        )
    )
    reviewed_by = sorted(
        {str(alias["reviewed_by"]) for alias in aliases if alias.get("reviewed_by")}
    )
    return {
        **candidate,
        "resolved_legal_entity": legal["legal_name"],
        "parent_organization": legal.get("parent_organization") or "",
        "observed_source_names": " | ".join(observed_names),
        "observed_legal_employer_locations": " | ".join(observed_locations),
        "source_ids": " | ".join(sorted({str(alias["source_id"]) for alias in aliases})),
        "match_statuses": " | ".join(statuses),
        "minimum_match_score": round(min(scores), 6) if scores else "",
        "location_conflict_observations": conflict_count,
        "location_conflict_source_rows": conflict_rows,
        "resolution_confidence": confidence,
        "human_override_required": "YES" if conflict_count or needs_review else "NO_EVIDENT_NEED",
        "existing_reviewers": " | ".join(reviewed_by),
        "audit_review_status": "PENDING_HUMAN_REVIEW",
        "reviewer": "",
        "reviewer_note": "",
    }


def generate_entity_audit(
    database_path: Path,
    output_directory: Path,
    *,
    company_count: int,
    institution_count: int,
) -> dict[str, Any]:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        candidates = _entity_candidates(
            connection,
            company_count=company_count,
            institution_count=institution_count,
        )
        rows = [_entity_audit_row(connection, candidate) for candidate in candidates]

    fields = [
        "audit_category",
        "legal_entity_id",
        "selection_name",
        "resolved_legal_entity",
        "parent_organization",
        "observed_source_names",
        "observed_legal_employer_locations",
        "source_ids",
        "match_statuses",
        "minimum_match_score",
        "location_conflict_observations",
        "location_conflict_source_rows",
        "resolution_confidence",
        "human_override_required",
        "existing_reviewers",
        "audit_review_status",
        "reviewer",
        "reviewer_note",
        "relevant_lca_count",
        "relevant_perm_count",
        "uscis_approval_count",
        "research_spending",
    ]
    csv_path = output_directory / "entity-audit.csv"
    markdown_path = output_directory / "entity-audit.md"
    _write_csv(csv_path, rows, fields)
    pending_count = sum(row["audit_review_status"] == "PENDING_HUMAN_REVIEW" for row in rows)
    override_count = sum(row["human_override_required"] == "YES" for row in rows)
    compact_fields = [
        "audit_category",
        "resolved_legal_entity",
        "parent_organization",
        "resolution_confidence",
        "human_override_required",
        "audit_review_status",
    ]
    markdown_path.write_text(
        "\n".join(
            [
                "# Phase 10 entity-resolution audit packet",
                "",
                f"Generated from `{database_path}` with SHA-256 `{_sha256(database_path)}`.",
                "",
                f"- Companies or other significant employers: {company_count}",
                f"- Universities or research institutions: {institution_count}",
                f"- Rows with an evident unresolved location/match issue: {override_count}",
                f"- Rows still requiring human review: {pending_count}",
                "",
                "This is a deterministic review packet, not a completed human audit. "
                "No uncertain parent mapping or location conflict was auto-approved.",
                "",
                _markdown_table(rows, compact_fields),
                "",
                "The complete alias, location, score, and reviewer fields are in "
                f"`{csv_path.name}`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "row_count": len(rows),
        "company_count": sum(row["audit_category"] == "COMPANY_OR_OTHER_EMPLOYER" for row in rows),
        "institution_count": sum(
            row["audit_category"] == "UNIVERSITY_OR_RESEARCH_INSTITUTION" for row in rows
        ),
        "human_override_required_count": override_count,
        "pending_human_review_count": pending_count,
        "csv_path": str(csv_path),
        "markdown_path": str(markdown_path),
    }


def _technical_label(value: object) -> str:
    if value is True:
        return "TECHNICAL"
    if value is False:
        return "NOT_RELEVANT"
    return "AMBIGUOUS"


def _classification_snapshot() -> dict[str, Any]:
    return {
        "record_count": 0,
        "technical_status_counts": Counter(),
        "family_counts": Counter(),
        "method_counts": Counter(),
    }


def _add_classification(
    snapshot: dict[str, Any], classification: dict[str, Any], occurrence_count: int
) -> None:
    snapshot["record_count"] += occurrence_count
    snapshot["technical_status_counts"][_technical_label(classification["technical_role"])] += (
        occurrence_count
    )
    snapshot["family_counts"][str(classification["role_family"])] += occurrence_count
    snapshot["method_counts"][str(classification["classification_method"])] += occurrence_count


def _serializable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_count": snapshot["record_count"],
        "technical_status_counts": dict(sorted(snapshot["technical_status_counts"].items())),
        "family_counts": dict(sorted(snapshot["family_counts"].items())),
        "method_counts": dict(sorted(snapshot["method_counts"].items())),
    }


def generate_classification_report(
    baseline_path: Path,
    output_directory: Path,
    *,
    sample_per_transition: int,
) -> dict[str, Any]:
    baseline = pl.read_parquet(baseline_path)
    required = {
        "source_id",
        "job_title_raw",
        "soc_code_raw",
        "occurrence_count",
        "classification_version",
        *_CLASSIFICATION_FIELDS,
    }
    missing = required - set(baseline.columns)
    if missing:
        raise ValueError(f"Baseline role classifications are missing columns: {sorted(missing)}")

    baseline_versions = sorted(str(item) for item in baseline["classification_version"].unique())
    classifier = RoleClassifier(RoleTaxonomyConfig.from_yaml())
    if classifier.config.classification_version in baseline_versions:
        raise ValueError(
            "Baseline classifications already use the current taxonomy version; "
            "restore the quality-approved V1 role_classifications.parquet first."
        )

    before = _classification_snapshot()
    after = _classification_snapshot()
    transitions: Counter[tuple[str, str]] = Counter()
    transition_combinations: Counter[tuple[str, str]] = Counter()
    changed: list[dict[str, Any]] = []
    for row in baseline.iter_rows(named=True):
        occurrence_count = int(row["occurrence_count"])
        before_result = {field: row[field] for field in _CLASSIFICATION_FIELDS}
        after_model = classifier.classify(row["job_title_raw"], row["soc_code_raw"])
        after_result = after_model.model_dump()
        _add_classification(before, before_result, occurrence_count)
        _add_classification(after, after_result, occurrence_count)
        if all(before_result[field] == after_result[field] for field in _CLASSIFICATION_FIELDS):
            continue
        before_key = (
            f"{_technical_label(before_result['technical_role'])}:{before_result['role_family']}"
        )
        after_key = (
            f"{_technical_label(after_result['technical_role'])}:{after_result['role_family']}"
        )
        transitions[(before_key, after_key)] += occurrence_count
        transition_combinations[(before_key, after_key)] += 1
        changed.append(
            {
                "source_id": row["source_id"],
                "job_title_raw": row["job_title_raw"],
                "soc_code_raw": row["soc_code_raw"],
                "occurrence_count": occurrence_count,
                "before_version": row["classification_version"],
                "after_version": after_model.classification_version,
                "before_technical_role": before_result["technical_role"],
                "after_technical_role": after_result["technical_role"],
                "before_family": before_result["role_family"],
                "after_family": after_result["role_family"],
                "before_method": before_result["classification_method"],
                "after_method": after_result["classification_method"],
                "before_rule": before_result["classification_rule"],
                "after_rule": after_result["classification_rule"],
                "before_review_status": before_result["review_status"],
                "after_review_status": after_result["review_status"],
                "transition": f"{before_key} -> {after_key}",
            }
        )

    changed.sort(
        key=lambda row: (
            -int(row["occurrence_count"]),
            str(row["transition"]),
            str(row["job_title_raw"]),
            str(row["soc_code_raw"]),
            str(row["source_id"]),
        )
    )
    change_fields = (
        list(changed[0])
        if changed
        else [
            "source_id",
            "job_title_raw",
            "soc_code_raw",
            "occurrence_count",
            "transition",
        ]
    )
    changes_path = output_directory / "role-classification-changes.csv"
    _write_csv(changes_path, changed, change_fields)

    by_transition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in changed:
        if len(by_transition[str(row["transition"])]) < sample_per_transition:
            by_transition[str(row["transition"])].append(row)
    sample = [
        {
            **row,
            "inspection_status": "PENDING_MANUAL_INSPECTION",
            "reviewer": "",
            "reviewer_note": "",
        }
        for transition in sorted(by_transition)
        for row in by_transition[transition]
    ]
    sample_path = output_directory / "role-classification-review-sample.csv"
    sample_fields = list(sample[0]) if sample else [*change_fields, "inspection_status"]
    _write_csv(sample_path, sample, sample_fields)

    transition_rows = [
        {
            "before": key[0],
            "after": key[1],
            "changed_records": count,
            "changed_combinations": transition_combinations[key],
        }
        for key, count in sorted(transitions.items(), key=lambda item: (-item[1], item[0]))
    ]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_path": _portable_path_label(baseline_path),
        "baseline_sha256": _sha256(baseline_path),
        "baseline_versions": baseline_versions,
        "current_version": classifier.config.classification_version,
        "unique_combination_count": baseline.height,
        "semantic_changed_combination_count": len(changed),
        "semantic_changed_record_count": sum(int(row["occurrence_count"]) for row in changed),
        "before": _serializable_snapshot(before),
        "after": _serializable_snapshot(after),
        "transitions": transition_rows,
        "manual_sample_count": len(sample),
        "manual_sample_pending_count": len(sample),
        "changes_path": str(changes_path),
        "sample_path": str(sample_path),
    }
    json_path = output_directory / "role-classification-summary.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = output_directory / "role-classification-summary.md"
    status_rows = [
        {
            "status": status,
            "before": payload["before"]["technical_status_counts"].get(status, 0),
            "after": payload["after"]["technical_status_counts"].get(status, 0),
        }
        for status in ("TECHNICAL", "NOT_RELEVANT", "AMBIGUOUS")
    ]
    markdown_path.write_text(
        "\n".join(
            [
                "# Phase 10 role-classification change report",
                "",
                f"Baseline: `{payload['baseline_path']}` (`{', '.join(baseline_versions)}`), "
                f"SHA-256 `{payload['baseline_sha256']}`.",
                "",
                f"Candidate taxonomy: `{classifier.config.classification_version}`.",
                "",
                f"- Unique title/SOC combinations: {baseline.height:,}",
                f"- Semantically changed combinations: {len(changed):,}",
                f"- Record-weighted changed rows: {payload['semantic_changed_record_count']:,}",
                f"- Stratified sample awaiting manual inspection: {len(sample):,}",
                "",
                "## Before and after record counts",
                "",
                _markdown_table(status_rows, ["status", "before", "after"]),
                "",
                "## Largest classification transitions",
                "",
                _markdown_table(
                    transition_rows[:25], list(transition_rows[0]) if transition_rows else []
                ),
                "",
                "The complete changed-combination file and stratified inspection packet are "
                f"`{changes_path.name}` and `{sample_path.name}`. Pending rows are not represented "
                "as human-reviewed.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {**payload, "json_path": str(json_path), "markdown_path": str(markdown_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("db/immigration.duckdb"))
    parser.add_argument(
        "--baseline-role-classifications",
        type=Path,
        default=Path("data/processed/role_classifications.parquet"),
    )
    parser.add_argument("--output-directory", type=Path, default=Path("outputs/reports/phase10"))
    parser.add_argument("--company-count", type=int, default=30)
    parser.add_argument("--institution-count", type=int, default=30)
    parser.add_argument("--sample-per-transition", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.company_count < 30 or arguments.institution_count < 30:
        parser.error("Phase 10 requires at least 30 companies and 30 institutions")
    if arguments.sample_per_transition < 1:
        parser.error("--sample-per-transition must be positive")
    if not arguments.database.is_file():
        parser.error(f"Presentation database not found: {arguments.database}")
    if not arguments.baseline_role_classifications.is_file():
        parser.error(
            f"Baseline role classifications not found: {arguments.baseline_role_classifications}"
        )

    arguments.output_directory.mkdir(parents=True, exist_ok=True)
    entity = generate_entity_audit(
        arguments.database,
        arguments.output_directory,
        company_count=arguments.company_count,
        institution_count=arguments.institution_count,
    )
    classification = generate_classification_report(
        arguments.baseline_role_classifications,
        arguments.output_directory,
        sample_per_transition=arguments.sample_per_transition,
    )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "entity_audit": entity,
        "classification_change_report": {
            key: classification[key]
            for key in (
                "baseline_versions",
                "current_version",
                "unique_combination_count",
                "semantic_changed_combination_count",
                "semantic_changed_record_count",
                "manual_sample_count",
                "manual_sample_pending_count",
                "json_path",
                "markdown_path",
            )
        },
    }
    summary_path = arguments.output_directory / "data-quality-audit-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
