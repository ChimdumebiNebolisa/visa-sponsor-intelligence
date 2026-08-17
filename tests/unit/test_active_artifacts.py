"""Active source-artifact selection contracts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from sponsor_intel.sources.errors import DataQualityError
from sponsor_intel.sources.manifests import (
    ArtifactManifestStore,
    active_artifact_records,
    active_layer_paths,
    lca_superseded_row_keys,
    write_json_atomic,
)
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry


def _candidate(quarter: int) -> SourceArtifactCandidate:
    return SourceArtifactCandidate(
        source_id="dol_lca",
        authority="U.S. Department of Labor",
        landing_page_url="https://www.dol.gov/agencies/eta/foreign-labor/performance",
        download_url=f"https://www.dol.gov/LCA_FY2026_Q{quarter}.xlsx",
        fiscal_year=2026,
        fiscal_quarter=quarter,
        is_partial_period=True,
        file_name=f"LCA_FY2026_Q{quarter}.xlsx",
        expected_format="xlsx",
    )


def _record(
    tmp_path: Path,
    registry: SourceRegistry,
    candidate: SourceArtifactCandidate,
) -> ArtifactManifestRecord:
    artifact_id = f"lca-{candidate.fiscal_year}-q{candidate.fiscal_quarter}"
    parquet_path = tmp_path / "normalized" / f"{artifact_id}.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_path.touch()
    source = registry.get(candidate.source_id)
    return ArtifactManifestRecord(
        source_artifact_id=artifact_id,
        source_id=candidate.source_id,
        authority=candidate.authority,
        landing_page_url=candidate.landing_page_url,
        download_url=candidate.download_url,
        retrieved_at=datetime(2026, 8, candidate.fiscal_quarter or 1, tzinfo=UTC),
        fiscal_year=candidate.fiscal_year,
        fiscal_quarter=candidate.fiscal_quarter,
        is_partial_period=candidate.is_partial_period,
        is_quarter_partition=candidate.is_quarter_partition,
        coverage_start_quarter=candidate.coverage_start_quarter,
        file_name=candidate.file_name,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size=1,
        sha256=str(candidate.fiscal_quarter) * 64,
        record_layout_url=None,
        parser_version=source.parser_version,
        schema_version=source.schema_version,
        raw_row_count=1,
        row_count=1,
        column_count=1,
        validation_status=ValidationStatus.PASSED,
        build_id="fixture",
        raw_path=tmp_path / "raw" / candidate.file_name,
        parquet_path=parquet_path,
        schema_diff_path=tmp_path / "schema" / f"{artifact_id}.json",
    )


def test_latest_discovery_selection_excludes_stale_cumulative_artifact(tmp_path: Path) -> None:
    registry = SourceRegistry.from_yaml()
    q2 = _candidate(2)
    q3 = _candidate(3)
    store = ArtifactManifestStore(tmp_path / "outputs" / "manifests" / "source_artifacts.jsonl")
    q2_record = _record(tmp_path, registry, q2)
    q3_record = _record(tmp_path, registry, q3)
    store.upsert(q2_record)
    store.upsert(q3_record)
    discovery_root = tmp_path / "outputs" / "manifests" / "discovery"
    write_json_atomic(
        discovery_root / "dol_lca-latest.json",
        DiscoveryReport(
            source_id="dol_lca",
            discovered_at=datetime(2026, 8, 16, tzinfo=UTC),
            from_fiscal_year=2022,
            landing_page_url=q3.landing_page_url,
            candidates=(q2, q3),
            selected_candidate_ids=(q3.candidate_id,),
        ),
    )

    active = active_artifact_records(
        store,
        registry,
        discovery_root=discovery_root,
        source_ids={"dol_lca"},
    )
    active_path = (
        tmp_path
        / "data"
        / "classified"
        / "sources"
        / "dol_lca"
        / "fy=2026"
        / f"{q3_record.source_artifact_id}.parquet"
    )
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.touch()
    stale_path = active_path.with_name(f"{q2_record.source_artifact_id}.parquet")
    stale_path.touch()

    assert [record.source_artifact_id for record in active] == [q3_record.source_artifact_id]
    assert active_layer_paths(
        tmp_path / "data",
        layer="classified",
        records=active,
        source_id="dol_lca",
    ) == [active_path]


def test_active_selection_rejects_completed_lca_q4_snapshot_without_annual_contract(
    tmp_path: Path,
) -> None:
    registry = SourceRegistry.from_yaml()
    candidate = _candidate(4).model_copy(
        update={
            "download_url": "https://www.dol.gov/LCA_FY2025_Q4.xlsx",
            "fiscal_year": 2025,
            "is_partial_period": False,
            "coverage_start_quarter": 1,
            "file_name": "LCA_FY2025_Q4.xlsx",
        }
    )
    store = ArtifactManifestStore(tmp_path / "outputs" / "manifests" / "source_artifacts.jsonl")
    store.upsert(_record(tmp_path, registry, candidate))
    discovery_root = tmp_path / "outputs" / "manifests" / "discovery"
    write_json_atomic(
        discovery_root / "dol_lca-latest.json",
        DiscoveryReport(
            source_id="dol_lca",
            discovered_at=datetime(2026, 8, 16, tzinfo=UTC),
            from_fiscal_year=2022,
            landing_page_url=candidate.landing_page_url,
            candidates=(candidate,),
            selected_candidate_ids=(candidate.candidate_id,),
        ),
    )

    with pytest.raises(DataQualityError, match="explicit annual artifact"):
        active_artifact_records(
            store,
            registry,
            discovery_root=discovery_root,
            source_ids={"dol_lca"},
        )


@pytest.mark.parametrize(
    ("validation_status", "newest_parquet_exists", "error_match"),
    [
        (ValidationStatus.FAILED, True, "failed validation"),
        (ValidationStatus.PASSED, False, "normalized Parquet is unavailable"),
    ],
)
def test_active_selection_never_falls_back_from_newest_matching_retrieval(
    tmp_path: Path,
    validation_status: ValidationStatus,
    newest_parquet_exists: bool,
    error_match: str,
) -> None:
    registry = SourceRegistry.from_yaml()
    candidate = _candidate(3)
    store = ArtifactManifestStore(tmp_path / "outputs" / "manifests" / "source_artifacts.jsonl")
    older = _record(tmp_path, registry, candidate)
    newest_path = tmp_path / "normalized" / "newest.parquet"
    if newest_parquet_exists:
        newest_path.touch()
    newest = older.model_copy(
        update={
            "source_artifact_id": "lca-2026-q3-newest",
            "retrieved_at": older.retrieved_at + timedelta(seconds=1),
            "sha256": "f" * 64,
            "validation_status": validation_status,
            "parquet_path": newest_path,
        }
    )
    store.upsert(older)
    store.upsert(newest)
    discovery_root = tmp_path / "outputs" / "manifests" / "discovery"
    write_json_atomic(
        discovery_root / "dol_lca-latest.json",
        DiscoveryReport(
            source_id="dol_lca",
            discovered_at=datetime(2026, 8, 16, tzinfo=UTC),
            from_fiscal_year=2022,
            landing_page_url=candidate.landing_page_url,
            candidates=(candidate,),
            selected_candidate_ids=(candidate.candidate_id,),
        ),
    )

    with pytest.raises(ValueError, match=error_match):
        active_artifact_records(
            store,
            registry,
            discovery_root=discovery_root,
            source_ids={"dol_lca"},
        )


def test_legacy_manifest_without_raw_row_count_remains_readable(tmp_path: Path) -> None:
    registry = SourceRegistry.from_yaml()
    record = _record(tmp_path, registry, _candidate(3))
    payload = record.model_dump(mode="json")
    payload.pop("raw_row_count")
    manifest_path = tmp_path / "outputs" / "manifests" / "source_artifacts.jsonl"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    loaded = ArtifactManifestStore(manifest_path).records()

    assert len(loaded) == 1
    assert loaded[0].raw_row_count is None
    assert loaded[0].row_count == record.row_count


@pytest.mark.parametrize(
    (
        "earlier_name",
        "later_name",
        "earlier_address",
        "later_address",
        "earlier_postal",
        "later_postal",
    ),
    [
        ("Example LLC", "Example LLC", "100 Main Street", "100 Main Street", "2109", "02109"),
        (
            "\ufffdExample LLC",
            "Example LLC",
            "100 O\ufffdBrien Street",
            "100 OBrien Street",
            "02109",
            "02109",
        ),
        (
            "Humane\u2013America Animal Foundation",
            "Humane\u201cAmerica Animal Foundation",
            "100 Main Street",
            "100 Main Street",
            "02109",
            "02109",
        ),
        (
            "Grogan\u2019s LLC",
            "Grogans LLC",
            "100 Main Street",
            "100 Main Street",
            "02109",
            "02109",
        ),
        (
            "\u00bfPGIM",
            "PGIM",
            "100 Animal\u00bfFoundation Road",
            "100 AnimalFoundation Road",
            "02109",
            "02109",
        ),
        (
            "R\u00c3\u00b6chling Automotive",
            "R\u00f6chling Automotive",
            "100 Main Street",
            "100 Main Street",
            "02109",
            "02109",
        ),
        (
            "Example\u00a0LLC",
            "Example LLC",
            "100\u00a0Main Street",
            "100 Main Street",
            "02109",
            "02109",
        ),
    ],
)
def test_lca_supersession_tracks_valid_cross_year_state_update(
    tmp_path: Path,
    earlier_name: str,
    later_name: str,
    earlier_address: str,
    later_address: str,
    earlier_postal: str,
    later_postal: str,
) -> None:
    registry = SourceRegistry.from_yaml()
    records: list[ArtifactManifestRecord] = []
    for fiscal_year, quarter, status, decision_date, employer_name, address, postal in (
        (
            2022,
            4,
            "Certified",
            date(2022, 9, 15),
            earlier_name,
            earlier_address,
            earlier_postal,
        ),
        (
            2023,
            1,
            "Certified - Withdrawn",
            date(2022, 11, 15),
            later_name,
            later_address,
            later_postal,
        ),
    ):
        candidate = _candidate(quarter).model_copy(
            update={
                "download_url": (f"https://www.dol.gov/LCA_FY{fiscal_year}_Q{quarter}.xlsx"),
                "fiscal_year": fiscal_year,
                "is_partial_period": False,
                "is_quarter_partition": True,
                "coverage_start_quarter": quarter,
                "file_name": f"LCA_FY{fiscal_year}_Q{quarter}.xlsx",
            }
        )
        record = _record(tmp_path, registry, candidate)
        pl.DataFrame(
            {
                "source_row_number": [2],
                "case_id": ["I-200-CROSS-YEAR"],
                "case_status": [status],
                "decision_date": [decision_date],
                "employer_name_raw": [employer_name],
                "visa_class": ["H-1B"],
                "employer_address_1": [address],
                "employer_address_2": [None],
                "employer_city": ["Austin"],
                "employer_state": ["TX"],
                "employer_postal_code": [postal],
            }
        ).write_parquet(record.parquet_path)
        records.append(record)

    superseded = lca_superseded_row_keys(tuple(records))

    assert superseded.select(
        "source_artifact_id",
        "source_row_number",
        "case_id",
        "fiscal_year",
        "superseding_fiscal_year",
        "superseding_source_artifact_id",
        "superseding_source_row_number",
    ).row(0, named=True) == {
        "source_artifact_id": "lca-2022-q4",
        "source_row_number": 2,
        "case_id": "I-200-CROSS-YEAR",
        "fiscal_year": 2022,
        "superseding_fiscal_year": 2023,
        "superseding_source_artifact_id": "lca-2023-q1",
        "superseding_source_row_number": 2,
    }


@pytest.mark.parametrize(
    ("earlier_name", "later_name", "earlier_address", "later_address"),
    [
        ("Example LLC", "Examples LLC", "100 Main Street", "100 Main Street"),
        ("Example LLC", "Example LLC", "100 Main Street", "200 Main Street"),
    ],
)
def test_lca_supersession_rejects_cross_year_stable_identity_conflict(
    tmp_path: Path,
    earlier_name: str,
    later_name: str,
    earlier_address: str,
    later_address: str,
) -> None:
    registry = SourceRegistry.from_yaml()
    records: list[ArtifactManifestRecord] = []
    for fiscal_year, quarter, status, decision_date, employer_name, address in (
        (
            2022,
            4,
            "Certified",
            date(2022, 9, 15),
            earlier_name,
            earlier_address,
        ),
        (
            2023,
            1,
            "Certified - Withdrawn",
            date(2022, 11, 15),
            later_name,
            later_address,
        ),
    ):
        candidate = _candidate(quarter).model_copy(
            update={
                "download_url": (f"https://www.dol.gov/LCA_FY{fiscal_year}_Q{quarter}.xlsx"),
                "fiscal_year": fiscal_year,
                "is_partial_period": False,
                "is_quarter_partition": True,
                "coverage_start_quarter": quarter,
                "file_name": f"LCA_FY{fiscal_year}_Q{quarter}.xlsx",
            }
        )
        record = _record(tmp_path, registry, candidate)
        pl.DataFrame(
            {
                "source_row_number": [2],
                "case_id": ["I-200-IDENTITY-CONFLICT"],
                "case_status": [status],
                "decision_date": [decision_date],
                "employer_name_raw": [employer_name],
                "visa_class": ["H-1B"],
                "employer_address_1": [address],
                "employer_address_2": [None],
                "employer_city": ["Austin"],
                "employer_state": ["TX"],
                "employer_postal_code": ["78701"],
            }
        ).write_parquet(record.parquet_path)
        records.append(record)

    with pytest.raises(DataQualityError, match="same stable employer identity"):
        lca_superseded_row_keys(tuple(records))
