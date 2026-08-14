"""Authoritative source discovery, ingestion, and normalization."""

from sponsor_intel.sources.base import SourceAdapter
from sponsor_intel.sources.models import SourceConfig, SourceContext
from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import SourceRegistry

__all__ = ["IngestionPipeline", "SourceAdapter", "SourceConfig", "SourceContext", "SourceRegistry"]
