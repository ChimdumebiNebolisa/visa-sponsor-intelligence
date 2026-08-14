"""Verified source-ingestion orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from sponsor_intel.logging import get_logger
from sponsor_intel.sources.base import SourceAdapter
from sponsor_intel.sources.dol_lca import DolLcaAdapter
from sponsor_intel.sources.dol_perm import DolPermAdapter
from sponsor_intel.sources.errors import DataQualityError
from sponsor_intel.sources.herd import HerdAdapter
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.institution_tables import build_institution_tables
from sponsor_intel.sources.ipeds import IpedsAdapter
from sponsor_intel.sources.manifests import (
    ArtifactManifestStore,
    RawArtifactManifestStore,
    write_json_atomic,
)
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    DownloadedArtifact,
    IngestionSummary,
    RawArtifactManifestRecord,
    SourceArtifactCandidate,
    SourceContext,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry
from sponsor_intel.sources.sevp_opt import SevpOptAdapter
from sponsor_intel.sources.uscis_h1b import UscisH1bAdapter

logger = get_logger("sources.pipeline")


def _build_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _adapter(
    source_id: str,
    registry: SourceRegistry,
    client: OfficialHttpClient,
    data_root: Path,
    output_root: Path,
) -> SourceAdapter:
    config = registry.get(source_id)
    if config.adapter == "dol_lca":
        return DolLcaAdapter(config, client, data_root, output_root)
    if config.adapter == "dol_perm":
        return DolPermAdapter(config, client, data_root, output_root)
    if config.adapter == "uscis_h1b":
        return UscisH1bAdapter(config, client, data_root, output_root)
    if config.adapter == "ipeds":
        return IpedsAdapter(config, client, data_root, output_root)
    if config.adapter == "herd":
        return HerdAdapter(config, client, data_root, output_root)
    if config.adapter == "sevp_opt":
        return SevpOptAdapter(config, client, data_root, output_root)
    raise ValueError(f"Unsupported source adapter: {config.adapter}")


def _cached_download(
    record: ArtifactManifestRecord | RawArtifactManifestRecord,
    candidate: SourceArtifactCandidate,
) -> DownloadedArtifact | None:
    if not record.raw_path.is_file() or record.raw_path.stat().st_size != record.byte_size:
        return None
    return DownloadedArtifact(
        candidate=candidate,
        raw_path=record.raw_path,
        retrieved_at=record.retrieved_at,
        sha256=record.sha256,
        byte_size=record.byte_size,
        mime_type=record.mime_type,
        etag=record.etag,
        last_modified=record.last_modified,
        cache_hit=True,
    )


class IngestionPipeline:
    """Discover and ingest one configured source in resumable increments."""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.registry = registry
        self.data_root = data_root
        self.output_root = output_root
        self.transport = transport
        self.manifest_store = ArtifactManifestStore(
            output_root / "manifests" / "source_artifacts.jsonl"
        )
        self.raw_manifest_store = RawArtifactManifestStore(
            output_root / "manifests" / "raw_downloads.jsonl"
        )

    def discover(
        self, source_id: str, *, from_fiscal_year: int = 2022
    ) -> tuple[Path, DiscoveryReport]:
        config = self.registry.get(source_id)
        with OfficialHttpClient(config.official_domains, transport=self.transport) as client:
            adapter = _adapter(source_id, self.registry, client, self.data_root, self.output_root)
            adapter.discover(SourceContext(from_fiscal_year=from_fiscal_year))
            report = adapter.last_discovery_report
            if report is None:
                raise RuntimeError("Source adapter did not retain its discovery report")
        report_path = self.output_root / "manifests" / "discovery" / f"{source_id}-latest.json"
        write_json_atomic(report_path, report)
        return report_path, report

    def ingest(
        self,
        source_id: str,
        *,
        from_fiscal_year: int = 2022,
        force_download: bool = False,
    ) -> IngestionSummary:
        build_id = _build_id()
        config = self.registry.get(source_id)
        report_path: Path
        records: list[ArtifactManifestRecord] = []
        ingested = 0
        reused = 0
        total_rows = 0

        with OfficialHttpClient(config.official_domains, transport=self.transport) as client:
            adapter = _adapter(source_id, self.registry, client, self.data_root, self.output_root)
            candidates = adapter.discover(SourceContext(from_fiscal_year=from_fiscal_year))
            report = adapter.last_discovery_report
            if report is None:
                raise RuntimeError("Source adapter did not retain its discovery report")
            report_path = self.output_root / "manifests" / "discovery" / f"{source_id}-latest.json"
            write_json_atomic(report_path, report)

            for candidate in candidates:
                existing = self.manifest_store.latest_for_candidate(candidate)
                if (
                    existing is not None
                    and not force_download
                    and existing.parquet_path.is_file()
                    and existing.parser_version == config.parser_version
                    and existing.schema_version == config.schema_version
                    and _cached_download(existing, candidate) is not None
                ):
                    logger.info(
                        "Reusing unchanged source artifact",
                        extra={
                            "build_id": build_id,
                            "source_id": source_id,
                            "artifact_id": existing.source_artifact_id,
                            "stage": "ingest",
                            "status": "cached",
                            "record_count": existing.row_count,
                        },
                    )
                    records.append(existing)
                    total_rows += existing.row_count
                    reused += 1
                    continue

                raw_record = self.raw_manifest_store.latest_for_candidate(candidate)
                cached_record = existing or raw_record
                cached_download = (
                    _cached_download(cached_record, candidate)
                    if cached_record is not None and not force_download
                    else None
                )
                downloaded = cached_download or adapter.download(candidate)
                if cached_download is None:
                    self.raw_manifest_store.upsert(
                        RawArtifactManifestRecord(
                            source_artifact_id=downloaded.source_artifact_id,
                            source_id=source_id,
                            authority=candidate.authority,
                            landing_page_url=candidate.landing_page_url,
                            download_url=candidate.download_url,
                            retrieved_at=downloaded.retrieved_at,
                            fiscal_year=candidate.fiscal_year,
                            fiscal_quarter=candidate.fiscal_quarter,
                            is_partial_period=candidate.is_partial_period,
                            file_name=candidate.file_name,
                            mime_type=downloaded.mime_type,
                            byte_size=downloaded.byte_size,
                            sha256=downloaded.sha256,
                            record_layout_url=candidate.record_layout_url,
                            raw_path=downloaded.raw_path,
                            etag=downloaded.etag,
                            last_modified=downloaded.last_modified,
                        )
                    )
                raw_validation = adapter.validate_raw(downloaded)
                if raw_validation.status is ValidationStatus.FAILED:
                    raise DataQualityError(
                        f"Raw validation failed for {candidate.file_name}: "
                        f"{[issue.message for issue in raw_validation.issues]}"
                    )
                logger.info(
                    "Normalizing source artifact",
                    extra={
                        "build_id": build_id,
                        "source_id": source_id,
                        "artifact_id": downloaded.source_artifact_id,
                        "stage": "normalize",
                        "status": "starting",
                    },
                )
                normalized = adapter.normalize(downloaded)
                normalized_validation = adapter.validate_normalized(normalized)
                if normalized_validation.status is ValidationStatus.FAILED:
                    raise DataQualityError(
                        f"Normalized validation failed for {candidate.file_name}: "
                        f"{[issue.message for issue in normalized_validation.issues]}"
                    )
                persisted = adapter.persist(normalized)
                record = ArtifactManifestRecord(
                    source_artifact_id=downloaded.source_artifact_id,
                    source_id=source_id,
                    authority=candidate.authority,
                    landing_page_url=candidate.landing_page_url,
                    download_url=candidate.download_url,
                    retrieved_at=downloaded.retrieved_at,
                    fiscal_year=candidate.fiscal_year,
                    fiscal_quarter=candidate.fiscal_quarter,
                    is_partial_period=candidate.is_partial_period,
                    file_name=candidate.file_name,
                    mime_type=downloaded.mime_type,
                    byte_size=downloaded.byte_size,
                    sha256=downloaded.sha256,
                    record_layout_url=candidate.record_layout_url,
                    parser_version=config.parser_version,
                    schema_version=config.schema_version,
                    row_count=persisted.row_count,
                    column_count=persisted.column_count,
                    validation_status=persisted.validation.status,
                    build_id=build_id,
                    raw_path=downloaded.raw_path,
                    parquet_path=persisted.parquet_path,
                    schema_diff_path=persisted.schema_diff_path,
                    etag=downloaded.etag,
                    last_modified=downloaded.last_modified,
                )
                self.manifest_store.upsert(record)
                records.append(record)
                total_rows += record.row_count
                ingested += 1
                del normalized
                logger.info(
                    "Persisted source artifact",
                    extra={
                        "build_id": build_id,
                        "source_id": source_id,
                        "artifact_id": record.source_artifact_id,
                        "stage": "persist",
                        "status": record.validation_status,
                        "record_count": record.row_count,
                    },
                )

        if source_id in {"ipeds", "herd"}:
            build_institution_tables(
                self.manifest_store,
                data_root=self.data_root,
                output_root=self.output_root,
            )

        return IngestionSummary(
            build_id=build_id,
            source_id=source_id,
            selected_artifact_count=len(records),
            ingested_artifact_count=ingested,
            reused_artifact_count=reused,
            row_count=total_rows,
            manifest_path=self.manifest_store.path,
            discovery_report_path=report_path,
            records=tuple(records),
        )
