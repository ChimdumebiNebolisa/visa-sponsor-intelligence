"""End-to-end fixture tests for Phase 2 federal and institution sources."""

from __future__ import annotations

import csv
import json
import zipfile
from io import BytesIO, StringIO
from pathlib import Path

import httpx
import polars as pl

from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import SourceRegistry


def _csv_bytes(header: list[str], rows: list[list[object]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _zip_bytes(member_name: str, content: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, content)
    return buffer.getvalue()


def test_uscis_pipeline_preserves_petition_decisions_as_separate_evidence(
    tmp_path: Path,
) -> None:
    landing = "https://www.uscis.gov/tools/h1b-hub"
    artifact_url = "https://bigdataanalyticspub-sb.uscis.dhs.gov/views/test/H1BPublic.csv"
    filtered_artifact_url = f"{artifact_url}?Fiscal%20Year%20%20%20=2026"
    guide_url = "https://www.uscis.gov/tools/understanding-h1b"
    landing_html = (
        "<p>Fiscal year 2009 through fiscal year 2026 (quarter 3).</p>"
        f'<a href="{guide_url}">Understanding Our H-1B Employer Data Hub</a>'
    )
    header = [
        "Employer (Petitioner) Name",
        "Fiscal Year   ",
        "Industry (NAICS) Code",
        "Measure Names",
        "Petitioner City",
        "Petitioner State",
        "Petitioner Zip Code",
        "Tax ID",
        "Line by line",
        "Measure Values",
    ]
    measures = [
        "New Employment Approval",
        "New Employment Denial",
        "Continuation Approval",
        "Continuation Denial",
        "Change with Same Employer Approval",
        "Change with Same Employer Denial",
        "New Concurrent Approval",
        "New Concurrent Denial",
        "Change of Employer Approval",
        "Change of Employer Denial",
        "Amended Approval",
        "Amended Denial",
    ]
    rows = [
        ["Example University", "2026", "61", measure, "Austin", "TX", "78701", "1234", "1", value]
        for value, measure in enumerate(measures, start=1)
    ]
    csv_content = _csv_bytes(header, rows)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == landing:
            return httpx.Response(200, text=landing_html, request=request)
        if str(request.url) == filtered_artifact_url:
            return httpx.Response(
                200,
                content=csv_content,
                headers={"content-type": "text/csv"},
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    config = (
        SourceRegistry.from_yaml()
        .get("uscis_h1b")
        .model_copy(
            update={
                "landing_page": landing,
                "artifact_url": artifact_url,
                "minimum_row_count": 1,
            }
        )
    )
    pipeline = IngestionPipeline(
        SourceRegistry((config,)),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
        transport=httpx.MockTransport(handler),
    )
    summary = pipeline.ingest("uscis_h1b", from_fiscal_year=2026)
    frame = pl.read_parquet(summary.records[0].parquet_path)

    assert frame.height == 1
    assert frame["employer_name_raw"].to_list() == ["Example University"]
    assert frame["initial_approvals"].to_list() == [1]
    assert frame["continuing_denials"].to_list() == [4]
    assert frame["evidence_type"].to_list() == ["USCIS_H1B_PETITION_DECISIONS"]
    assert frame["legal_entity_id"].null_count() == 1
    validation_report = json.loads(summary.records[0].schema_diff_path.read_text())
    assert validation_report["validation_status"] == "PASSED"
    assert validation_report["validation_issues"] == []


_IPEDS_HEADER = [
    "UNITID",
    "INSTNM",
    "CITY",
    "STABBR",
    "ZIP",
    "WEBADDR",
    "SECTOR",
    "CONTROL",
    "HLOFFER",
    "CYACTIVE",
    "ACT",
    "F1SYSNAM",
]
_HERD_STANDARD_HEADER = [
    "inst_id",
    "year",
    "ncses_inst_id",
    "ipeds_unitid",
    "hbcu_flag",
    "med_sch_flag",
    "hhe_flag",
    "toi_code",
    "hdg_code",
    "toc_code",
    "inst_name_long",
    "inst_city",
    "inst_state_code",
    "inst_zip",
    "questionnaire_no",
    "question",
    "row",
    "column",
    "data",
    "status",
    "othinfo",
    "othinfo_s",
    "standardized_agency_names",
]
_HERD_SHORT_HEADER = _HERD_STANDARD_HEADER[:21]


def _herd_row(
    *,
    inst_id: str,
    unitid: str,
    name: str,
    questionnaire: str,
    row: str,
    column: str,
    value: int,
    short: bool,
) -> list[object]:
    result: list[object] = [
        inst_id,
        "2024",
        f"U{inst_id}",
        unitid,
        "0",
        "F",
        "0",
        "1",
        "1",
        "1",
        name,
        "Austin",
        "TX",
        "78701",
        questionnaire,
        "Source" if questionnaire.startswith("01") else "Expenditures",
        row,
        column,
        value,
        "",
        "",
    ]
    if not short:
        result.extend(["", ""])
    return result


def _herd_fixture(*, short: bool) -> bytes:
    inst_id = "000002" if short else "000001"
    unitid = "999999" if short else "123456"
    name = "Unmatched College" if short else "Example University"
    questions = [
        ("01.a", "Federal government", "", 50 if not short else 1),
        ("01.c", "Business", "", 10 if not short else 0),
        ("01.e", "Institution funds", "", 20 if not short else 1),
        ("01.g", "Total", "", 100 if not short else 2),
    ]
    if short:
        questions.extend(
            [
                ("02.a", "Computer and information sciences, all", "Total", 1),
                ("02.b", "Engineering, all", "Total", 0),
            ]
        )
    else:
        questions.extend(
            [
                ("09A", "Computer and information sciences, all", "Total", 5),
                ("11A", "Computer and information sciences, all", "Total", 3),
                ("09B10", "Engineering, all", "Total", 7),
                ("11B10", "Engineering, all", "Total", 2),
                ("15", "Total", "Total", 12),
            ]
        )
    rows = [
        _herd_row(
            inst_id=inst_id,
            unitid=unitid,
            name=name,
            questionnaire=questionnaire,
            row=row,
            column=column,
            value=value,
            short=short,
        )
        for questionnaire, row, column, value in questions
    ]
    return _zip_bytes(
        "short2024.csv" if short else "herd2024.csv",
        _csv_bytes(_HERD_SHORT_HEADER if short else _HERD_STANDARD_HEADER, rows),
    )


def test_ipeds_and_herd_build_exact_unitid_join_and_review_queue(tmp_path: Path) -> None:
    ipeds_landing = "https://nces.ed.gov/ipeds/files"
    ipeds_url = "https://nces.ed.gov/ipeds/complete-data-files/HD2025.zip"
    dictionary_url = "https://nces.ed.gov/ipeds/complete-data-files/HD2025_Dict.zip"
    herd_landing = "https://ncses.nsf.gov/explore-data/herd"
    herd_url = "https://ncses.nsf.gov/files/higher_education_r_and_d_2024.zip"
    herd_short_url = "https://ncses.nsf.gov/files/higher_education_r_and_d_2024_short.zip"
    ipeds_zip = _zip_bytes(
        "hd2025.csv",
        _csv_bytes(
            _IPEDS_HEADER,
            [
                [
                    "123456",
                    "Example University",
                    "Austin",
                    "TX",
                    "78701",
                    "example.edu",
                    "1",
                    "1",
                    "9",
                    "1",
                    "A",
                    "Example System",
                ]
            ],
        ),
    )
    herd_zip = _herd_fixture(short=False)
    herd_short_zip = _herd_fixture(short=True)
    ipeds_html = f'<a href="{ipeds_url}">HD2025</a><a href="{dictionary_url}">HD2025 dictionary</a>'
    herd_html = f'<a href="{herd_url}">HERD 2024</a><a href="{herd_short_url}">HERD 2024 short</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            ipeds_url: (ipeds_zip, "application/zip"),
            herd_url: (herd_zip, "application/zip"),
            herd_short_url: (herd_short_zip, "application/zip"),
        }
        if str(request.url) == ipeds_landing:
            return httpx.Response(200, text=ipeds_html, request=request)
        if str(request.url) == herd_landing:
            return httpx.Response(200, text=herd_html, request=request)
        if str(request.url) in payloads:
            content, content_type = payloads[str(request.url)]
            return httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    registry = SourceRegistry.from_yaml()
    ipeds_config = registry.get("ipeds").model_copy(
        update={"landing_page": ipeds_landing, "minimum_row_count": 1}
    )
    herd_config = registry.get("herd").model_copy(
        update={"landing_page": herd_landing, "minimum_row_count": 1}
    )
    pipeline = IngestionPipeline(
        SourceRegistry((ipeds_config, herd_config)),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
        transport=httpx.MockTransport(handler),
    )
    pipeline.ingest("ipeds", from_fiscal_year=2025)
    pipeline.ingest("herd", from_fiscal_year=2024)

    institutions = pl.read_parquet(tmp_path / "data/processed/institutions.parquet")
    observations = pl.read_parquet(tmp_path / "data/processed/herd_observations.parquet")
    review_json = json.loads(
        (tmp_path / "outputs/reports/institutions/herd_ipeds_join_review.json").read_text()
    )
    assert institutions["institution_id"].to_list() == ["ipeds:123456"]
    assert observations.height == 2
    assert set(observations["institution_review_status"].to_list()) == {
        "IDENTIFIER_MATCHED",
        "NEEDS_REVIEW",
    }
    matched = observations.filter(pl.col("institution_review_status") == "IDENTIFIER_MATCHED")
    assert matched["institution_id"].to_list() == ["ipeds:123456"]
    assert matched["total_rd"].to_list() == [100_000]
    assert matched["computing_rd"].to_list() == [8_000]
    assert review_json["join_policy"].startswith("Exact six-digit IPEDS UNITID")
    assert review_json["needs_review_count"] == 1
