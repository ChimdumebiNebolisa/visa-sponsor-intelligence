from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.entity_resolution.pipeline import EntityResolutionPipeline
from sponsor_intel.sources.manifests import ArtifactManifestStore, write_json_atomic
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry


def _record(
    tmp_path: Path,
    registry: SourceRegistry,
    source_id: str,
    fiscal_year: int,
    frame: pl.DataFrame,
    *,
    fiscal_quarter: int | None = None,
    is_quarter_partition: bool = False,
    coverage_start_quarter: int | None = None,
) -> ArtifactManifestRecord:
    source = registry.get(source_id)
    quarter_suffix = f"-q{fiscal_quarter}" if fiscal_quarter is not None else ""
    artifact_id = f"{source_id}-{fiscal_year}{quarter_suffix}"
    parquet_path = tmp_path / "staging" / f"{artifact_id}.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame = frame.with_columns(pl.lit(artifact_id).alias("source_artifact_id"))
    frame.write_parquet(parquet_path)
    return ArtifactManifestRecord(
        source_artifact_id=artifact_id,
        source_id=source_id,
        authority=source.authority,
        landing_page_url=source.landing_page,
        download_url=f"https://example.gov/{artifact_id}.parquet",
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        is_partial_period=False,
        is_quarter_partition=is_quarter_partition,
        coverage_start_quarter=coverage_start_quarter,
        file_name=f"{artifact_id}.parquet",
        mime_type="application/vnd.apache.parquet",
        byte_size=parquet_path.stat().st_size,
        sha256="a" * 64,
        record_layout_url=None,
        parser_version=source.parser_version,
        schema_version=source.schema_version,
        row_count=frame.height,
        column_count=frame.width,
        validation_status=ValidationStatus.PASSED,
        build_id="fixture",
        raw_path=tmp_path / "raw" / f"{artifact_id}.parquet",
        parquet_path=parquet_path,
        schema_diff_path=tmp_path / "schema" / f"{artifact_id}.json",
    )


def _write_discovery_reports(output_root: Path, manifest: ArtifactManifestStore) -> None:
    records = manifest.records()
    for source_id in sorted({record.source_id for record in records}):
        source_records = [record for record in records if record.source_id == source_id]
        candidates = tuple(
            SourceArtifactCandidate(
                source_id=record.source_id,
                authority=record.authority,
                landing_page_url=record.landing_page_url,
                download_url=record.download_url,
                fiscal_year=record.fiscal_year,
                fiscal_quarter=record.fiscal_quarter,
                is_partial_period=record.is_partial_period,
                is_quarter_partition=record.is_quarter_partition,
                coverage_start_quarter=record.coverage_start_quarter,
                file_name=record.file_name,
                expected_format="parquet",
            )
            for record in source_records
        )
        write_json_atomic(
            output_root / "manifests" / "discovery" / f"{source_id}-latest.json",
            DiscoveryReport(
                source_id=source_id,
                discovered_at=datetime(2026, 8, 14, tzinfo=UTC),
                from_fiscal_year=2022,
                landing_page_url=source_records[0].landing_page_url,
                candidates=candidates,
                selected_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
            ),
        )


def test_pipeline_builds_auditable_registries_and_resolved_mirrors(
    tmp_path: Path,
) -> None:
    registry = SourceRegistry.from_yaml()
    output_root = tmp_path / "outputs"
    manifest = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "dol_lca",
            2026,
            pl.DataFrame(
                {
                    "case_id": ["LCA-1", "LCA-2"],
                    "employer_name_raw": [
                        "Amazon.com Services LLC",
                        "Amazon.com Services LLC",
                    ],
                    "employer_city": ["Seattle", "Seattle"],
                    "employer_state": ["WA", "WA"],
                    "employer_postal_code": ["98101", "98101"],
                    "source_id": ["dol_lca", "dol_lca"],
                }
            ),
        )
    )
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "uscis_h1b",
            2026,
            pl.DataFrame(
                {
                    "employer_name_raw": ["AMAZON COM SERVICES LLC"],
                    "city": ["Seattle"],
                    "state": ["WA"],
                    "zip_code": ["98101"],
                    "source_id": ["uscis_h1b"],
                }
            ),
        )
    )
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "ipeds",
            2025,
            pl.DataFrame(
                {
                    "unitid": ["228778"],
                    "instnm": ["University of Texas at Austin"],
                    "city": ["Austin"],
                    "stabbr": ["TX"],
                    "zip": ["78712"],
                    "f1sysnam": ["The University of Texas System"],
                    "f1syscod": ["128010"],
                    "source_id": ["ipeds"],
                }
            ),
        )
    )
    _write_discovery_reports(output_root, manifest)
    data_root = tmp_path / "data"
    pipeline = EntityResolutionPipeline(registry, data_root=data_root, output_root=output_root)

    summary = pipeline.build()

    assert summary.observation_count == 3
    assert summary.resolved_record_count == 4
    assert summary.review_queue_count == 0
    assert summary.legal_entities_path.is_file()
    assert summary.parent_organizations_path.is_file()
    assert summary.aliases_path.is_file()
    resolved_dol = pl.read_parquet(
        next((data_root / "resolved" / "sources" / "dol_lca").rglob("*.parquet"))
    )
    assert resolved_dol["employer_name_raw"].to_list() == [
        "Amazon.com Services LLC",
        "Amazon.com Services LLC",
    ]
    assert set(resolved_dol["legal_entity_id"]) == {"legal_amazon_com_services"}


