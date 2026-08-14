"""Common source-adapter protocol."""

from __future__ import annotations

from typing import Protocol

from sponsor_intel.sources.models import (
    ArtifactFingerprint,
    DiscoveryReport,
    DownloadedArtifact,
    NormalizedDataset,
    PersistedDataset,
    SourceArtifactCandidate,
    SourceContext,
    ValidationResult,
)


class SourceAdapter(Protocol):
    """Contract every official source adapter must implement."""

    @property
    def last_discovery_report(self) -> DiscoveryReport | None: ...

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]: ...

    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact: ...

    def fingerprint(self, artifact: DownloadedArtifact) -> ArtifactFingerprint: ...

    def validate_raw(self, artifact: DownloadedArtifact) -> ValidationResult: ...

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset: ...

    def validate_normalized(self, dataset: NormalizedDataset) -> ValidationResult: ...

    def persist(self, dataset: NormalizedDataset) -> PersistedDataset: ...
