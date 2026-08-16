"""End-to-end fixture test for resumable DOL ingestion."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import httpx
import polars as pl
import pytest
import xlsxwriter

from sponsor_intel.sources.errors import DataQualityError
from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import SourceRegistry


def _xlsx_bytes(
    case_id: str = "I-200-PIPELINE",
    decision_date: str = "2025-09-30",
    *,
    case_status: str = "Certified",
    additional_rows: tuple[tuple[str, str], ...] = (),
) -> bytes:
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    worksheet = workbook.add_worksheet("Disclosure")
    worksheet.write_row(
        0,
        0,
        [
            "CASE_NUMBER",
            "CASE_STATUS",
            "EMPLOYER_NAME",
            "JOB_TITLE",
            "SOC_CODE",
            "SOC_TITLE",
            "WORKSITE_STATE",
            "DECISION_DATE",
        ],
    )
    for row_index, (row_case_id, row_decision_date) in enumerate(
        ((case_id, decision_date), *additional_rows), start=1
    ):
        worksheet.write_row(
            row_index,
            0,
            [
                row_case_id,
                case_status,
                "Pipeline Example LLC",
                "Software Engineer",
                "15-1252",
                "Software Developers",
                "TX",
                row_decision_date,
            ],
        )
    workbook.close()
    return buffer.getvalue()


def test_pipeline_ingests_and_resumes_from_raw_manifest(tmp_path: Path) -> None:
    landing_page = "https://www.dol.gov/performance"
    artifact_url = "https://www.dol.gov/files/LCA_Disclosure_Data_FY2025.xlsx"
    layout_url = "https://www.dol.gov/files/LCA_Record_Layout_FY2025.pdf"
    html = (
        f'<a href="{artifact_url}">LCA Disclosure Data FY2025 Annual</a>'
        f'<a href="{layout_url}">LCA Record Layout FY2025 Annual</a>'
    )
    workbook = _xlsx_bytes(
        "I-200-PIPELINE-Q1",
        "2024-10-15",
        additional_rows=(
            ("I-200-PIPELINE-Q2", "2025-01-15"),
            ("I-200-PIPELINE-Q3", "2025-04-15"),
            ("I-200-PIPELINE-Q4", "2025-07-15"),
        ),
    )
    request_counts = {"landing": 0, "artifact": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == landing_page:
            request_counts["landing"] += 1
            return httpx.Response(200, text=html, request=request)
        if str(request.url) == artifact_url:
            request_counts["artifact"] += 1
            return httpx.Response(
                200,
                content=workbook,
                headers={
                    "content-type": (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                    "content-length": str(len(workbook)),
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    config = (
        SourceRegistry.from_yaml()
        .get("dol_lca")
        .model_copy(update={"landing_page": landing_page, "minimum_row_count": 1})
    )
    pipeline = IngestionPipeline(
        SourceRegistry((config,)),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
        transport=httpx.MockTransport(handler),
    )

    first = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    first_record = first.records[0]
    assert first.ingested_artifact_count == 1
    assert first.reused_artifact_count == 0
    assert first_record.raw_path.is_file()
    assert first_record.parquet_path.is_file()
    assert first_record.raw_row_count == 4
    assert first_record.coverage_start_quarter == 1
    assert not first_record.is_quarter_partition
    assert pipeline.raw_manifest_store.path.is_file()
    first_raw_record = pipeline.raw_manifest_store.records()[0]
    assert first_raw_record.coverage_start_quarter == 1
    assert not first_raw_record.is_quarter_partition
    assert pl.read_parquet(first_record.parquet_path).height == 4

    first_record.parquet_path.unlink()
    pipeline.manifest_store.path.unlink()
    resumed = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    assert resumed.ingested_artifact_count == 1
    assert resumed.reused_artifact_count == 0
    assert request_counts["artifact"] == 1

    legacy_record = json.loads(pipeline.manifest_store.path.read_text(encoding="utf-8"))
    legacy_record.pop("raw_row_count")
    pipeline.manifest_store.path.write_text(
        json.dumps(legacy_record) + "\n",
        encoding="utf-8",
    )
    renormalized = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    assert renormalized.ingested_artifact_count == 1
    assert renormalized.reused_artifact_count == 0
    assert renormalized.records[0].raw_row_count == 4
    assert request_counts["artifact"] == 1

    reused = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    assert reused.ingested_artifact_count == 0
    assert reused.reused_artifact_count == 1
    assert request_counts == {"landing": 4, "artifact": 1}


def test_pipeline_rejects_case_overlap_across_completed_lca_quarter_partitions(
    tmp_path: Path,
) -> None:
    landing_page = "https://www.dol.gov/performance"
    artifact_payloads = {
        f"https://www.dol.gov/files/LCA_Disclosure_Data_FY2022_Q{quarter}.xlsx": (
            _xlsx_bytes(case_id, decision_date)
        )
        for quarter, case_id, decision_date in (
            (1, "I-200-OVERLAP", "2021-10-15"),
            (2, "I-200-OVERLAP", "2022-01-15"),
            (3, "I-200-Q3", "2022-04-15"),
            (4, "I-200-Q4", "2022-07-15"),
        )
    }
    current_url = "https://www.dol.gov/files/LCA_Disclosure_Data_FY2023_Q1.xlsx"
    artifact_payloads[current_url] = _xlsx_bytes("I-200-CURRENT", "2022-10-15")
    html = "".join(
        [
            *[f'<a href="{url}">{Path(url).name}</a>' for url in artifact_payloads],
            '<a href="https://www.dol.gov/files/LCA_Record_Layout_FY2022_Q4.pdf">'
            "LCA Record Layout FY2022 Q4</a>",
            '<a href="https://www.dol.gov/files/LCA_Record_Layout_FY2023_Q1.pdf">'
            "LCA Record Layout FY2023 Q1</a>",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == landing_page:
            return httpx.Response(200, text=html, request=request)
        payload = artifact_payloads.get(url)
        if payload is None:
            raise AssertionError(f"Unexpected request: {request.url}")
        return httpx.Response(
            200,
            content=payload,
            headers={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content-length": str(len(payload)),
            },
            request=request,
        )

    config = (
        SourceRegistry.from_yaml()
        .get("dol_lca")
        .model_copy(update={"landing_page": landing_page, "minimum_row_count": 1})
    )
    pipeline = IngestionPipeline(
        SourceRegistry((config,)),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DataQualityError, match="unsupported selected-artifact overlap"):
        pipeline.ingest("dol_lca", from_fiscal_year=2022)

    records = pipeline.manifest_store.records()
    fy2022 = sorted(
        (record for record in records if record.fiscal_year == 2022),
        key=lambda record: record.fiscal_quarter or 0,
    )
    assert all(record.is_quarter_partition for record in fy2022)
    assert [record.coverage_start_quarter for record in fy2022] == [1, 2, 3, 4]
    assert not next(record for record in records if record.fiscal_year == 2023).is_quarter_partition


def test_pipeline_ingests_reviewed_segments_with_valid_state_supersession(
    tmp_path: Path,
) -> None:
    landing_page = "https://www.dol.gov/performance"
    artifact_payloads = {
        "https://www.dol.gov/files/LCA_Disclosure_Data_FY2023_Q1.xlsx": _xlsx_bytes(
            "I-200-2023-Q1", "2022-10-15"
        ),
        "https://www.dol.gov/files/LCA_Disclosure_Data_FY2023_Q2.xlsx": _xlsx_bytes(
            "I-200-2023-SUPERSESSION",
            "2022-10-15",
            additional_rows=(("I-200-2023-Q2-Q2", "2023-01-15"),),
        ),
        "https://www.dol.gov/files/LCA_Disclosure_Data_FY2023_Q3.xlsx": _xlsx_bytes(
            "I-200-2023-SUPERSESSION",
            "2023-04-15",
            case_status="Certified - Withdrawn",
        ),
        "https://www.dol.gov/files/LCA_Disclosure_Data_FY2023_Q4.xlsx": _xlsx_bytes(
            "I-200-2023-Q4", "2023-07-15"
        ),
    }
    current_url = "https://www.dol.gov/files/LCA_Disclosure_Data_FY2024_Q1.xlsx"
    artifact_payloads[current_url] = _xlsx_bytes("I-200-CURRENT", "2023-10-15")
    html = "".join(
        [
            *[f'<a href="{url}">{Path(url).name}</a>' for url in artifact_payloads],
            '<a href="https://www.dol.gov/files/LCA_Record_Layout_FY2023_Q4.pdf">'
            "LCA Record Layout FY2023 Q4</a>",
            '<a href="https://www.dol.gov/files/LCA_Record_Layout_FY2024_Q1.pdf">'
            "LCA Record Layout FY2024 Q1</a>",
        ]
    )
    requested_artifacts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == landing_page:
            return httpx.Response(200, text=html, request=request)
        payload = artifact_payloads.get(url)
        if payload is None:
            raise AssertionError(f"Unexpected request: {request.url}")
        requested_artifacts.append(url)
        return httpx.Response(
            200,
            content=payload,
            headers={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content-length": str(len(payload)),
            },
            request=request,
        )

    config = (
        SourceRegistry.from_yaml()
        .get("dol_lca")
        .model_copy(update={"landing_page": landing_page, "minimum_row_count": 1})
    )
    pipeline = IngestionPipeline(
        SourceRegistry((config,)),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
        transport=httpx.MockTransport(handler),
    )

    summary = pipeline.ingest("dol_lca", from_fiscal_year=2023)

    completed = sorted(
        (record for record in summary.records if record.fiscal_year == 2023),
        key=lambda record: record.fiscal_quarter or 0,
    )
    completed_rows = pl.concat(
        [pl.read_parquet(record.parquet_path) for record in completed],
        how="diagonal_relaxed",
    )
    assert [(record.coverage_start_quarter, record.fiscal_quarter) for record in completed] == [
        (1, 2),
        (3, 3),
        (4, 4),
    ]
    assert all(record.is_quarter_partition for record in completed)
    assert completed_rows.height == 4
    assert completed_rows["case_id"].n_unique() == 3
    assert {Path(url).name for url in requested_artifacts} == {
        "LCA_Disclosure_Data_FY2023_Q2.xlsx",
        "LCA_Disclosure_Data_FY2023_Q3.xlsx",
        "LCA_Disclosure_Data_FY2023_Q4.xlsx",
        "LCA_Disclosure_Data_FY2024_Q1.xlsx",
    }