def test_pipeline_uses_legal_employer_location_not_worksite_location(tmp_path: Path) -> None:
    registry = SourceRegistry.from_yaml()
    output_root = tmp_path / "outputs"
    manifest = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "dol_lca",
            2026,
            pl.DataFrame(
                {
                    "case_id": ["LCA-LOCATION-1"],
                    "employer_name_raw": ["University of Texas at Austin"],
                    "employer_city": ["Austin"],
                    "employer_state": ["TX"],
                    "employer_postal_code": ["78712"],
                    "worksite_city": ["Boston"],
                    "worksite_state": ["MA"],
                    "worksite_postal_code": ["02108"],
                    "source_id": ["dol_lca"],
                }
            ),
        )
    )
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "ipeds",
            2025,
            pl.DataFrame(
                {
                    "unitid": ["228778"],
                    "instnm": ["University of Texas at Austin"],
                    "city": ["Austin"],
                    "stabbr": ["TX"],
                    "zip": ["78712"],
                    "f1sysnam": ["The University of Texas System"],
                    "f1syscod": ["128010"],
                    "source_id": ["ipeds"],
                }
            ),
        )
    )
    _write_discovery_reports(output_root, manifest)
    data_root = tmp_path / "data"

    summary = EntityResolutionPipeline(
        registry, data_root=data_root, output_root=output_root
    ).build()

    aliases = pl.read_parquet(summary.aliases_path)
    lca_alias = aliases.filter(pl.col("source_id") == "dol_lca").row(0, named=True)
    assert lca_alias["city"] == "AUSTIN"
    assert lca_alias["state"] == "TX"
    assert lca_alias["legal_entity_id"] == "legal_ipeds_228778"
    resolved = pl.read_parquet(
        next((data_root / "resolved" / "sources" / "dol_lca").rglob("*.parquet"))
    )
    assert resolved["worksite_state"].to_list() == ["MA"]
    assert resolved["legal_entity_id"].to_list() == ["legal_ipeds_228778"]


def test_pipeline_filters_validated_lca_state_supersession_from_resolved_sources(
    tmp_path: Path,
) -> None:
    registry = SourceRegistry.from_yaml()
    output_root = tmp_path / "outputs"
    manifest = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    lca_records: list[ArtifactManifestRecord] = []
    for fiscal_year, status, decision_date, employer_name, job_title in (
        (2022, "Certified", date(2022, 9, 15), "EXAMPLE   LLC", "Engineer I"),
        (
            2023,
            "Certified - Withdrawn",
            date(2022, 11, 15),
            "Example LLC",
            "Engineer II",
        ),
    ):
        record = _record(
            tmp_path,
            registry,
            "dol_lca",
            fiscal_year,
            pl.DataFrame(
                {
                    "source_row_number": [2],
                    "case_id": ["LCA-SUPERSEDED"],
                    "case_status": [status],
                    "decision_date": [decision_date],
                    "employer_name_raw": [employer_name],
                    "visa_class": ["H-1B"],
                    "employer_address_1": ["100 Main Street"],
                    "employer_address_2": ["Suite 200"],
                    "job_title_raw": [job_title],
                    "employer_city": ["Austin"],
                    "employer_state": ["TX"],
                    "employer_postal_code": ["78701"],
                    "source_id": ["dol_lca"],
                }
            ),
        )
        manifest.upsert(record)
        lca_records.append(record)
    manifest.upsert(
        _record(
            tmp_path,
            registry,
            "ipeds",
            2025,
            pl.DataFrame(
                {
                    "unitid": ["228778"],
                    "instnm": ["University of Texas at Austin"],
                    "city": ["Austin"],
                    "stabbr": ["TX"],
                    "zip": ["78712"],
                    "f1sysnam": ["The University of Texas System"],
                    "f1syscod": ["128010"],
                    "source_id": ["ipeds"],
                }
            ),
        )
    )
    _write_discovery_reports(output_root, manifest)
    data_root = tmp_path / "data"

    summary = EntityResolutionPipeline(
        registry, data_root=data_root, output_root=output_root
    ).build()

    staging_lca_count = sum(pl.read_parquet(record.parquet_path).height for record in lca_records)
    resolved_lca = pl.concat(
        [
            pl.read_parquet(path)
            for path in (data_root / "resolved" / "sources" / "dol_lca").rglob("*.parquet")
        ],
        how="diagonal_relaxed",
    )
    superseded = pl.read_parquet(
        output_root / "reports" / "entities" / "lca_superseded_source_rows.parquet"
    )
    aliases = pl.read_parquet(summary.aliases_path)
    assert staging_lca_count == 2
    assert resolved_lca.height == 1
    assert summary.resolved_record_count == 2
    assert resolved_lca.filter(pl.col("case_id") == "LCA-SUPERSEDED")["case_status"].to_list() == [
        "Certified - Withdrawn"
    ]
    assert "Engineer I" not in resolved_lca["job_title_raw"].to_list()
    assert "Engineer II" in resolved_lca["job_title_raw"].to_list()
    assert (
        aliases.filter((pl.col("source_id") == "dol_lca") & (pl.col("alias_raw") == "Example LLC"))[
            "occurrence_count"
        ].item()
        == 1
    )
    assert aliases.filter(pl.col("alias_raw") == "EXAMPLE   LLC").is_empty()
    assert superseded.select(
        "source_artifact_id",
        "source_row_number",
        "case_id",
        "fiscal_year",
        "superseding_fiscal_year",
        "superseding_source_artifact_id",
    ).row(0, named=True) == {
        "source_artifact_id": "dol_lca-2022",
        "source_row_number": 2,
        "case_id": "LCA-SUPERSEDED",
        "fiscal_year": 2022,
        "superseding_fiscal_year": 2023,
        "superseding_source_artifact_id": "dol_lca-2023",
    }
