"""Shared quality gates for official CSV and ZIP-backed source adapters."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import polars as pl

from sponsor_intel.sources.downloader import ArtifactDownloader
from sponsor_intel.sources.errors import DataQualityError, DownloadError, SchemaDriftError
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.manifests import write_json_atomic
from sponsor_intel.sources.models import (
    ArtifactFingerprint,
    DiscoveryReport,
    DownloadedArtifact,
    IssueSeverity,
    NormalizedDataset,
    PersistedDataset,
    SourceArtifactCandidate,
    SourceConfig,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)
from sponsor_intel.sources.normalizer import normalize_column_name


@dataclass(frozen=True, slots=True)
class SchemaInspection:
    """Schema mapping and drift evidence for one source artifact."""

    original_columns: tuple[str, ...]
    normalized_columns: tuple[str, ...]
    column_mapping: dict[str, str]
    schema_diff_path: Path
    issues: tuple[ValidationIssue, ...]


def validation_status(issues: list[ValidationIssue]) -> ValidationStatus:
    if any(issue.severity is IssueSeverity.ERROR for issue in issues):
        return ValidationStatus.FAILED
    if issues:
        return ValidationStatus.WARNING
    return ValidationStatus.PASSED


def inspect_schema(
    config: SourceConfig,
    artifact: DownloadedArtifact,
    original_columns: tuple[str, ...],
    report_root: Path,
) -> SchemaInspection:
    normalized_columns = tuple(normalize_column_name(column) for column in original_columns)
    available = set(normalized_columns)
    all_aliases = config.required_columns | config.optional_columns
    column_mapping: dict[str, str] = {}
    missing_required: list[str] = []
    for logical_name, aliases in all_aliases.items():
        source_name = next(
            (
                normalize_column_name(alias)
                for alias in aliases
                if normalize_column_name(alias) in available
            ),
            None,
        )
        if source_name is None:
            if logical_name in config.required_columns:
                missing_required.append(logical_name)
            continue
        column_mapping[logical_name] = source_name

    schema_key = f"{artifact.candidate.variant}:fy{artifact.candidate.fiscal_year}"
    fingerprint = hashlib.sha256(("\n".join(normalized_columns) + "\n").encode("utf-8")).hexdigest()
    expected_fingerprint = config.expected_schema_fingerprints.get(schema_key)
    fingerprint_changed = fingerprint != expected_fingerprint
    known_columns = {
        normalize_column_name(alias) for aliases in all_aliases.values() for alias in aliases
    }
    unexpected_columns = sorted(available - known_columns)
    report_path = report_root / "schema" / config.id / f"{artifact.source_artifact_id}.json"
    write_json_atomic(
        report_path,
        {
            "source_artifact_id": artifact.source_artifact_id,
            "source_id": config.id,
            "fiscal_year": artifact.candidate.fiscal_year,
            "variant": artifact.candidate.variant,
            "schema_version": config.schema_version,
            "parser_version": config.parser_version,
            "schema_fingerprint": fingerprint,
            "expected_schema_fingerprint": expected_fingerprint,
            "schema_fingerprint_changed": fingerprint_changed,
            "original_columns": original_columns,
            "normalized_columns": normalized_columns,
            "logical_column_mapping": column_mapping,
            "missing_required_columns": missing_required,
            "unexpected_optional_columns": unexpected_columns,
        },
    )
    if missing_required:
        raise SchemaDriftError(
            f"{config.id} required logical columns are missing: {missing_required}; "
            f"see {report_path}"
        )

    issues: list[ValidationIssue] = []
    if fingerprint_changed:
        issues.append(
            ValidationIssue(
                severity=IssueSeverity.WARNING,
                category="source_schema_drift",
                message="Source schema differs from the committed official-layout baseline",
                details={
                    "schema_fingerprint": fingerprint,
                    "expected_schema_fingerprint": expected_fingerprint,
                    "unexpected_optional_columns": unexpected_columns,
                },
            )
        )
    return SchemaInspection(
        original_columns=original_columns,
        normalized_columns=normalized_columns,
        column_mapping=column_mapping,
        schema_diff_path=report_path,
        issues=tuple(issues),
    )


def record_validation_report(path: Path, validation: ValidationResult) -> None:
    """Attach durable validation findings to the artifact's schema evidence."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["validation_status"] = validation.status.value
    payload["validation_issues"] = [issue.model_dump(mode="json") for issue in validation.issues]
    write_json_atomic(path, payload)


