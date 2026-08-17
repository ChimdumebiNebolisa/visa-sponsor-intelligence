"""Build deterministic publication gates from processed evidence and manifests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from sponsor_intel.quality.models import QualityCheck, QualityReport
from sponsor_intel.sources.manifests import write_json_atomic

REQUIRED_TABLE_COLUMNS = {
    "employer_metrics.parquet": {
        "organization_id",
        "metric_version",
        "score_version",
        "entity_coverage_state",
        "h1b_entity_coverage_state",
        "perm_entity_coverage_state",
        "h1b_history_score",
        "h1b_history_status",
        "h1b_history_coverage",
        "h1b_history_star_rating",
        "h1b_history_stars",
        "h1b_history_star_label",
        "h1b_history_explanation",
        "green_card_history_score",
        "green_card_history_status",
        "green_card_history_coverage",
        "green_card_history_star_rating",
        "green_card_history_stars",
        "green_card_history_star_label",
        "green_card_history_explanation",
        "overall_sponsorship_score",
        "overall_sponsorship_status",
        "overall_sponsorship_coverage",
        "overall_sponsorship_star_rating",
        "overall_sponsorship_stars",
        "overall_sponsorship_star_label",
        "overall_sponsorship_explanation",
    },
    "institution_metrics.parquet": {
        "institution_id",
        "metric_version",
        "score_version",
        "entity_coverage_state",
        "h1b_entity_coverage_state",
        "perm_entity_coverage_state",
        "h1b_history_score",
        "h1b_history_status",
        "h1b_history_coverage",
        "h1b_history_star_rating",
        "h1b_history_stars",
        "h1b_history_star_label",
        "h1b_history_explanation",
        "green_card_history_score",
        "green_card_history_status",
        "green_card_history_coverage",
        "green_card_history_star_rating",
        "green_card_history_stars",
        "green_card_history_star_label",
        "green_card_history_explanation",
        "overall_sponsorship_score",
        "overall_sponsorship_status",
        "overall_sponsorship_coverage",
        "overall_sponsorship_star_rating",
        "overall_sponsorship_stars",
        "overall_sponsorship_star_label",
        "overall_sponsorship_explanation",
        "research_scale_score",
        "research_scale_status",
        "research_scale_star_rating",
        "research_scale_stars",
        "research_scale_star_label",
        "research_scale_explanation",
    },
    "lca_cases_resolved.parquet": {"source_artifact_id", "case_id", "organization_id"},
    "perm_cases_resolved.parquet": {"source_artifact_id", "case_id", "organization_id"},
    "h1b_petitions_resolved.parquet": {
        "source_artifact_id",
        "source_row_number",
        "organization_id",
    },
    "data_health.parquet": {"source_id", "row_count", "freshness_warning"},
    "legal_entities.parquet": {"legal_entity_id", "parent_organization_id"},
    "parent_organizations.parquet": {"parent_organization_id"},
    "institutions.parquet": {"institution_id"},
    "source_artifacts.parquet": {
        "source_artifact_id",
        "source_id",
        "schema_version",
        "parser_version",
        "sha256",
        "validation_status",
    },
}

EXPECTED_METRIC_VERSION = "product_a_metrics_v1"
EXPECTED_SCORE_VERSION = "product_a_scores_v1"


def _write_parquet_atomic(frame: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        frame.write_parquet(temporary_path, compression="zstd", statistics=True)
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _unique_text(frame: pl.DataFrame, column: str) -> str | None:
    if column not in frame.columns:
        return None
    values = frame[column].drop_nulls().unique().to_list()
    return str(values[0]) if len(values) == 1 else None


def _frame_or_none(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.is_file() else None


def _product_a_metric_fingerprint(
    frame: pl.DataFrame,
    *,
    required_columns: set[str],
    identity_column: str,
) -> str:
    """Fingerprint only the active Product A contract, excluding supplemental columns."""

    selected_columns = sorted(required_columns & set(frame.columns))
    if not selected_columns:
        return hashlib.sha256(b"").hexdigest()
    selected = frame.select(selected_columns)
    if identity_column in selected_columns:
        selected = selected.sort(identity_column)
    buffer = io.BytesIO()
    selected.write_parquet(buffer, compression="zstd", statistics=True)
    return hashlib.sha256(buffer.getbuffer()).hexdigest()


def _manifest_metadata(
    path: Path, active_artifact_ids: set[str]
) -> tuple[int, int, int, list[str], list[str], list[str], str]:
    if not path.is_file() or not active_artifact_ids:
        return 0, 0, 0, [], sorted(active_artifact_ids), [], "UNAVAILABLE"
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    selected = [
        row for row in rows if str(row.get("source_artifact_id", "")) in active_artifact_ids
    ]
    selected_counts: dict[str, int] = {}
    for row in selected:
        artifact_id = str(row.get("source_artifact_id", ""))
        selected_counts[artifact_id] = selected_counts.get(artifact_id, 0) + 1
    missing = sorted(active_artifact_ids - set(selected_counts))
    duplicates = sorted(artifact_id for artifact_id, count in selected_counts.items() if count != 1)
    failed = sum(row.get("validation_status") == "FAILED" for row in selected)
    warnings = sum(row.get("validation_status") == "WARNING" for row in selected)
    versions = sorted(
        {
            f"{row.get('source_id')}:{row.get('schema_version')}:{row.get('parser_version')}"
            for row in selected
        }
    )
    canonical = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in sorted(selected, key=lambda row: str(row.get("source_artifact_id", "")))
    )
    selected_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return (
        len(selected),
        failed,
        warnings,
        versions,
        missing,
        duplicates,
        selected_sha256,
    )


def _schema_failures(root: Path, active_artifact_ids: set[str]) -> tuple[int, int, int]:
    report_count = 0
    missing_count = 0
    observed_artifact_ids: set[str] = set()
    if not root.is_dir() or not active_artifact_ids:
        return report_count, missing_count, len(active_artifact_ids)
    for path in root.rglob("*.json"):
        if path.stem not in active_artifact_ids:
            continue
        report_count += 1
        observed_artifact_ids.add(path.stem)
        values = json.loads(path.read_text(encoding="utf-8"))
        missing_count += len(values.get("missing_required_columns", []))
    missing_report_count = len(active_artifact_ids - observed_artifact_ids)
    return report_count, missing_count, missing_report_count


class QualityReporter:
    """Measure Product A build health and fail publication on critical violations."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root

    def build(self) -> QualityReport:
        processed = self.data_root / "processed"
        manifest_path = self.output_root / "manifests" / "source_artifacts.jsonl"
        schema_root = self.output_root / "reports" / "schema"
        generated_at = datetime.now(UTC)
        checks: list[QualityCheck] = []

        def add(
            check_id: str,
            category: str,
            passed: bool,
            *,
            critical: bool,
            value: float | None,
            threshold: str,
            details: str,
            warn_only: bool = False,
        ) -> None:
            status = "PASS" if passed else ("WARN" if warn_only else "FAIL")
            checks.append(
                QualityCheck(
                    check_id=check_id,
                    category=category,
                    status=status,
                    critical=critical,
                    value=value,
                    threshold=threshold,
                    details=details,
                )
            )

        missing_files = [
            name for name in REQUIRED_TABLE_COLUMNS if not (processed / name).is_file()
        ]
        add(
            "required_processed_tables",
            "outputs",
            not missing_files,
            critical=True,
            value=float(len(REQUIRED_TABLE_COLUMNS) - len(missing_files)),
            threshold=f"{len(REQUIRED_TABLE_COLUMNS)} required tables",
            details=(
                "All required processed tables exist." if not missing_files else str(missing_files)
            ),
        )

        missing_columns: dict[str, list[str]] = {}
        for name, required in REQUIRED_TABLE_COLUMNS.items():
            path = processed / name
            if not path.is_file():
                continue
            actual = set(pl.read_parquet_schema(path))
            missing = sorted(required - actual)
            if missing:
                missing_columns[name] = missing
        add(
            "required_columns",
            "schema",
            not missing_columns,
            critical=True,
            value=float(sum(map(len, missing_columns.values()))),
            threshold="0 missing columns",
            details=(
                "No required processed columns are missing."
                if not missing_columns
                else str(missing_columns)
            ),
        )

        source_artifacts = _frame_or_none(processed / "source_artifacts.parquet")
        active_artifact_values = (
            source_artifacts.get_column("source_artifact_id").drop_nulls().cast(pl.String).to_list()
            if source_artifacts is not None and "source_artifact_id" in source_artifacts.columns
            else []
        )
        active_artifact_ids = {value.strip() for value in active_artifact_values if value.strip()}
        invalid_active_id_count = (
            0
            if source_artifacts is not None
            and source_artifacts.height == len(active_artifact_values) == len(active_artifact_ids)
            else 1
        )
        (
            manifest_count,
            manifest_failures,
            manifest_warnings,
            schema_versions,
            missing_manifest_ids,
            duplicate_manifest_ids,
            manifest_sha256,
        ) = _manifest_metadata(manifest_path, active_artifact_ids)
        add(
            "source_manifest",
            "provenance",
            manifest_count > 0
            and invalid_active_id_count == 0
            and not missing_manifest_ids
            and not duplicate_manifest_ids,
            critical=True,
            value=float(manifest_count),
            threshold="every active artifact maps to exactly one validated manifest record",
            details=(
                f"Selected SHA-256 {manifest_sha256}; versions: {', '.join(schema_versions)}; "
                f"missing={missing_manifest_ids}; duplicates={duplicate_manifest_ids}."
            ),
        )
        add(
            "manifest_validations",
            "schema",
            manifest_failures == 0,
            critical=True,
            value=float(manifest_failures),
            threshold="0 failed manifest validations",
            details=f"{manifest_failures} of {manifest_count} source artifacts failed validation.",
        )
        add(
            "manifest_warnings",
            "schema",
            manifest_warnings == 0,
            critical=False,
            value=float(manifest_warnings),
            threshold="review every warning",
            details=f"{manifest_warnings} source artifacts retain validation warnings.",
            warn_only=True,
        )

        schema_report_count, schema_missing_count, missing_schema_report_count = _schema_failures(
            schema_root, active_artifact_ids
        )
        add(
            "schema_reports",
            "schema",
            schema_report_count > 0
            and schema_missing_count == 0
            and missing_schema_report_count == 0,
            critical=True,
            value=float(schema_missing_count + missing_schema_report_count),
            threshold="one active report per artifact and 0 missing required source columns",
            details="; ".join(
                [
                    f"{schema_report_count} active schema reports",
                    f"{missing_schema_report_count} missing reports",
                    f"{schema_missing_count} missing columns.",
                ]
            ),
        )

        lca = _frame_or_none(processed / "lca_cases_resolved.parquet")
        perm = _frame_or_none(processed / "perm_cases_resolved.parquet")
        uscis = _frame_or_none(processed / "h1b_petitions_resolved.parquet")
        duplicate_count = 0
        for frame, keys in (
            (lca, ["source_artifact_id", "case_id"]),
            (perm, ["source_artifact_id", "case_id"]),
            (uscis, ["source_artifact_id", "source_row_number"]),
        ):
            if frame is not None and set(keys).issubset(frame.columns):
                duplicate_count += frame.height - frame.unique(keys).height
        add(
            "duplicate_source_records",
            "records",
            duplicate_count == 0,
            critical=True,
            value=float(duplicate_count),
            threshold="0 duplicate source keys",
            details=f"{duplicate_count} duplicate processed source keys.",
        )

        source_frames = [frame for frame in (lca, perm, uscis) if frame is not None]
        resolved_rows = sum(
            frame["organization_id"].is_not_null().sum()
            for frame in source_frames
            if "organization_id" in frame.columns
        )
        source_rows = sum(frame.height for frame in source_frames)
        match_coverage = resolved_rows / source_rows if source_rows else 0.0
        add(
            "entity_match_coverage",
            "entity_resolution",
            match_coverage >= 0.95,
            critical=True,
            value=float(match_coverage),
            threshold=">= 95%",
            details=f"{resolved_rows:,} of {source_rows:,} processed rows have an organization ID.",
        )

        dol_frames = [frame for frame in (lca, perm) if frame is not None]
        classified_rows = sum(
            frame["technical_role"].is_not_null().sum()
            for frame in dol_frames
            if "technical_role" in frame.columns
        )
        dol_rows = sum(frame.height for frame in dol_frames)
        role_coverage = classified_rows / dol_rows if dol_rows else 0.0
        add(
            "role_classification_coverage",
            "roles",
            role_coverage >= 0.98,
            critical=True,
            value=float(role_coverage),
            threshold=">= 98%",
            details=f"{classified_rows:,} of {dol_rows:,} DOL rows have a non-ambiguous decision.",
        )

        legal_entities = _frame_or_none(processed / "legal_entities.parquet")
        scope_conflicts = 0
        if legal_entities is not None:
            scope_conflicts = legal_entities.filter(
                pl.col("parent_organization_id").is_not_null()
                & (pl.col("legal_entity_id") == pl.col("parent_organization_id"))
            ).height
        add(
            "legal_parent_separation",
            "entity_resolution",
            scope_conflicts == 0,
            critical=True,
            value=float(scope_conflicts),
            threshold="0 legal/parent ID collisions",
            details=f"{scope_conflicts} legal identities reuse a parent identifier.",
        )

        employer_metrics = _frame_or_none(processed / "employer_metrics.parquet")
        institution_metrics = _frame_or_none(processed / "institution_metrics.parquet")
        employer_metric_version = (
            _unique_text(employer_metrics, "metric_version")
            if employer_metrics is not None
            else None
        )
        employer_score_version = (
            _unique_text(employer_metrics, "score_version")
            if employer_metrics is not None
            else None
        )
        institution_metric_version = (
            _unique_text(institution_metrics, "metric_version")
            if institution_metrics is not None
            else None
        )
        institution_score_version = (
            _unique_text(institution_metrics, "score_version")
            if institution_metrics is not None
            else None
        )
        metric_version = (
            employer_metric_version
            if employer_metric_version == institution_metric_version
            else None
        )
        score_version = (
            employer_score_version if employer_score_version == institution_score_version else None
        )
        rating_contract_errors = 0
        rating_pairs = (
            ("h1b_history_score", "h1b_history_star_rating"),
            ("green_card_history_score", "green_card_history_star_rating"),
            ("overall_sponsorship_score", "overall_sponsorship_star_rating"),
        )
        for frame in (employer_metrics, institution_metrics):
            if frame is None:
                continue
            for score_column, stars_column in rating_pairs:
                if {score_column, stars_column} <= set(frame.columns):
                    rating_contract_errors += frame.filter(
                        (
                            pl.col(score_column).is_not_null()
                            & ~pl.col(score_column).is_between(0, 100)
                        )
                        | (
                            pl.col(stars_column).is_not_null()
                            & ~pl.col(stars_column).is_between(1, 5)
                        )
                        | (pl.col(score_column).is_null() & pl.col(stars_column).is_not_null())
                        | (pl.col(score_column).eq(0) & pl.col(stars_column).is_not_null())
                        | (pl.col(score_column).gt(0) & pl.col(stars_column).is_null())
                    ).height
            for coverage_column in (
                "h1b_history_coverage",
                "green_card_history_coverage",
                "overall_sponsorship_coverage",
            ):
                if coverage_column in frame.columns:
                    rating_contract_errors += frame.filter(
                        pl.col(coverage_column).is_not_null()
                        & ~pl.col(coverage_column).is_between(0, 1)
                    ).height
        if institution_metrics is not None and {
            "research_scale_score",
            "research_scale_star_rating",
        } <= set(institution_metrics.columns):
            rating_contract_errors += institution_metrics.filter(
                (
                    pl.col("research_scale_score").is_not_null()
                    & ~pl.col("research_scale_score").is_between(0, 100)
                )
                | (
                    pl.col("research_scale_star_rating").is_not_null()
                    & ~pl.col("research_scale_star_rating").is_between(1, 5)
                )
                | (
                    pl.col("research_scale_score").is_null()
                    & pl.col("research_scale_star_rating").is_not_null()
                )
            ).height
        add(
            "score_contract",
            "scoring",
            employer_metric_version == EXPECTED_METRIC_VERSION
            and institution_metric_version == EXPECTED_METRIC_VERSION
            and employer_score_version == EXPECTED_SCORE_VERSION
            and institution_score_version == EXPECTED_SCORE_VERSION
            and rating_contract_errors == 0,
            critical=True,
            value=float(rating_contract_errors),
            threshold="Product A versions and valid 0-100 score/1-5 star semantics",
            details="; ".join(
                [
                    f"employer metric={employer_metric_version}",
                    f"institution metric={institution_metric_version}",
                    f"employer score={employer_score_version}",
                    f"institution score={institution_score_version}",
                    f"rating contract errors={rating_contract_errors}.",
                ]
            ),
        )

        health = _frame_or_none(processed / "data_health.parquet")
        freshness_rows = health.height if health is not None else 0
        add(
            "source_freshness",
            "freshness",
            freshness_rows >= 5,
            critical=True,
            value=float(freshness_rows),
            threshold=">= 5 source health rows",
            details=f"{freshness_rows} source health rows retain counts, periods, and warnings.",
        )

        output_fingerprints: dict[str, str] = {}
        if employer_metrics is not None:
            output_fingerprints["employer_metrics.parquet"] = _product_a_metric_fingerprint(
                employer_metrics,
                required_columns=REQUIRED_TABLE_COLUMNS["employer_metrics.parquet"],
                identity_column="organization_id",
            )
        if institution_metrics is not None:
            output_fingerprints["institution_metrics.parquet"] = _product_a_metric_fingerprint(
                institution_metrics,
                required_columns=REQUIRED_TABLE_COLUMNS["institution_metrics.parquet"],
                identity_column="institution_id",
            )
        build_material = json.dumps(
            {
                "manifest_sha256": manifest_sha256,
                "metric_version": metric_version,
                "score_version": score_version,
                "source_rows": source_rows,
                "output_fingerprints": output_fingerprints,
            },
            sort_keys=True,
        ).encode()
        build_id = f"product-a-{hashlib.sha256(build_material).hexdigest()[:16]}"
        critical_failure_count = sum(check.critical and check.status == "FAIL" for check in checks)
        checks_path = processed / "quality_checks.parquet"
        report_path = self.output_root / "reports" / "quality" / "data_quality.json"
        metadata_path = self.output_root / "reports" / "quality" / "build_metadata.json"
        report = QualityReport(
            build_id=build_id,
            generated_at=generated_at,
            passed=critical_failure_count == 0,
            critical_failure_count=critical_failure_count,
            manifest_sha256=manifest_sha256,
            metric_version=metric_version,
            score_version=score_version,
            checks=tuple(checks),
            checks_path=checks_path,
            report_path=report_path,
            metadata_path=metadata_path,
        )
        check_rows = [
            check.model_dump() | {"build_id": build_id, "checked_at": generated_at.isoformat()}
            for check in checks
        ]
        _write_parquet_atomic(pl.DataFrame(check_rows), checks_path)
        report_json = report.model_dump(mode="json")
        write_json_atomic(report_path, report_json)
        write_json_atomic(
            metadata_path,
            {
                "build_id": build_id,
                "generated_at": report_json["generated_at"],
                "manifest_sha256": manifest_sha256,
                "metric_version": metric_version,
                "score_version": score_version,
                "output_fingerprints": output_fingerprints,
                "employer_count": employer_metrics.height if employer_metrics is not None else 0,
                "institution_count": (
                    institution_metrics.height if institution_metrics is not None else 0
                ),
                "quality_passed": report.passed,
            },
        )
        return report
