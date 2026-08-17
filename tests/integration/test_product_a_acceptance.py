"""Fixture-level checks for the Product A real-data acceptance runner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from sponsor_intel.scoring import (
    ProductAScoringConfig,
    score_employers_product_a,
    score_institutions_product_a,
)

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_product_a_acceptance.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location("run_product_a_acceptance", _SCRIPT_PATH)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = _SCRIPT_MODULE
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)
REPORT_FILES = _SCRIPT_MODULE.REPORT_FILES
source_selection = _SCRIPT_MODULE._source_selection
main = _SCRIPT_MODULE.main
run_acceptance = _SCRIPT_MODULE.run_acceptance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_specs() -> list[tuple[str, int, int | None, bool, str]]:
    return [
        *[
            (
                "dol_lca",
                year,
                quarter,
                False,
                f"LCA_Disclosure_Data_FY{year}_Q{quarter}.xlsx",
            )
            for year in (2022, 2024)
            for quarter in range(1, 5)
        ],
        *[
            (
                "dol_lca",
                year,
                quarter,
                False,
                f"LCA_Disclosure_Data_FY{year}_Q{quarter}.xlsx",
            )
            for year in (2023,)
            for quarter in (2, 3, 4)
        ],
        ("dol_lca", 2025, 3, True, "LCA_Disclosure_Data_FY2025_Q3.xlsx"),
        ("dol_perm", 2022, 4, False, "PERM_Disclosure_Data_FY2022_Q4.xlsx"),
        ("dol_perm", 2023, 4, False, "PERM_Disclosure_Data_FY2023_Q4.xlsx"),
        ("dol_perm", 2024, 4, False, "PERM_Disclosure_Data_FY2024_Q4.xlsx"),
        (
            "dol_perm",
            2024,
            4,
            False,
            "PERM_Disclosure_Data_New_Form_FY2024_Q4.xlsx",
        ),
        ("dol_perm", 2025, 3, True, "PERM_Disclosure_Data_FY2025_Q3.xlsx"),
        ("uscis_h1b", 2022, None, False, "H1BPublic_FY2022.csv"),
        ("uscis_h1b", 2023, None, False, "H1BPublic_FY2023.csv"),
        ("uscis_h1b", 2024, None, False, "H1BPublic_FY2024.csv"),
        ("uscis_h1b", 2025, 3, True, "H1BPublic_FY2025.csv"),
        ("ipeds", 2025, None, False, "HD2025.zip"),
        ("ipeds", 2025, None, False, "IC2025.zip"),
        ("herd", 2022, None, False, "higher_education_r_and_d_2022.zip"),
        ("herd", 2022, None, False, "higher_education_r_and_d_2022_short.zip"),
        ("herd", 2023, None, False, "higher_education_r_and_d_2023.zip"),
        ("herd", 2023, None, False, "higher_education_r_and_d_2023_short.zip"),
        ("herd", 2024, None, False, "higher_education_r_and_d_2024.zip"),
        ("herd", 2024, None, False, "higher_education_r_and_d_2024_short.zip"),
        (
            "sevp_opt",
            2024,
            None,
            False,
            "2024_Top200_Employers_OPT_STEM_OPT_Students.pdf",
        ),
    ]


def _source_artifacts(root: Path) -> tuple[pl.DataFrame, Path, dict[str, str]]:
    manifest_path = root / "outputs" / "manifests" / "source_artifacts.jsonl"
    manifest_path.parent.mkdir(parents=True)
    raw_root = root / "data" / "raw"
    rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    artifact_ids: dict[str, str] = {}
    for index, (source_id, year, quarter, partial, file_name) in enumerate(_artifact_specs()):
        artifact_id = f"artifact_{index:02d}"
        artifact_ids[f"{source_id}:{year}:{file_name}"] = artifact_id
        raw_path = raw_root / source_id / file_name
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(f"fixture:{artifact_id}\n".encode())
        checksum = _sha256(raw_path)
        hostname = {
            "dol_lca": "www.dol.gov",
            "dol_perm": "www.dol.gov",
            "herd": "ncses.nsf.gov",
            "ipeds": "nces.ed.gov",
            "sevp_opt": "www.ice.gov",
            "uscis_h1b": "www.uscis.gov",
        }[source_id]
        download_url = f"https://{hostname}/{source_id}/{file_name}"
        record_layout_url = (
            f"https://nces.ed.gov/ipeds/{Path(file_name).stem}_Dict.zip"
            if source_id == "ipeds"
            else None
        )
        staging_path = root / "data" / "staging" / f"{artifact_id}.parquet"
        if source_id == "dol_lca":
            supersession_fixture = (year, quarter) in {(2022, 1), (2023, 4)}
            case_id = "lca-cross-year" if supersession_fixture else f"case-{artifact_id}"
            case_status = (
                "CERTIFIED"
                if year == 2022 and quarter == 1
                else "Certified - Withdrawn"
                if year == 2023 and quarter == 4
                else "DENIED"
            )
            quarter_key = quarter if quarter is not None else 4
            decision_date = {
                1: f"{year - 1}-12-15",
                2: f"{year}-03-15",
                3: f"{year}-06-15",
                4: f"{year}-09-15",
            }[quarter_key]
            employer_name = (
                "\u00bfR\u00c3\u00b6chling\u00a0LLC"
                if (year, quarter) == (2022, 1)
                else "R\u00f6chling LLC"
                if (year, quarter) == (2023, 4)
                else "Google LLC"
            )
            employer_address = (
                "1 O\u2019Brien\u2013Plaza"
                if (year, quarter) == (2022, 1)
                else "1 OBrien\u201cPlaza"
                if (year, quarter) == (2023, 4)
                else "1 Legal Plaza"
            )
            employer_postal_code = (
                "2109"
                if (year, quarter) == (2022, 1)
                else "02109"
                if (year, quarter) == (2023, 4)
                else "98101"
            )
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(
                {
                    "source_row_number": [1],
                    "case_id": [case_id],
                    "case_status": [case_status],
                    "decision_date": pl.Series([decision_date]).str.to_date(),
                    "employer_name_raw": [employer_name],
                    "visa_class": ["H-1B"],
                    "employer_address_1": [employer_address],
                    "employer_address_2": [None],
                    "employer_city": ["Seattle"],
                    "employer_state": ["WA"],
                    "employer_postal_code": [employer_postal_code],
                }
            ).write_parquet(staging_path)
        elif source_id == "dol_perm":
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(
                {
                    "source_row_number": [1],
                    "form_version": ["new_form" if "New_Form" in file_name else "standard"],
                }
            ).write_parquet(staging_path)
        row = {
            "source_artifact_id": artifact_id,
            "source_id": source_id,
            "authority": "Fixture federal authority",
            "landing_page_url": f"https://{hostname}/{source_id}",
            "download_url": download_url,
            "retrieved_at": "2026-08-16T00:00:00+00:00",
            "fiscal_year": year,
            "fiscal_quarter": quarter,
            "is_partial_period": partial,
            "is_quarter_partition": (
                source_id == "dol_lca" and not partial and quarter is not None
            ),
            "coverage_start_quarter": (
                1
                if source_id == "dol_lca" and (partial or (year == 2023 and quarter == 2))
                else quarter
                if source_id == "dol_lca" and quarter is not None
                else None
            ),
            "file_name": file_name,
            "mime_type": "application/octet-stream",
            "byte_size": raw_path.stat().st_size,
            "sha256": checksum,
            "record_layout_url": record_layout_url,
            "parser_version": "fixture_parser_v1",
            "schema_version": f"{source_id}_fixture_v1",
            "raw_row_count": 3,
            "normalized_row_count": 1,
            "validation_status": "PASSED",
        }
        rows.append(row)
        manifest.append(
            row
            | {
                "row_count": 1,
                "column_count": 1,
                "build_id": "fixture-build",
                "raw_path": str(raw_path),
                "parquet_path": str(staging_path),
                "schema_diff_path": str(root / "outputs" / "schema" / f"{artifact_id}.json"),
            }
        )
    manifest_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest),
        encoding="utf-8",
    )
    return pl.DataFrame(rows), manifest_path, artifact_ids


def _case(
    case_id: str,
    organization_id: str,
    *,
    source_artifact_id: str,
    fiscal_year: int,
    partial: bool,
    status: str,
    role_family: str = "software_engineering",
    visa_class: str = "H-1B",
    technical: bool = True,
    parent_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source_row_number": 1,
        "case_id": case_id,
        "source_artifact_id": source_artifact_id,
        "source_file_name": f"{source_artifact_id}.xlsx",
        "fiscal_year": fiscal_year,
        "fiscal_quarter": 3 if partial else 4,
        "is_partial_period": partial,
        "decision_date": f"{fiscal_year}-06-30" if partial else f"{fiscal_year}-09-30",
        "case_status": status,
        "visa_class": visa_class,
        "technical_role": technical,
        "role_family": role_family,
        "organization_id": organization_id,
        "legal_entity_id": organization_id,
        "parent_organization_id": parent_id,
        "employer_name_raw": organization_id.replace("legal_", "").replace("_", " ").title(),
        "employer_address_1": "1 Legal Plaza",
        "employer_city": "Seattle",
        "employer_state": "WA",
        "employer_postal_code": "98101",
        "worksite_city": "Austin",
        "worksite_state": "TX",
        "job_title_raw": "Software Engineer",
    }


def _program_metrics(
    cases: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    *,
    program: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dimension in dimensions:
        organization_id = dimension["organization_id"]
        scope = dimension["identity_scope"]
        selected = [
            row
            for row in cases
            if (
                row["parent_organization_id"] == organization_id
                if scope == "PARENT_ROLLUP"
                else row["organization_id"] == organization_id
            )
        ]
        full_status = "CERTIFIED"
        half_status = "CERTIFIED-WITHDRAWN" if program == "lca" else "CERTIFIED-EXPIRED"

        def relevant(row: dict[str, Any]) -> bool:
            return bool(row["technical_role"]) and (program != "lca" or row["visa_class"] == "H-1B")

        def normalized_status(row: dict[str, Any]) -> str:
            return re.sub(r"\s*-\s*", "-", str(row["case_status"]).strip().upper())

        full = [row for row in selected if relevant(row) and normalized_status(row) == full_status]
        half = [row for row in selected if relevant(row) and normalized_status(row) == half_status]
        positive = full + half
        prefix = "lca" if program == "lca" else "perm"
        values: dict[str, Any] = {
            f"{prefix}_case_count": len(selected),
            f"weighted_relevant_{prefix}_count": len(full) + len(half) * 0.5,
            f"{prefix}_active_years": len({row["fiscal_year"] for row in positive}),
            f"{prefix}_complete_active_years": len(
                {row["fiscal_year"] for row in positive if not row["is_partial_period"]}
            ),
            f"{prefix}_relevant_job_family_count": len({row["role_family"] for row in positive}),
            f"last_relevant_{prefix}_activity_year": max(
                (row["fiscal_year"] for row in positive), default=None
            ),
            f"last_{prefix}_activity_year": max(
                (row["fiscal_year"] for row in selected), default=None
            ),
        }
        if program == "lca":
            values["relevant_certified_lca_count"] = len(full)
            values["relevant_certified_withdrawn_lca_count"] = len(half)
        else:
            values["relevant_certified_perm_count"] = len(full)
            values["relevant_certified_expired_perm_count"] = len(half)
        result[organization_id] = values
    return result


def _build_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    data_root = root / "data"
    processed = data_root / "processed"
    processed.mkdir(parents=True)
    artifacts, manifest_path, artifact_ids = _source_artifacts(root)

    legal_names = {
        "legal_microsoft": "Microsoft Corporation",
        "legal_google": "Google LLC",
        "legal_amazon": "Amazon.com Services LLC",
        "legal_meta": "Meta Platforms, Inc.",
        "legal_ibm": "IBM Corporation",
        "legal_smart": "Smart Data Solutions LLC",
        "legal_small_one": "Focused Compiler Labs Inc",
        "legal_small_two": "Quiet Systems Research Inc",
        "legal_mit": "Massachusetts Institute of Technology",
        "legal_cmu": "Carnegie Mellon University",
        "legal_rice": "Rice University",
        "legal_michigan": "University of Michigan-Ann Arbor",
        "legal_illinois": "University of Illinois Urbana-Champaign",
        "legal_washington": "University of Washington-Seattle Campus",
        "legal_high_herd": "High Research University",
        "legal_strong_sponsor": "Applied Computing Institute",
    }
    institution_ids = {
        "legal_mit": "ipeds:000001",
        "legal_cmu": "ipeds:000002",
        "legal_rice": "ipeds:000003",
        "legal_michigan": "ipeds:000004",
        "legal_illinois": "ipeds:000005",
        "legal_washington": "ipeds:000006",
        "legal_high_herd": "ipeds:000007",
        "legal_strong_sponsor": "ipeds:000008",
    }
    parent_by_legal = {"legal_amazon": "parent_amazon"}
    dimensions = [
        {
            "organization_id": legal_id,
            "legal_entity_id": legal_id,
            "parent_organization_id": parent_by_legal.get(legal_id),
            "organization_name": name,
            "legal_entity_name": name,
            "parent_organization_name": (
                "Amazon" if parent_by_legal.get(legal_id) == "parent_amazon" else None
            ),
            "identity_scope": "LEGAL_ENTITY",
            "organization_type": (
                "university_private_nonprofit" if legal_id in institution_ids else "for_profit"
            ),
            "is_higher_education": legal_id in institution_ids,
        }
        for legal_id, name in legal_names.items()
    ]
    dimensions.append(
        {
            "organization_id": "parent_amazon",
            "legal_entity_id": None,
            "parent_organization_id": "parent_amazon",
            "organization_name": "Amazon",
            "legal_entity_name": None,
            "parent_organization_name": "Amazon",
            "identity_scope": "PARENT_ROLLUP",
            "organization_type": "for_profit",
            "is_higher_education": False,
        }
    )

    lca_artifact = artifact_ids["dol_lca:2024:LCA_Disclosure_Data_FY2024_Q4.xlsx"]
    lca_partial_artifact = artifact_ids["dol_lca:2025:LCA_Disclosure_Data_FY2025_Q3.xlsx"]
    lca_cases = [
        _case(
            "lca-cross-year",
            "legal_google",
            source_artifact_id=artifact_ids["dol_lca:2023:LCA_Disclosure_Data_FY2023_Q4.xlsx"],
            fiscal_year=2023,
            partial=False,
            status="Certified - Withdrawn",
            technical=False,
        ),
        _case(
            "lca-dummy-23",
            "legal_google",
            source_artifact_id=artifact_ids["dol_lca:2023:LCA_Disclosure_Data_FY2023_Q4.xlsx"],
            fiscal_year=2023,
            partial=False,
            status="CERTIFIED",
            visa_class="H-1B1 Chile",
        ),
        _case(
            "lca-microsoft",
            "legal_microsoft",
            source_artifact_id=lca_artifact,
            fiscal_year=2024,
            partial=False,
            status="CERTIFIED",
        ),
        _case(
            "lca-microsoft-partial",
            "legal_microsoft",
            source_artifact_id=lca_partial_artifact,
            fiscal_year=2025,
            partial=True,
            status="Certified - Withdrawn",
            role_family="machine_learning",
        ),
    ]
    for index, legal_id in enumerate(
        (
            "legal_amazon",
            "legal_meta",
            "legal_ibm",
            "legal_small_one",
            "legal_small_two",
            "legal_mit",
            "legal_strong_sponsor",
        )
    ):
        lca_cases.append(
            _case(
                f"lca-{index}",
                legal_id,
                source_artifact_id=lca_artifact,
                fiscal_year=2024,
                partial=False,
                status="CERTIFIED",
                parent_id=parent_by_legal.get(legal_id),
            )
        )
    lca_cases.append(
        _case(
            "lca-e3",
            "legal_google",
            source_artifact_id=lca_artifact,
            fiscal_year=2024,
            partial=False,
            status="CERTIFIED",
            visa_class="E-3 Australian",
        )
    )

    perm_artifact = artifact_ids["dol_perm:2024:PERM_Disclosure_Data_FY2024_Q4.xlsx"]
    perm_partial_artifact = artifact_ids["dol_perm:2025:PERM_Disclosure_Data_FY2025_Q3.xlsx"]
    perm_cases = [
        _case(
            "perm-dummy-22",
            "legal_google",
            source_artifact_id=artifact_ids["dol_perm:2022:PERM_Disclosure_Data_FY2022_Q4.xlsx"],
            fiscal_year=2022,
            partial=False,
            status="DENIED",
        ),
        _case(
            "perm-dummy-23",
            "legal_google",
            source_artifact_id=artifact_ids["dol_perm:2023:PERM_Disclosure_Data_FY2023_Q4.xlsx"],
            fiscal_year=2023,
            partial=False,
            status="WITHDRAWN",
        ),
        _case(
            "perm-microsoft",
            "legal_microsoft",
            source_artifact_id=perm_artifact,
            fiscal_year=2024,
            partial=False,
            status="CERTIFIED",
        ),
        _case(
            "perm-partial",
            "legal_microsoft",
            source_artifact_id=perm_partial_artifact,
            fiscal_year=2025,
            partial=True,
            status="DENIED",
        ),
    ]
    for index, legal_id in enumerate(
        (
            "legal_amazon",
            "legal_meta",
            "legal_ibm",
            "legal_small_one",
            "legal_small_two",
            "legal_mit",
            "legal_strong_sponsor",
        )
    ):
        perm_cases.append(
            _case(
                f"perm-{index}",
                legal_id,
                source_artifact_id=perm_artifact,
                fiscal_year=2024,
                partial=False,
                status=("Certified - Expired" if index == 0 else "CERTIFIED"),
                parent_id=parent_by_legal.get(legal_id),
            )
        )
    for index in range(3):
        lca_cases.append(
            _case(
                f"lca-strong-{index}",
                "legal_strong_sponsor",
                source_artifact_id=lca_artifact,
                fiscal_year=2024,
                partial=False,
                status="CERTIFIED",
                role_family=f"family_{index}",
            )
        )
        perm_cases.append(
            _case(
                f"perm-strong-{index}",
                "legal_strong_sponsor",
                source_artifact_id=perm_artifact,
                fiscal_year=2024,
                partial=False,
                status="CERTIFIED",
                role_family=f"family_{index}",
            )
        )

    lca_metrics = _program_metrics(lca_cases, dimensions, program="lca")
    perm_metrics = _program_metrics(perm_cases, dimensions, program="perm")
    uscis_rows = [
        {
            "source_row_number": index + 1,
            "source_artifact_id": artifact_ids[f"uscis_h1b:{year}:H1BPublic_FY{year}.csv"],
            "source_file_name": f"H1BPublic_FY{year}.csv",
            "fiscal_year": year,
            "is_partial_period": year == 2025,
            "employer_name_raw": "Microsoft Corporation",
            "organization_id": "legal_microsoft",
            "legal_entity_id": "legal_microsoft",
            "parent_organization_id": None,
            "initial_approvals": 10 if year == 2024 else 0,
            "initial_denials": 0,
            "continuing_approvals": 0,
            "continuing_denials": 0,
        }
        for index, year in enumerate((2022, 2023, 2024, 2025))
    ]
    uscis_by_org: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in uscis_rows:
        uscis_by_org[row["organization_id"]].append(row)

    metric_rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        organization_id = dimension["organization_id"]
        source_uscis = (
            [row for row in uscis_rows if row["parent_organization_id"] == organization_id]
            if dimension["identity_scope"] == "PARENT_ROLLUP"
            else uscis_by_org[organization_id]
        )
        metric_rows.append(
            dimension
            | lca_metrics[organization_id]
            | perm_metrics[organization_id]
            | {
                "initial_approvals": sum(row["initial_approvals"] for row in source_uscis),
                "uscis_employer_year_rows": len(source_uscis),
                "entity_resolution_valid": True,
                "h1b_entity_resolution_valid": True,
                "perm_entity_resolution_valid": True,
                "entity_coverage_state": "COMPLETE_ENTITY_COVERAGE",
                "h1b_entity_coverage_state": "COMPLETE_ENTITY_COVERAGE",
                "perm_entity_coverage_state": "COMPLETE_ENTITY_COVERAGE",
                "has_unresolved_h1b_candidate_evidence": False,
                "has_unresolved_perm_candidate_evidence": False,
                "lca_source_valid": True,
                "perm_source_valid": True,
                "uscis_source_valid": True,
                "lca_complete_fiscal_year_count": 3,
                "perm_complete_fiscal_year_count": 3,
                "latest_complete_immigration_fiscal_year": 2024,
                "current_partial_immigration_fiscal_year": 2025,
                "everify_status": "UNKNOWN",
                "known_opt_observation": "UNKNOWN",
                "relevant_lca_count": lca_metrics[organization_id]["relevant_certified_lca_count"],
            }
        )
    employer_metrics = score_employers_product_a(
        pl.DataFrame(metric_rows), ProductAScoringConfig.from_yaml()
    )

    legal_entities = pl.DataFrame(
        [
            {
                "legal_entity_id": legal_id,
                "legal_name": name,
                "parent_organization_id": parent_by_legal.get(legal_id),
                "city": "Seattle",
                "state": "WA",
                "postal_code": "98101",
                "review_status": "DETERMINISTIC",
                "created_by": "FIXTURE",
            }
            for legal_id, name in legal_names.items()
        ]
    )
    parents = pl.DataFrame(
        {
            "parent_organization_id": ["parent_amazon"],
            "canonical_name": ["Amazon"],
            "review_status": ["MANUAL_OVERRIDE"],
        }
    )
    institution_rows = [
        {
            "institution_id": institution_id,
            "official_name": legal_names[legal_id],
            "organization_id": legal_id,
            "legal_entity_id": legal_id,
            "legal_entity_name": legal_names[legal_id],
            "legal_employer_name": legal_names[legal_id],
            "parent_organization_id": None,
            "parent_organization_name": None,
            "identity_scope": "LEGAL_ENTITY",
            "computing_rd": 1_000.0 if legal_id == "legal_high_herd" else 10.0,
            "engineering_rd": 2_000.0 if legal_id == "legal_high_herd" else 20.0,
            "total_rd": 5_000.0 if legal_id == "legal_high_herd" else 50.0,
            "has_computing_rd_data": True,
            "has_engineering_rd_data": True,
            "has_total_rd_data": True,
        }
        for legal_id, institution_id in institution_ids.items()
    ]
    institution_base = pl.DataFrame(institution_rows).join(
        pl.DataFrame(metric_rows), on="organization_id", how="left", suffix="_metric"
    )
    institution_scored = score_institutions_product_a(
        institution_base, ProductAScoringConfig.from_yaml()
    )
    sponsorship_columns = [
        column
        for column in employer_metrics.columns
        if column.startswith("h1b_history_")
        or column.startswith("green_card_history_")
        or column.startswith("overall_sponsorship_")
        or column
        in {
            "score_version",
            "metric_version",
            "score_count_percentile_cap",
            "h1b_volume_p95_cap",
            "uscis_initial_approvals_p95_cap",
            "green_card_volume_p95_cap",
        }
    ]
    institution_metrics = institution_scored.drop(
        [column for column in sponsorship_columns if column in institution_scored.columns]
    ).join(
        employer_metrics.select("organization_id", *sponsorship_columns),
        on="organization_id",
        how="left",
    )

    institutions = pl.DataFrame(
        [
            {
                "institution_id": row["institution_id"],
                "official_name": row["official_name"],
                "legal_entity_id": row["organization_id"],
                "source_artifact_id": artifact_ids["ipeds:2025:HD2025.zip"],
                "directory_year": 2025,
                "release_status": "FINAL",
                "is_finalized": True,
                "characteristics_source_artifact_id": (
                    None
                    if row["institution_id"] == "ipeds:000008"
                    else artifact_ids["ipeds:2025:IC2025.zip"]
                ),
                "characteristics_year": (None if row["institution_id"] == "ipeds:000008" else 2025),
            }
            for row in institution_rows
        ]
    )
    herd = pl.DataFrame(
        [
            {
                "inst_id": f"herd:{index:06d}",
                "institution_id": row["institution_id"],
                "survey_year": 2024,
                "computing_rd": row["computing_rd"],
            }
            for index, row in enumerate(institution_rows)
        ]
    )
    health = pl.DataFrame(
        [
            {
                "source_id": source_id,
                "row_count": 1,
                "has_partial_period": source_id in {"dol_lca", "dol_perm", "uscis_h1b"},
                "freshness_warning": (
                    "Partial FY2025 data must not be annualized or compared with complete years."
                    if source_id in {"dol_lca", "dol_perm", "uscis_h1b"}
                    else None
                ),
            }
            for source_id in sorted({spec[0] for spec in _artifact_specs()})
        ]
    )
    quality = pl.DataFrame(
        {
            "check_id": ["fixture_product_a"],
            "critical": [True],
            "status": ["PASS"],
        }
    )
    entity_aliases = pl.DataFrame(
        {
            "alias_id": ["alias_unresolved"],
            "observation_id": ["observation_unresolved"],
            "alias_raw": ["Ambiguous Fixture Employer"],
            "source_id": ["dol_lca"],
            "city": ["Austin"],
            "state": ["TX"],
            "postal_code": ["78701"],
            "candidate_legal_entity_id": pl.Series([None], dtype=pl.String),
            "legal_entity_id": pl.Series([None], dtype=pl.String),
            "parent_organization_id": pl.Series([None], dtype=pl.String),
            "match_method": ["NO_MATCH"],
            "match_score": [None],
            "candidate_margin": [None],
            "match_status": ["UNRESOLVED"],
            "review_status": ["UNRESOLVED"],
            "resolution_reason": ["No safe fixture match"],
            "occurrence_count": [1],
        }
    )
    everify = pl.DataFrame({"organization_id": ["legal_microsoft"], "status": ["UNKNOWN"]})
    opt = pl.DataFrame({"organization_id": ["legal_microsoft"], "is_positive": [True]})

    tables = {
        "data_health": health,
        "employer_metrics": employer_metrics,
        "h1b_petitions_resolved": pl.DataFrame(uscis_rows),
        "herd_observations": herd,
        "institution_metrics": institution_metrics,
        "institutions": institutions,
        "lca_cases_resolved": pl.DataFrame(lca_cases),
        "legal_entities": legal_entities,
        "parent_organizations": parents,
        "perm_cases_resolved": pl.DataFrame(perm_cases).drop("visa_class"),
        "source_artifacts": artifacts,
    }
    for name, frame in tables.items():
        frame.write_parquet(processed / f"{name}.parquet")

    database = root / "db" / "fixture.duckdb"
    database.parent.mkdir()
    with duckdb.connect(str(database)) as connection:
        for name, frame in tables.items():
            connection.register(f"_{name}", frame)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")
            connection.unregister(f"_{name}")
        for name, frame in (
            ("quality_checks", quality),
            ("entity_aliases", entity_aliases),
            ("everify_observations", everify),
            ("opt_employer_observations", opt),
        ):
            connection.register(f"_{name}", frame)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")
            connection.unregister(f"_{name}")
        connection.execute("CREATE VIEW vw_employer_explorer AS SELECT * FROM employer_metrics")
        connection.execute(
            "CREATE VIEW vw_institution_explorer AS SELECT * FROM institution_metrics"
        )
        connection.execute("CREATE VIEW vw_organization_detail AS SELECT * FROM employer_metrics")
        connection.execute("CREATE VIEW vw_h1b_trends AS SELECT * FROM lca_cases_resolved")
        connection.execute("CREATE VIEW vw_perm_trends AS SELECT * FROM perm_cases_resolved")
        connection.execute("CREATE VIEW vw_relevant_titles AS SELECT * FROM lca_cases_resolved")
        connection.execute("CREATE VIEW vw_everify_evidence AS SELECT * FROM everify_observations")
        connection.execute("CREATE VIEW vw_opt_evidence AS SELECT * FROM opt_employer_observations")
        connection.execute(
            "CREATE VIEW vw_entity_review_queue AS "
            "SELECT * FROM entity_aliases WHERE match_status = 'UNRESOLVED'"
        )
        connection.execute("CREATE VIEW vw_data_health AS SELECT * FROM data_health")
        connection.execute("CREATE VIEW vw_source_artifacts AS SELECT * FROM source_artifacts")
        connection.execute("CREATE VIEW vw_quality_checks AS SELECT * FROM quality_checks")
    output_root = root / "outputs" / "reports" / "product-a"
    return data_root, database, output_root, manifest_path


def test_product_a_acceptance_writes_exact_report_family(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    output_root.mkdir(parents=True)
    (output_root / "acceptance.json").write_text('{"passed": true}\n', encoding="utf-8")
    (output_root / "stale-extra.txt").write_text("stale\n", encoding="utf-8")

    report = run_acceptance(
        data_root=data_root,
        database=database,
        output_root=output_root,
        manifest_path=manifest_path,
    )

    assert report["passed"] is True
    assert {path.name for path in output_root.iterdir()} == set(REPORT_FILES)
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    distribution = json.loads((output_root / "score-distribution.json").read_text(encoding="utf-8"))
    source_selection = json.loads(
        (output_root / "source-selection.json").read_text(encoding="utf-8")
    )
    validation = pl.read_csv(output_root / "validation.csv")
    assert acceptance["product_results"]["relevant_h1b_lca_rows"] > 0
    assert distribution["score_version"] == "product_a_scores_v1"
    assert source_selection["artifacts"][0]["raw_row_count"] == 3
    assert source_selection["artifacts"][0]["normalized_row_count"] == 1
    assert source_selection["selected_sources"] == [
        "dol_lca",
        "dol_perm",
        "herd",
        "ipeds",
        "sevp_opt",
        "uscis_h1b",
    ]
    assert source_selection["lca_global_supersessions"] == {
        "duplicate_case_ids": 1,
        "failure_count": 0,
        "permitted_supersessions": 1,
    }
    assert "| 3 | 1 |" in (output_root / "source-selection.md").read_text(encoding="utf-8")
    ipeds_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "ipeds_finalized_identity_contract"
    )
    h1b_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "h1b_only_status_weighting"
    )
    perm_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "perm_status_weighting"
    )
    supersession_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "lca_global_state_supersession"
    )
    materialization_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "lca_supersession_materialization"
    )
    assert ipeds_check["passed"] is True
    assert "IC matched=7; HD-only=1" in ipeds_check["evidence"]
    assert h1b_check["passed"] is True
    assert perm_check["passed"] is True
    assert supersession_check["passed"] is True
    assert materialization_check["passed"] is True
    assert acceptance["product_results"]["h1b1_e3_queryable_rows"] == 2
    assert set(validation["target"]) >= {
        "Microsoft legal entity",
        "Amazon parent rollup",
        "Massachusetts Institute of Technology",
    }
    assert "Employer-level H-1B initial approvals" not in validation.columns
    assert (output_root / "unresolved-entities.csv").stat().st_size > 0


def test_product_a_acceptance_removes_stale_reports_before_early_failure(
    tmp_path: Path,
) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    output_root.mkdir(parents=True)
    (output_root / "acceptance.json").write_text('{"passed": true}\n', encoding="utf-8")
    database.unlink()

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 2
    assert not output_root.exists()


def test_product_a_acceptance_recomputes_caps_instead_of_trusting_metadata(
    tmp_path: Path,
) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    employer_path = data_root / "processed" / "employer_metrics.parquet"
    pl.read_parquet(employer_path).with_columns(
        pl.lit(999.0).alias("h1b_volume_p95_cap")
    ).write_parquet(employer_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE employer_metrics SET h1b_volume_p95_cap = 999.0")

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    distribution = json.loads((output_root / "score-distribution.json").read_text("utf-8"))
    assert distribution["formula_audit"]["cap_mismatches"] == 1
    assert distribution["formula_audit"]["formula_mismatches"] == 0
    assert distribution["independently_recomputed_caps"]["h1b_volume_p95_cap"] != 999.0


def test_product_a_acceptance_rejects_filename_only_perm_variants(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    new_form = next(
        row
        for row in manifest_rows
        if row["source_id"] == "dol_perm"
        and row["fiscal_year"] == 2024
        and "New_Form" in row["file_name"]
    )
    staging_path = Path(new_form["parquet_path"])
    pl.read_parquet(staging_path).with_columns(
        pl.lit("standard").alias("form_version")
    ).write_parquet(staging_path)

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    variant_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "perm_form_variants"
    )
    assert variant_check["passed"] is False
    assert "form_version metadata" in variant_check["evidence"]


def test_product_a_acceptance_fails_without_two_real_smaller_companies(
    tmp_path: Path,
) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    employer_path = data_root / "processed" / "employer_metrics.parquet"
    employers = pl.read_parquet(employer_path).with_columns(
        pl.when(pl.col("organization_id").is_in(["legal_small_one", "legal_small_two"]))
        .then(True)
        .otherwise(pl.col("is_higher_education"))
        .alias("is_higher_education")
    )
    employers.write_parquet(employer_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE employer_metrics SET is_higher_education = true "
            "WHERE organization_id IN ('legal_small_one', 'legal_small_two')"
        )

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    representative = next(
        check for check in acceptance["checks"] if check["check_id"] == "representative_validation"
    )
    assert representative["passed"] is False
    assert "selected-real smaller rows=0" in representative["evidence"]
    validation = pl.read_csv(output_root / "validation.csv").filter(
        pl.col("category") == "smaller_company"
    )
    assert validation["selection_status"].to_list() == ["UNRESOLVED", "UNRESOLVED"]


def test_product_a_acceptance_fails_without_two_real_institution_contrasts(
    tmp_path: Path,
) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    institution_path = data_root / "processed" / "institution_metrics.parquet"
    institutions = pl.read_parquet(institution_path).with_columns(
        pl.lit("UNRATED").alias("research_scale_status")
    )
    institutions.write_parquet(institution_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("UPDATE institution_metrics SET research_scale_status = 'UNRATED'")

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    representative = next(
        check for check in acceptance["checks"] if check["check_id"] == "representative_validation"
    )
    assert representative["passed"] is False
    assert "selected-real contrast rows=0" in representative["evidence"]
    validation = pl.read_csv(output_root / "validation.csv").filter(
        pl.col("category") == "institution_contrast"
    )
    assert validation["selection_status"].to_list() == ["UNRESOLVED", "UNRESOLVED"]


def test_product_a_acceptance_returns_nonzero_for_formula_drift(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    employer_path = data_root / "processed" / "employer_metrics.parquet"
    employers = pl.read_parquet(employer_path).with_columns(
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(99.0)
        .otherwise(pl.col("overall_sponsorship_score"))
        .alias("overall_sponsorship_score")
    )
    employers.write_parquet(employer_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE employer_metrics SET overall_sponsorship_score = 99 "
            "WHERE organization_id = 'legal_microsoft'"
        )

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    formula_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "deterministic_score_formula"
    )
    assert formula_check["passed"] is False


def test_product_a_acceptance_rejects_missed_spaced_secondary_statuses(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    employer_path = data_root / "processed" / "employer_metrics.parquet"
    employers = pl.read_parquet(employer_path).with_columns(
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.col("relevant_certified_withdrawn_lca_count") - 1)
        .otherwise(pl.col("relevant_certified_withdrawn_lca_count"))
        .alias("relevant_certified_withdrawn_lca_count"),
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.col("weighted_relevant_lca_count") - 0.5)
        .otherwise(pl.col("weighted_relevant_lca_count"))
        .alias("weighted_relevant_lca_count"),
        pl.when(pl.col("organization_id") == "legal_amazon")
        .then(pl.col("relevant_certified_expired_perm_count") - 1)
        .otherwise(pl.col("relevant_certified_expired_perm_count"))
        .alias("relevant_certified_expired_perm_count"),
        pl.when(pl.col("organization_id") == "legal_amazon")
        .then(pl.col("weighted_relevant_perm_count") - 0.5)
        .otherwise(pl.col("weighted_relevant_perm_count"))
        .alias("weighted_relevant_perm_count"),
    )
    employers.write_parquet(employer_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE employer_metrics
            SET relevant_certified_withdrawn_lca_count =
                    relevant_certified_withdrawn_lca_count - 1,
                weighted_relevant_lca_count = weighted_relevant_lca_count - 0.5
            WHERE organization_id = 'legal_microsoft'
            """
        )
        connection.execute(
            """
            UPDATE employer_metrics
            SET relevant_certified_expired_perm_count =
                    relevant_certified_expired_perm_count - 1,
                weighted_relevant_perm_count = weighted_relevant_perm_count - 0.5
            WHERE organization_id = 'legal_amazon'
            """
        )

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    h1b_status_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "h1b_only_status_weighting"
    )
    perm_status_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "perm_status_weighting"
    )
    assert h1b_status_check["passed"] is False
    assert perm_status_check["passed"] is False


