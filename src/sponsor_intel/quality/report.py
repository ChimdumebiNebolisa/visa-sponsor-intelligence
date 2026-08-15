"""Build deterministic publication gates from processed evidence and manifests."""

from __future__ import annotations

import hashlib
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
        "immigration_evidence_coverage",
    },
    "institution_metrics.parquet": {
        "institution_id",
        "metric_version",
        "score_version",
        "research_pathway_coverage",
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
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _manifest_metadata(path: Path) -> tuple[int, int, int, list[str]]:
    if not path.is_file():
        return 0, 0, 0, []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    failed = sum(row.get("validation_status") == "FAILED" for row in rows)
    warnings = sum(row.get("validation_status") == "WARNING" for row in rows)
    versions = sorted(
        {
            f"{row.get('source_id')}:{row.get('schema_version')}:{row.get('parser_version')}"
            for row in rows
        }
    )
    return len(rows), failed, warnings, versions


def _schema_failures(root: Path) -> tuple[int, int]:
    report_count = 0
    missing_count = 0
    if not root.is_dir():
        return report_count, missing_count
    for path in root.rglob("*.json"):
        report_count += 1
        values = json.loads(path.read_text(encoding="utf-8"))
        missing_count += len(values.get("missing_required_columns", []))
    return report_count, missing_count


class QualityReporter:
    """Measure V1 build health and fail publication on critical violations."""

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

        manifest_count, manifest_failures, manifest_warnings, schema_versions = _manifest_metadata(
            manifest_path
        )
        manifest_sha256 = _sha256(manifest_path) if manifest_path.is_file() else "UNAVAILABLE"
        add(
            "source_manifest",
            "provenance",
            manifest_count > 0,
            critical=True,
            value=float(manifest_count),
            threshold="> 0 validated source artifacts",
            details=f"SHA-256 {manifest_sha256}; versions: {', '.join(schema_versions)}",
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

        schema_report_count, schema_missing_count = _schema_failures(schema_root)
        add(
            "schema_reports",
            "schema",
            schema_report_count > 0 and schema_missing_count == 0,
            critical=True,
            value=float(schema_missing_count),
            threshold="reports present and 0 missing required source columns",
            details="; ".join(
                [
                    f"{schema_report_count} schema reports",
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
        facts = _frame_or_none(processed / "policy_facts.parquet")
        accepted = facts.head(0) if facts is not None else None
        if facts is not None:
            accepted = facts.filter(pl.col("human_review_status") == "REVIEWED_ACCEPTED")
        institution_ids = (
            institution_metrics["institution_id"].drop_nulls().unique().to_list()
            if institution_metrics is not None
            else []
        )
        reviewed_institutions = (
            accepted.filter(pl.col("institution_id").is_in(institution_ids))[
                "institution_id"
            ].n_unique()
            if accepted is not None
            else 0
        )
        invalid_accepted = 0
        if accepted is not None and not accepted.is_empty():
            invalid_accepted = accepted.filter(
                ~pl.col("exact_excerpt_verified").fill_null(False)
                | ~pl.col("source_url").fill_null("").str.starts_with("https://")
                | pl.col("supporting_excerpt").fill_null("").str.strip_chars().eq("")
            ).height
        add(
            "reviewed_policy_coverage",
            "policy",
            reviewed_institutions >= 100,
            critical=True,
            value=float(reviewed_institutions),
            threshold=">= 100 institutions",
            details=f"{reviewed_institutions} institutions have accepted policy evidence.",
        )
        add(
            "accepted_policy_evidence",
            "policy",
            invalid_accepted == 0,
            critical=True,
            value=float(invalid_accepted),
            threshold="0 accepted facts without exact official evidence",
            details=f"{invalid_accepted} accepted facts fail URL/excerpt gates.",
        )

        metric_version = (
            _unique_text(employer_metrics, "metric_version")
            if employer_metrics is not None
            else None
        )
        score_version = (
            _unique_text(employer_metrics, "score_version")
            if employer_metrics is not None
            else None
        )
        coverage_errors = 0
        for frame in (employer_metrics, institution_metrics):
            if frame is None:
                continue
            for column in (name for name in frame.columns if name.endswith("_coverage")):
                coverage_errors += frame.filter(
                    pl.col(column).is_not_null() & ~pl.col(column).is_between(0, 1)
                ).height
        add(
            "score_contract",
            "scoring",
            metric_version is not None and score_version is not None and coverage_errors == 0,
            critical=True,
            value=float(coverage_errors),
            threshold="one metric/score version and all coverage in [0, 1]",
            details="; ".join(
                [
                    f"metric={metric_version}",
                    f"score={score_version}",
                    f"range errors={coverage_errors}.",
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

        output_fingerprints = {
            name: _sha256(processed / name)
            for name in (
                "employer_metrics.parquet",
                "institution_metrics.parquet",
                "policy_facts.parquet",
            )
            if (processed / name).is_file()
        }
        build_material = json.dumps(
            {
                "manifest_sha256": manifest_sha256,
                "metric_version": metric_version,
                "score_version": score_version,
                "source_rows": source_rows,
                "reviewed_institutions": reviewed_institutions,
                "output_fingerprints": output_fingerprints,
            },
            sort_keys=True,
        ).encode()
        build_id = f"v1-{hashlib.sha256(build_material).hexdigest()[:16]}"
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
        write_json_atomic(report_path, report.model_dump(mode="json"))
        write_json_atomic(
            metadata_path,
            {
                "build_id": build_id,
                "generated_at": generated_at.isoformat(),
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