def read_csv_artifact(
    artifact: DownloadedArtifact,
    *,
    member_name: str | None = None,
    separator: str = ",",
) -> pl.DataFrame:
    """Read an all-string CSV directly or from one explicit safe ZIP member."""

    if artifact.candidate.expected_format == "csv":
        return pl.read_csv(artifact.raw_path, infer_schema=False, separator=separator)
    if artifact.candidate.expected_format != "zip" or member_name is None:
        raise DownloadError(f"Unsupported tabular artifact: {artifact.raw_path.name}")
    member = PurePosixPath(member_name)
    if member.is_absolute() or ".." in member.parts:
        raise DownloadError(f"Unsafe requested ZIP member: {member_name}")
    try:
        with zipfile.ZipFile(artifact.raw_path) as archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
            if member_name not in names:
                raise SchemaDriftError(
                    f"Expected {member_name} in {artifact.raw_path.name}; found {sorted(names)}"
                )
            with archive.open(member_name) as source:
                return pl.read_csv(source, infer_schema=False, separator=separator)
    except zipfile.BadZipFile as error:
        raise DownloadError(f"Invalid ZIP archive: {artifact.raw_path.name}") from error


class TabularSourceAdapter:
    """Common immutable download, checksum validation, and Parquet persistence."""

    def __init__(
        self,
        config: SourceConfig,
        client: OfficialHttpClient,
        data_root: Path,
        output_root: Path,
    ) -> None:
        self.config = config
        self.client = client
        self.downloader = ArtifactDownloader(config, client, data_root / "raw")
        self.staging_root = data_root / "staging"
        self.report_root = output_root / "reports"
        self.last_discovery_report: DiscoveryReport | None = None

    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact:
        return self.downloader.download(candidate)

    def fingerprint(self, artifact: DownloadedArtifact) -> ArtifactFingerprint:
        return ArtifactFingerprint(sha256=artifact.sha256, byte_size=artifact.byte_size)

    def validate_raw(self, artifact: DownloadedArtifact) -> ValidationResult:
        issues: list[ValidationIssue] = []
        size_changed = (
            not artifact.raw_path.is_file()
            or artifact.raw_path.stat().st_size != artifact.byte_size
        )
        if size_changed:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="raw_size_mismatch",
                    message="Downloaded raw artifact is missing or changed size",
                )
            )
        else:
            hasher = hashlib.sha256()
            with artifact.raw_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != artifact.sha256:
                issues.append(
                    ValidationIssue(
                        severity=IssueSeverity.ERROR,
                        category="raw_checksum_mismatch",
                        message="Downloaded raw artifact checksum changed after validation",
                    )
                )
        return ValidationResult(status=validation_status(issues), issues=tuple(issues))

    def validate_normalized(self, dataset: NormalizedDataset) -> ValidationResult:
        return dataset.validation

    def persist(self, dataset: NormalizedDataset) -> PersistedDataset:
        if dataset.validation.status is ValidationStatus.FAILED:
            errors = [issue.message for issue in dataset.validation.issues]
            raise DataQualityError(f"Normalized data failed quality gates: {errors}")
        target_directory = (
            self.staging_root / self.config.id / f"fy={dataset.artifact.candidate.fiscal_year}"
        )
        target_directory.mkdir(parents=True, exist_ok=True)
        target_path = target_directory / f"{dataset.artifact.source_artifact_id}.parquet"
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{target_path.name}-", suffix=".tmp", dir=target_directory
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            dataset.frame.write_parquet(temporary_path, compression="zstd", statistics=True)
            os.replace(temporary_path, target_path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
        return PersistedDataset(
            artifact=dataset.artifact,
            parquet_path=target_path,
            raw_row_count=dataset.raw_row_count,
            row_count=dataset.frame.height,
            column_count=dataset.frame.width,
            schema_version=self.config.schema_version,
            parser_version=self.config.parser_version,
            validation=dataset.validation,
            schema_diff_path=dataset.schema_diff_path,
        )
