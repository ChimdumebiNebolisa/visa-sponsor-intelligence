"""Fixture-backed DOL Excel-to-Parquet integration tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import polars as pl
import pytest
import xlsxwriter

from sponsor_intel.sources.errors import SchemaDriftError
from sponsor_intel.sources.models import (
    DownloadedArtifact,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.normalizer import DolExcelNormalizer
from sponsor_intel.sources.registry import SourceRegistry


def _write_lca_fixture(path: Path, *, include_soc: bool = True) -> None:
    workbook = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet("Disclosure")
    columns = [
        "CASE_NUMBER",
        "CASE_STATUS",
        "EMPLOYER_NAME",
        "JOB_TITLE",
        "WORKSITE_STATE",
        "WAGE_RATE_OF_PAY_FROM",
        "DECISION_DATE",
    ]
    if include_soc:
        columns[4:4] = ["SOC_CODE", "SOC_TITLE"]
    worksheet.write_row(0, 0, columns)
    rows = [
        ["I-200-001", "Certified", "Example LLC", "Software Engineer"],
        ["I-200-002", "Denied", "University Example", "Systems Engineer"],
    ]
    for row_index, prefix in enumerate(rows, start=1):
        soc = ["15-1252", "Software Developers"] if include_soc else []
        values = prefix + soc + ["TX", "120,000", "2025-09-30"]
        worksheet.write_row(row_index, 0, values)
    workbook.close()


def _write_lca_formula_error_fixture(path: Path) -> None:
    workbook = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet("Disclosure")
    columns = [
        "CASE_NUMBER",
        "CASE_STATUS",
        "EMPLOYER_NAME",
        "JOB_TITLE",
        "SOC_CODE",
        "SOC_TITLE",
        "WORKSITE_STATE",
        "EMP_POC_JOB_TITLE",
    ]
    worksheet.write_row(0, 0, columns)
    worksheet.write_row(
        1,
        0,
        [
            "I-200-ERROR",
            "Certified",
            "Example LLC",
            "Software Engineer",
            "15-1252",
            "Software Developers",
            "TX",
        ],
    )
    worksheet.write_formula(1, 7, "=UNKNOWN()", None, cast(int, "#NAME?"))
    workbook.close()


def _write_duplicate_fixture(path: Path, *, exact: bool) -> None:
    workbook = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet("Disclosure")
    columns = [
        "CASE_NUMBER",
        "CASE_STATUS",
        "EMPLOYER_NAME",
        "JOB_TITLE",
        "SOC_CODE",
        "SOC_TITLE",
        "WORKSITE_STATE",
    ]
    worksheet.write_row(0, 0, columns)
    row = [
        "I-200-DUPLICATE",
        "Certified",
        "Example LLC",
        "Software Engineer",
        "15-1252",
        "Software Developers",
        "TX",
    ]
    worksheet.write_row(1, 0, row)
    worksheet.write_row(2, 0, row if exact else [row[0], "Denied", *row[2:]])
    workbook.close()


def _write_repeated_decision_fixture(path: Path) -> None:
    workbook = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet("Disclosure")
    columns = [
        "CASE_NUMBER",
        "CASE_STATUS",
        "EMPLOYER_NAME",
        "JOB_TITLE",
        "SOC_CODE",
        "SOC_TITLE",
        "WORKSITE_STATE",
        "DECISION_DATE",
    ]
    worksheet.write_row(0, 0, columns)
    row = [
        "I-200-REPEATED",
        "Certified",
        "Example LLC",
        "Software Engineer",
        "15-1252",
        "Software Developers",
        "TX",
    ]
    worksheet.write_row(1, 0, [*row, "2024-07-05"])
    worksheet.write_row(2, 0, [*row, "2024-08-12"])
    workbook.close()


def _downloaded(path: Path) -> DownloadedArtifact:
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    candidate = SourceArtifactCandidate(
        source_id="dol_lca",
        authority="U.S. Department of Labor",
        landing_page_url="https://www.dol.gov/performance",
        download_url="https://www.dol.gov/files/lca.xlsx",
        fiscal_year=2025,
        fiscal_quarter=4,
        is_partial_period=False,
        file_name=path.name,
        expected_format="xlsx",
        record_layout_url="https://www.dol.gov/files/layout.pdf",
    )
    return DownloadedArtifact(
        candidate=candidate,
        raw_path=path,
        retrieved_at=datetime.now(UTC),
        sha256=checksum,
        byte_size=path.stat().st_size,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def test_lca_normalization_writes_typed_parquet_and_provenance(tmp_path: Path) -> None:
    source_path = tmp_path / "lca.xlsx"
    _write_lca_fixture(source_path)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    normalized = normalizer.normalize(_downloaded(source_path))
    persisted = normalizer.persist(normalized)
    result = pl.read_parquet(persisted.parquet_path)

    assert result.height == 2
    assert result["employer_name_raw"].to_list() == ["Example LLC", "University Example"]
    assert result.schema["wage_from"] == pl.Float64
    assert result.schema["decision_date"] == pl.Date
    assert result["source_artifact_id"].n_unique() == 1
    assert result["is_partial_period"].to_list() == [False, False]
    assert persisted.schema_diff_path.is_file()


def test_missing_required_column_fails_closed_with_schema_report(tmp_path: Path) -> None:
    source_path = tmp_path / "lca-missing-soc.xlsx"
    _write_lca_fixture(source_path, include_soc=False)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    with pytest.raises(SchemaDriftError, match="soc_code"):
        normalizer.normalize(_downloaded(source_path))

    assert list((tmp_path / "outputs" / "schema" / "dol_lca").glob("*.json"))


def test_configured_source_variant_absence_is_preserved_as_unknown(tmp_path: Path) -> None:
    source_path = tmp_path / "perm-new-form-missing-soc.xlsx"
    _write_lca_fixture(source_path, include_soc=False)
    config = SourceRegistry.from_yaml().get("dol_perm").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")
    artifact = _downloaded(source_path)
    candidate = artifact.candidate.model_copy(
        update={"source_id": "dol_perm", "fiscal_year": 2024, "variant": "new_form"}
    )

    normalized = normalizer.normalize(artifact.model_copy(update={"candidate": candidate}))

    assert normalized.frame["soc_code"].null_count() == normalized.frame.height
    assert normalized.frame["soc_title"].null_count() == normalized.frame.height
    assert normalized.validation.status is ValidationStatus.WARNING
    assert any(
        issue.category == "known_source_schema_absence" for issue in normalized.validation.issues
    )


def test_formula_error_cell_uses_compatible_excel_fallback(tmp_path: Path) -> None:
    source_path = tmp_path / "lca-error-cell.xlsx"
    _write_lca_formula_error_fixture(source_path)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    normalized = normalizer.normalize(_downloaded(source_path))

    assert normalized.frame["case_id"].to_list() == ["I-200-ERROR"]


def test_exact_duplicate_source_rows_are_removed_with_warning(tmp_path: Path) -> None:
    source_path = tmp_path / "lca-exact-duplicate.xlsx"
    _write_duplicate_fixture(source_path, exact=True)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    normalized = normalizer.normalize(_downloaded(source_path))

    assert normalized.frame.height == 1
    assert normalized.frame["source_row_number"].to_list() == [2]
    assert normalized.validation.status is ValidationStatus.WARNING
    assert any(
        issue.category == "exact_duplicate_source_rows" and issue.details["count"] == 1
        for issue in normalized.validation.issues
    )


def test_nonidentical_duplicate_case_ids_still_fail_validation(tmp_path: Path) -> None:
    source_path = tmp_path / "lca-conflicting-duplicate.xlsx"
    _write_duplicate_fixture(source_path, exact=False)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    normalized = normalizer.normalize(_downloaded(source_path))

    assert normalized.frame.height == 2
    assert normalized.validation.status is ValidationStatus.FAILED
    assert any(issue.category == "duplicate_case_id" for issue in normalized.validation.issues)


def test_repeated_case_decisions_collapse_to_latest_date(tmp_path: Path) -> None:
    source_path = tmp_path / "lca-repeated-decision.xlsx"
    _write_repeated_decision_fixture(source_path)
    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    normalizer = DolExcelNormalizer(config, tmp_path / "staging", tmp_path / "outputs")

    normalized = normalizer.normalize(_downloaded(source_path))

    assert normalized.frame.height == 1
    assert normalized.frame["decision_date"].dt.to_string("%Y-%m-%d").to_list() == ["2024-08-12"]
    assert normalized.frame["source_row_number"].to_list() == [3]
    assert normalized.validation.status is ValidationStatus.WARNING
    assert any(
        issue.category == "repeated_case_decisions" and issue.details["count"] == 1
        for issue in normalized.validation.issues
    )