def test_product_a_acceptance_rejects_unreconciled_candidate_coverage(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE entity_aliases
            SET legal_entity_id = 'legal_microsoft',
                candidate_legal_entity_id = 'legal_mit',
                match_status = 'REVIEW_REQUIRED',
                review_status = 'REVIEW_REQUIRED'
            WHERE alias_id = 'alias_unresolved'
            """
        )

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    coverage_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "entity_coverage_semantics"
    )
    assert coverage_check["passed"] is False
    assert "H-1B expected/actual=1/0" in coverage_check["evidence"]


def test_product_a_acceptance_allows_confirmed_partial_entity_coverage(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    warning = "Rating is based on confirmed records. Additional ambiguous records were excluded."
    employer_metrics_path = data_root / "processed" / "employer_metrics.parquet"
    employer_metrics = pl.read_parquet(employer_metrics_path).with_columns(
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(True)
        .otherwise(pl.col("has_unresolved_h1b_candidate_evidence"))
        .alias("has_unresolved_h1b_candidate_evidence"),
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.lit("PARTIAL_ENTITY_COVERAGE"))
        .otherwise(pl.col("h1b_entity_coverage_state"))
        .alias("h1b_entity_coverage_state"),
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.lit("PARTIAL_ENTITY_COVERAGE"))
        .otherwise(pl.col("entity_coverage_state"))
        .alias("entity_coverage_state"),
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.col("h1b_history_explanation") + " " + warning)
        .otherwise(pl.col("h1b_history_explanation"))
        .alias("h1b_history_explanation"),
        pl.when(pl.col("organization_id") == "legal_microsoft")
        .then(pl.col("overall_sponsorship_explanation") + " " + warning)
        .otherwise(pl.col("overall_sponsorship_explanation"))
        .alias("overall_sponsorship_explanation"),
    )
    employer_metrics.write_parquet(employer_metrics_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            UPDATE entity_aliases
            SET legal_entity_id = 'legal_strong_sponsor',
                candidate_legal_entity_id = 'legal_microsoft',
                match_status = 'REVIEW_REQUIRED',
                review_status = 'REVIEW_REQUIRED'
            WHERE alias_id = 'alias_unresolved'
            """
        )
        connection.execute(
            """
            UPDATE employer_metrics
            SET has_unresolved_h1b_candidate_evidence = TRUE,
                h1b_entity_coverage_state = 'PARTIAL_ENTITY_COVERAGE',
                entity_coverage_state = 'PARTIAL_ENTITY_COVERAGE',
                h1b_history_explanation = h1b_history_explanation || ' ' || ?,
                overall_sponsorship_explanation = overall_sponsorship_explanation || ' ' || ?
            WHERE organization_id = 'legal_microsoft'
            """,
            [warning, warning],
        )

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 0
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    coverage_check = next(
        check for check in acceptance["checks"] if check["check_id"] == "entity_coverage_semantics"
    )
    assert coverage_check["passed"] is True


