from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.role_classification.pipeline import RoleClassificationPipeline
from sponsor_intel.sources.manifests import ArtifactManifestStore, write_json_atomic
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry


def _write_source(
    data_root: Path,
    registry: SourceRegistry,
    source_id: str,
    fiscal_year: int,
    artifact_id: str,
    frame: pl.DataFrame,
    *,
    fiscal_quarter: int,
    is_partial_period: bool,
) -> tuple[ArtifactManifestRecord, SourceArtifactCandidate]:
    path = (
        data_root
        / "resolved"
        / "sources"
        / source_id
        / f"fy={fiscal_year}"
        / f"{artifact_id}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    source = registry.get(source_id)
    candidate = SourceArtifactCandidate(
        source_id=source_id,
        authority=source.authority,
        landing_page_url=source.landing_page,
        download_url=f"https://example.gov/{artifact_id}.xlsx",
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        is_partial_period=is_partial_period,
        file_name=f"{artifact_id}.xlsx",
        expected_format="xlsx",
    )
    return (
        ArtifactManifestRecord(
            source_artifact_id=artifact_id,
            source_id=source_id,
            authority=source.authority,
            landing_page_url=source.landing_page,
            download_url=candidate.download_url,
            retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            is_partial_period=is_partial_period,
            file_name=candidate.file_name,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            byte_size=path.stat().st_size,
            sha256="a" * 64,
            record_layout_url=None,
            parser_version=source.parser_version,
            schema_version=source.schema_version,
            row_count=frame.height,
            column_count=frame.width,
            validation_status=ValidationStatus.PASSED,
            build_id="fixture",
            raw_path=path,
            parquet_path=path,
            schema_diff_path=path.with_suffix(".json"),
        ),
        candidate,
    )


def _write_report(
    output_root: Path,
    source_id: str,
    candidates: tuple[SourceArtifactCandidate, ...],
    selected: tuple[SourceArtifactCandidate, ...],
) -> None:
    write_json_atomic(
        output_root / "manifests" / "discovery" / f"{source_id}-latest.json",
        DiscoveryReport(
            source_id=source_id,
            discovered_at=datetime(2026, 8, 16, tzinfo=UTC),
            from_fiscal_year=2022,
            landing_page_url=candidates[0].landing_page_url,
            candidates=candidates,
            selected_candidate_ids=tuple(candidate.candidate_id for candidate in selected),
        ),
    )


def test_pipeline_classifies_every_dol_row_and_preserves_source_values(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    registry = SourceRegistry.from_yaml()
    manifest = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    active_lca, active_lca_candidate = _write_source(
        data_root,
        registry,
        "dol_lca",
        2026,
        "dol_lca-2026-q3",
        pl.DataFrame(
            {
                "case_id": ["LCA-1", "LCA-2", "LCA-3"],
                "source_id": ["dol_lca"] * 3,
                "fiscal_year": [2026] * 3,
                "job_title_raw": ["Software Engineer", "Research Scientist", "Physician"],
                "soc_code": ["15-1252.00", None, "29-1215.00"],
            }
        ),
        fiscal_quarter=3,
        is_partial_period=True,
    )
    stale_lca, stale_lca_candidate = _write_source(
        data_root,
        registry,
        "dol_lca",
        2026,
        "dol_lca-2026-q2-stale",
        pl.DataFrame(
            {
                "case_id": ["LCA-STALE"],
                "source_id": ["dol_lca"],
                "fiscal_year": [2026],
                "job_title_raw": ["Stale Software Engineer"],
                "soc_code": ["15-1252.00"],
            }
        ),
        fiscal_quarter=2,
        is_partial_period=True,
    )
    active_perm, active_perm_candidate = _write_source(
        data_root,
        registry,
        "dol_perm",
        2025,
        "dol_perm-2025-q4",
        pl.DataFrame(
            {
                "case_id": ["PERM-1", "PERM-2"],
                "source_id": ["dol_perm"] * 2,
                "fiscal_year": [2025] * 2,
                "job_title_raw": ["Data Engineer", "Crew Member"],
                "soc_code": [None, "35-3023.00"],
            }
        ),
        fiscal_quarter=4,
        is_partial_period=False,
    )
    for record in (active_lca, stale_lca, active_perm):
        manifest.upsert(record)
    _write_report(
        output_root,
        "dol_lca",
        (stale_lca_candidate, active_lca_candidate),
        (active_lca_candidate,),
    )
    _write_report(
        output_root,
        "dol_perm",
        (active_perm_candidate,),
        (active_perm_candidate,),
    )
    pipeline = RoleClassificationPipeline(data_root=data_root, output_root=output_root)

    summary = pipeline.build()

    assert summary.record_count == 5
    assert summary.technical_record_count == 2
    assert summary.ambiguous_record_count == 1
    assert summary.review_queue_count == 1
    assert summary.classifications_path.is_file()
    assert summary.review_queue_path.is_file()
    classified_path = next((data_root / "classified" / "sources" / "dol_lca").rglob("*.parquet"))
    classified = pl.read_parquet(classified_path)
    assert classified["job_title_raw"].to_list() == [
        "Software Engineer",
        "Research Scientist",
        "Physician",
    ]
    assert classified["role_family"].to_list() == [
        "software_engineering",
        "ambiguous",
        "not_relevant",
    ]
    assert classified["classification_version"].n_unique() == 1
    assert {
        "technical_role",
        "role_family",
        "role_confidence",
        "classification_method",
        "classification_version",
        "review_status",
    }.issubset(classified.columns)
    assert "role_review_status" not in classified.columns
