from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.entity_resolution.pipeline import EntityResolutionPipeline
from sponsor_intel.sources.manifests import ArtifactManifestStore
from sponsor_intel.sources.models import ArtifactManifestRecord, ValidationStatus
from sponsor_intel.sources.registry import SourceRegistry


def _record(
    tmp_path: Path,
    registry: SourceRegistry,
    source_id: str,
    fiscal_year: int,
    frame: pl.DataFrame,
) -> ArtifactManifestRecord:
    source = registry.get(source_id)
    artifact_id = f"{source_id}-{fiscal_year}"
    parquet_path = tmp_path / "staging" / f"{artifact_id}.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(parquet_path)
    return ArtifactManifestRecord(
        source_artifact_id=artifact_id,
        source_id=source_id,
        authority=source.authority,
        landing_page_url=source.landing_page,
        download_url=f"https://example.gov/{artifact_id}.parquet",
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        fiscal_year=fiscal_year,
        fiscal_quarter=None,
        is_partial_period=False,
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
