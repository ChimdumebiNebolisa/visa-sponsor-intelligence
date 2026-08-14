"""Shared DOL disclosure adapter implementation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sponsor_intel.sources.discovery import discover_dol_artifacts
from sponsor_intel.sources.downloader import ArtifactDownloader
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.models import (
    ArtifactFingerprint,
    DiscoveryReport,
    DownloadedArtifact,
    IssueSeverity,
    NormalizedDataset,
    PersistedDataset,
    SourceArtifactCandidate,
    SourceConfig,
    SourceContext,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)
from sponsor_intel.sources.normalizer import DolExcelNormalizer


class DolDisclosureAdapter:
    """Full source-adapter contract for one DOL disclosure program."""

    expected_source_id: str

    def __init__(
        self,
        config: SourceConfig,
        client: OfficialHttpClient,
        data_root: Path,
        output_root: Path,
    ) -> None:
        if config.id != self.expected_source_id:
            raise ValueError(
                f"{type(self).__name__} requires {self.expected_source_id}, received {config.id}"
            )
        self.config = config
        self.client = client
        self.downloader = ArtifactDownloader(config, client, data_root / "raw")
        self.normalizer = DolExcelNormalizer(
            config,
            staging_root=data_root / "staging",
            report_root=output_root / "reports",
        )
        self.last_discovery_report: DiscoveryReport | None = None

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]:
        report = discover_dol_artifacts(
            self.config,
            self.client,
            from_fiscal_year=context.from_fiscal_year,
        )
        self.last_discovery_report = report
        return list(report.selected)

    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact:
        return self.downloader.download(candidate)

    def fingerprint(self, artifact: DownloadedArtifact) -> ArtifactFingerprint:
        return ArtifactFingerprint(sha256=artifact.sha256, byte_size=artifact.byte_size)

    def validate_raw(self, artifact: DownloadedArtifact) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if not artifact.raw_path.is_file():
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="missing_raw_artifact",
                    message="Downloaded raw artifact is missing",
                )
            )
        elif artifact.raw_path.stat().st_size != artifact.byte_size:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="raw_size_mismatch",
                    message="Downloaded raw artifact size changed after validation",
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
        status = ValidationStatus.FAILED if issues else ValidationStatus.PASSED
        return ValidationResult(status=status, issues=tuple(issues))

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        return self.normalizer.normalize(artifact)

    def validate_normalized(self, dataset: NormalizedDataset) -> ValidationResult:
        return dataset.validation

    def persist(self, dataset: NormalizedDataset) -> PersistedDataset:
        return self.normalizer.persist(dataset)
