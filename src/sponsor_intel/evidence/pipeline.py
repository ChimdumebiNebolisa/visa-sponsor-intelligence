"""Phase 6 orchestration over official OPT and E-Verify evidence."""

from __future__ import annotations

from pathlib import Path

from sponsor_intel.database.builder import DuckDBBuilder
from sponsor_intel.evidence.everify import (
    EVerifyEvidenceBuilder,
    EVerifySearchProvider,
    PlaywrightEVerifyClient,
)
from sponsor_intel.evidence.models import Phase6BuildSummary
from sponsor_intel.evidence.opt import OptEvidenceBuilder
from sponsor_intel.metrics.pipeline import MetricsPipeline
from sponsor_intel.sources.manifests import write_json_atomic
from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import DEFAULT_SOURCE_REGISTRY_PATH, SourceRegistry


class Phase6Pipeline:
    """Build positive OPT evidence, prioritized E-Verify evidence, metrics, and DuckDB."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        database_path: Path = Path("db/immigration.duckdb"),
        registry_path: Path = DEFAULT_SOURCE_REGISTRY_PATH,
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.database_path = database_path
        self.registry = SourceRegistry.from_yaml(registry_path)

    def build(
        self,
        *,
        everify_limit: int = 0,
        force_opt_download: bool = False,
        provider: EVerifySearchProvider | None = None,
    ) -> Phase6BuildSummary:
        IngestionPipeline(
            self.registry,
            data_root=self.data_root,
            output_root=self.output_root,
        ).ingest(
            "sevp_opt",
            from_fiscal_year=2022,
            force_download=force_opt_download,
        )
        opt_summary = OptEvidenceBuilder(
            data_root=self.data_root,
            output_root=self.output_root,
        ).build()
        everify_builder = EVerifyEvidenceBuilder(
            data_root=self.data_root,
            output_root=self.output_root,
        )
        priorities = everify_builder.build_priorities()
        if everify_limit and provider is None:
            with PlaywrightEVerifyClient() as live_provider:
                everify_summary = everify_builder.run(
                    priorities, limit=everify_limit, provider=live_provider
                )
        else:
            everify_summary = everify_builder.run(
                priorities, limit=everify_limit, provider=provider
            )
        MetricsPipeline(data_root=self.data_root, output_root=self.output_root).build()
        DuckDBBuilder(data_root=self.data_root, database_path=self.database_path).build()
        summary_path = self.output_root / "reports" / "evidence" / "phase6_summary.json"
        summary = Phase6BuildSummary(
            opt=opt_summary,
            everify=everify_summary,
            database_path=self.database_path,
            summary_path=summary_path,
        )
        write_json_atomic(summary_path, summary)
        return summary