def test_product_a_acceptance_rejects_arbitrary_cross_year_lca_overlap(tmp_path: Path) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    q1_record = next(
        row
        for row in manifest_rows
        if row["source_id"] == "dol_lca"
        and row["fiscal_year"] == 2022
        and row["fiscal_quarter"] == 1
    )
    staging_path = Path(q1_record["parquet_path"])
    pl.read_parquet(staging_path).with_columns(
        pl.lit("2 Different Plaza").alias("employer_address_1")
    ).write_parquet(staging_path)

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    supersession_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "lca_global_state_supersession"
    )
    assert supersession_check["passed"] is False
    assert "unsupported selected-artifact overlap" in supersession_check["evidence"]


def test_product_a_acceptance_rejects_materialized_cross_year_superseded_state(
    tmp_path: Path,
) -> None:
    data_root, database, output_root, manifest_path = _build_fixture(tmp_path)
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    q1_artifact_id = next(
        row["source_artifact_id"]
        for row in manifest_rows
        if row["source_id"] == "dol_lca"
        and row["fiscal_year"] == 2022
        and row["fiscal_quarter"] == 1
    )
    earlier = pl.DataFrame(
        [
            _case(
                "lca-cross-year",
                "legal_google",
                source_artifact_id=q1_artifact_id,
                fiscal_year=2022,
                partial=False,
                status="CERTIFIED",
                technical=False,
            )
            | {"fiscal_quarter": 1, "decision_date": "2021-12-15"}
        ]
    )
    lca_path = data_root / "processed" / "lca_cases_resolved.parquet"
    pl.concat([pl.read_parquet(lca_path), earlier], how="diagonal_relaxed").write_parquet(lca_path)
    with duckdb.connect(str(database)) as connection:
        connection.register("_earlier_lca_state", earlier)
        connection.execute("INSERT INTO lca_cases_resolved SELECT * FROM _earlier_lca_state")
        connection.unregister("_earlier_lca_state")

    exit_code = main(
        [
            "--data-root",
            str(data_root),
            "--database",
            str(database),
            "--output-root",
            str(output_root),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 1
    acceptance = json.loads((output_root / "acceptance.json").read_text(encoding="utf-8"))
    materialization_check = next(
        check
        for check in acceptance["checks"]
        if check["check_id"] == "lca_supersession_materialization"
    )
    assert materialization_check["passed"] is False
    assert "superseded states still present=1" in materialization_check["evidence"]
    assert "global duplicate case IDs=1" in materialization_check["evidence"]


def test_source_selection_rejects_ipeds_without_finalized_ic_dictionary(
    tmp_path: Path,
) -> None:
    artifacts, manifest_path, _artifact_ids = _source_artifacts(tmp_path)
    artifacts = artifacts.filter(~pl.col("file_name").str.starts_with("IC"))
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    _report, checks = source_selection(
        artifacts,
        manifest_rows,
        repository_root=tmp_path,
        manifest_path=manifest_path,
    )

    ipeds_check = next(check for check in checks if check.check_id == "ipeds_finalized_hd_ic")
    assert ipeds_check.passed is False


def test_source_selection_rejects_unreviewed_lca_coverage_segments(tmp_path: Path) -> None:
    artifacts, manifest_path, _artifact_ids = _source_artifacts(tmp_path)
    bad_artifact_id = artifacts.filter(
        (pl.col("source_id") == "dol_lca")
        & (pl.col("fiscal_year") == 2023)
        & (pl.col("fiscal_quarter") == 3)
    ).item(0, "source_artifact_id")
    artifacts = artifacts.with_columns(
        pl.when(pl.col("source_artifact_id") == bad_artifact_id)
        .then(2)
        .otherwise(pl.col("coverage_start_quarter"))
        .alias("coverage_start_quarter")
    )
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in manifest_rows:
        if row["source_artifact_id"] == bad_artifact_id:
            row["coverage_start_quarter"] = 2
    manifest_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest_rows),
        encoding="utf-8",
    )

    _report, checks = source_selection(
        artifacts,
        manifest_rows,
        repository_root=tmp_path,
        manifest_path=manifest_path,
    )

    selection_check = next(
        check for check in checks if check.check_id == "dol_cumulative_selection"
    )
    assert selection_check.passed is False
    assert "FY2023 completed segments" in selection_check.evidence
