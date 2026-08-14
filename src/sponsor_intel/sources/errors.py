"""Explicit source-pipeline failure categories."""


class SourcePipelineError(RuntimeError):
    """Base class for source-pipeline failures."""


class UnsafeSourceUrlError(SourcePipelineError):
    """Raised when a URL escapes a source's official-domain policy."""


class SourceDiscoveryError(SourcePipelineError):
    """Raised when official source artifacts cannot be discovered safely."""


class DownloadError(SourcePipelineError):
    """Raised when an artifact cannot be downloaded or validated."""


class SchemaDriftError(SourcePipelineError):
    """Raised when required source fields disappear or become ambiguous."""


class DataQualityError(SourcePipelineError):
    """Raised when normalized data violates a critical quality gate."""
