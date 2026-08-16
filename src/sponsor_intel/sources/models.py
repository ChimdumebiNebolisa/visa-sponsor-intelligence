"""Typed contracts shared by source adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IssueSeverity(StrEnum):
    """Data-quality issue severity."""

    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationStatus(StrEnum):
    """Overall validation result."""

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class SourceConfig(BaseModel):
    """Validated source-registry entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    adapter: str
    authority: str
    landing_page: str
    minimum_fiscal_year: int = Field(ge=2022)
    refresh_cadence: str
    expected_formats: tuple[str, ...]
    official_domains: tuple[str, ...]
    partial_year_supported: bool
    parser_version: str
    schema_version: str
    artifact_url: str | None = None
    record_layout_url: str | None = None
    published_through_fiscal_year: int | None = Field(default=None, ge=2022)
    published_through_quarter: int | None = Field(default=None, ge=1, le=4)
    minimum_row_count: int = Field(ge=1)
    max_download_bytes: int = Field(gt=0)
    max_uncompressed_bytes: int = Field(gt=0)
    required_columns: dict[str, tuple[str, ...]]
    optional_columns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    allowed_missing_columns: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    expected_schema_fingerprints: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_boundaries(self) -> SourceConfig:
        parsed = urlparse(self.landing_page)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Source landing pages must use HTTPS")
        if not self.official_domains:
            raise ValueError("At least one official domain is required")
        if not self.required_columns:
            raise ValueError("At least one required logical column is required")
        for label, value in (
            ("artifact_url", self.artifact_url),
            ("record_layout_url", self.record_layout_url),
        ):
            if value is None:
                continue
            direct = urlparse(value)
            if direct.scheme != "https" or not direct.hostname:
                raise ValueError(f"{label} must use HTTPS")
            hostname = direct.hostname.casefold().rstrip(".")
            allowed = any(
                hostname == domain.casefold().removeprefix("*.").rstrip(".")
                or hostname.endswith(f".{domain.casefold().removeprefix('*.').rstrip('.')}")
                for domain in self.official_domains
            )
            if not allowed:
                raise ValueError(f"{label} must use a configured official domain")
        return self


class SourceContext(BaseModel):
    """Discovery/ingestion parameters shared across adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_fiscal_year: int = Field(default=2022, ge=2022)


class SourceArtifactCandidate(BaseModel):
    """Official artifact discovered from a source landing page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    authority: str
    landing_page_url: str
    download_url: str
    fiscal_year: int = Field(ge=2022)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    is_partial_period: bool
    is_quarter_partition: bool = False
    coverage_start_quarter: int | None = Field(default=None, ge=1, le=4)
    file_name: str
    expected_format: str
    variant: str = "standard"
    record_layout_url: str | None = None

    @property
    def candidate_id(self) -> str:
        payload = (
            f"{self.source_id}|{self.download_url}|{self.fiscal_year}|"
            f"{self.fiscal_quarter}|{self.variant}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class DiscoveryReport(BaseModel):
    """All matching links plus the canonical ingest selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    discovered_at: datetime
    from_fiscal_year: int
    landing_page_url: str
    candidates: tuple[SourceArtifactCandidate, ...]
    selected_candidate_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def selected(self) -> tuple[SourceArtifactCandidate, ...]:
        selected_ids = set(self.selected_candidate_ids)
        return tuple(
            candidate for candidate in self.candidates if candidate.candidate_id in selected_ids
        )


class DownloadedArtifact(BaseModel):
    """Validated immutable raw artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: SourceArtifactCandidate
    raw_path: Path
    retrieved_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)
    mime_type: str
    etag: str | None = None
    last_modified: str | None = None
    cache_hit: bool = False

    @property
    def source_artifact_id(self) -> str:
        payload = f"{self.candidate.source_id}|{self.candidate.download_url}|{self.sha256}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class ArtifactFingerprint(BaseModel):
    """Content identity for an immutable source artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str
    byte_size: int


class ValidationIssue(BaseModel):
    """Machine-readable validation finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: IssueSeverity
    category: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Validation status and its supporting findings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ValidationStatus
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(slots=True)
class NormalizedDataset:
    """In-memory normalized dataset pending persistence."""

    artifact: DownloadedArtifact
    frame: pl.DataFrame
    raw_row_count: int
    original_columns: tuple[str, ...]
    normalized_columns: tuple[str, ...]
    column_mapping: dict[str, str]
    validation: ValidationResult
    schema_diff_path: Path


class PersistedDataset(BaseModel):
    """Normalized Parquet artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: DownloadedArtifact
    parquet_path: Path
    raw_row_count: int = Field(ge=0)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    schema_version: str
    parser_version: str
    validation: ValidationResult
    schema_diff_path: Path


class ArtifactManifestRecord(BaseModel):
    """Complete provenance record for a persisted source artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_artifact_id: str
    source_id: str
    authority: str
    landing_page_url: str
    download_url: str
    retrieved_at: datetime
    fiscal_year: int
    fiscal_quarter: int | None
    is_partial_period: bool
    is_quarter_partition: bool = False
    coverage_start_quarter: int | None = Field(default=None, ge=1, le=4)
    file_name: str
    mime_type: str
    byte_size: int
    sha256: str
    record_layout_url: str | None
    parser_version: str
    schema_version: str
    raw_row_count: int | None = Field(default=None, ge=0)
    row_count: int
    column_count: int
    validation_status: ValidationStatus
    build_id: str
    raw_path: Path
    parquet_path: Path
    schema_diff_path: Path
    etag: str | None = None
    last_modified: str | None = None


class RawArtifactManifestRecord(BaseModel):
    """Provenance recorded immediately after an immutable raw download."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_artifact_id: str
    source_id: str
    authority: str
    landing_page_url: str
    download_url: str
    retrieved_at: datetime
    fiscal_year: int
    fiscal_quarter: int | None
    is_partial_period: bool
    is_quarter_partition: bool = False
    coverage_start_quarter: int | None = Field(default=None, ge=1, le=4)
    file_name: str
    mime_type: str
    byte_size: int
    sha256: str
    record_layout_url: str | None
    raw_path: Path
    etag: str | None = None
    last_modified: str | None = None


class IngestionSummary(BaseModel):
    """One source build summary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_id: str
    source_id: str
    selected_artifact_count: int
    ingested_artifact_count: int
    reused_artifact_count: int
    row_count: int
    manifest_path: Path
    discovery_report_path: Path
    records: tuple[ArtifactManifestRecord, ...]
