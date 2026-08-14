"""End-to-end fixture test for resumable DOL ingestion."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import polars as pl
import xlsxwriter

from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import SourceRegistry


def _xlsx_bytes() -> bytes:
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
    worksheet.write_row(
        1,
        0,
        [
            "I-200-PIPELINE",
            "Certified",
            "Pipeline Example LLC",
            "Software Engineer",
            "15-1252",
            "Software Developers",
            "TX",
            "2025-09-30",
        ],
    )
    workbook.close()
    return buffer.getvalue()


def test_pipeline_ingests_and_resumes_from_raw_manifest(tmp_path: Path) -> None:
    landing_page = "https://www.dol.gov/performance"
    artifact_url = "https://www.dol.gov/files/LCA_Disclosure_Data_FY2025_Q4.xlsx"
    layout_url = "https://www.dol.gov/files/LCA_Record_Layout_FY2025_Q4.pdf"
    html = (
        f'<a href="{artifact_url}">LCA Disclosure Data FY2025 Q4</a>'
        f'<a href="{layout_url}">LCA Record Layout FY2025 Q4</a>'
    )
    workbook = _xlsx_bytes()
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
    assert pipeline.raw_manifest_store.path.is_file()
    assert pl.read_parquet(first_record.parquet_path).height == 1

    first_record.parquet_path.unlink()
    pipeline.manifest_store.path.unlink()
    resumed = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    assert resumed.ingested_artifact_count == 1
    assert resumed.reused_artifact_count == 0
    assert request_counts["artifact"] == 1

    reused = pipeline.ingest("dol_lca", from_fiscal_year=2025)
    assert reused.ingested_artifact_count == 0
    assert reused.reused_artifact_count == 1
    assert request_counts == {"landing": 3, "artifact": 1}
