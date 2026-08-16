"""Build a small, sanitized Product A presentation database for CI and UI tests."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.database import DuckDBBuilder
from sponsor_intel.metrics import MetricsPipeline
from sponsor_intel.sources.manifests import (
    ArtifactManifestStore,
    write_json_atomic,
)
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry

FIXTURE_BUILD_ID = "product-a-ci-fixture-v1"


def _write_source(
    data_root: Path,
    layer: str,
    source_id: str,
    fiscal_year: int,
    frame: pl.DataFrame,
) -> None:
    artifact_ids = frame["source_artifact_id"].unique().to_list()
    if len(artifact_ids) != 1:
        raise ValueError("Sanitized fixture source files must contain exactly one artifact")
    target = (
        data_root
        / layer
        / "sources"
        / source_id
        / f"fy={fiscal_year}"
        / f"{artifact_ids[0]}.parquet"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(target, compression="zstd", statistics=True)


def _case_frame(source_id: str, records: list[dict[str, object]], *, perm: bool) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    checksum_character = {
        "dol_lca": "a",
        "dol_lca-partial": "b",
        "dol_perm": "d",
    }.get(source_id, "f")
    for index, record in enumerate(records, start=1):
        row = {
            "case_id": f"{source_id}-ci-{index:03d}",
            "source_row_number": index,
            "source_artifact_id": f"{source_id}-ci-artifact",
            "source_file_name": f"{source_id}-sanitized-fixture.csv",
            "ingested_at": "2026-08-15T12:00:00+00:00",
            "source_url": f"https://www.dol.gov/media/{source_id}-sanitized-fixture.csv",
            "source_sha256": checksum_character * 64,
            "schema_version": "sanitized-product-a-v1",
            "fiscal_quarter": 4 if perm else None,
            "is_partial_period": False,
            "case_status": "Certified",
            "decision_date": f"{record['fiscal_year']}-06-15",
            "job_title_raw": "Software Research Engineer",
            "soc_code": "15-1252.00",
            "soc_title": "Software Developers",
            "role_family": "software_engineering",
            "technical_role": True,
            "role_confidence": 0.98,
            "classification_method": "SOC_MAPPING",
            "classification_version": "role_taxonomy_v2",
            "review_status": "NOT_REQUIRED",
            "worksite_state": record["state"],
            **record,
        }
        if perm:
            row.update(
                {
                    "wage_offer_from": 112_000,
                    "wage_offer_to": 145_000,
                    "wage_offer_unit_of_pay": "Year",
                }
            )
        else:
            row.update(
                {
                    "visa_class": "H-1B",
                    "wage_from": 110_000,
                    "wage_to": 150_000,
                    "wage_unit": "Year",
                }
            )
        rows.append(row)
    return pl.DataFrame(rows)


def _write_dimensions(data_root: Path) -> None:
    resolved = data_root / "resolved"
    resolved.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "legal_entity_id": [
                "legal_orbit",
                "legal_orbit_labs",
                "legal_aurora",
                "legal_beacon",
            ],
            "legal_name": [
                "Orbit Systems Inc",
                "Orbit Research Labs LLC",
                "Aurora Research University",
                "Beacon Technical Institute",
            ],
            "normalized_legal_name": [
                "ORBIT SYSTEMS INC",
                "ORBIT RESEARCH LABS LLC",
                "AURORA RESEARCH UNIVERSITY",
                "BEACON TECHNICAL INSTITUTE",
            ],
            "parent_organization_id": ["parent_orbit", "parent_orbit", None, None],
            "city": ["Austin", "Austin", "Madison", "Denver"],
            "state": ["TX", "TX", "WI", "CO"],
            "postal_code": ["78701", "78701", "53706", "80202"],
            "country": ["US", "US", "US", "US"],
            "organization_type": [
                "TECHNOLOGY",
                "TECHNOLOGY",
                "HIGHER_EDUCATION",
                "HIGHER_EDUCATION",
            ],
            "institution_id": [None, None, "ipeds:ci100001", "ipeds:ci100002"],
            "created_by": ["SANITIZED_CI_FIXTURE"] * 4,
            "review_status": ["MANUAL_OVERRIDE"] * 4,
        }
    ).write_parquet(resolved / "legal_entities.parquet")
    pl.DataFrame(
        {
            "parent_organization_id": ["parent_orbit"],
            "canonical_name": ["Orbit Group"],
            "organization_type": ["TECHNOLOGY"],
            "headquarters_state": ["TX"],
            "is_staffing_or_consulting": [False],
            "created_by": ["SANITIZED_CI_FIXTURE"],
            "review_status": ["MANUAL_OVERRIDE"],
            "notes": ["Synthetic parent used only for CI."],
        }
    ).write_parquet(resolved / "parent_organizations.parquet")
    pl.DataFrame(
        {
            "alias_raw": ["Orbit Labs", "Aurora University"],
            "source_id": ["dol_lca", "ipeds"],
            "city": ["Austin", "Madison"],
            "state": ["TX", "WI"],
            "match_method": ["REVIEWED_ALIAS", "AUTHORITATIVE_SOURCE_ID"],
            "match_score": [1.0, 1.0],
            "review_status": ["REVIEWED", "REVIEWED"],
            "match_status": ["MANUAL", "MATCHED"],
            "occurrence_count": [3, 1],
            "legal_entity_id": ["legal_orbit_labs", "legal_aurora"],
            "parent_organization_id": ["parent_orbit", None],
            "candidate_legal_entity_id": [None, None],
        }
    ).write_parquet(resolved / "entity_aliases.parquet")


def _write_source_metadata(data_root: Path, output_root: Path) -> None:
    """Write deterministic active-source metadata for the sanitized fixture artifacts."""

    registry = SourceRegistry.from_yaml()
    store = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    specs = [
        (
            "dol_lca",
            "classified",
            "dol_lca-ci-artifact",
            2025,
            None,
            False,
            False,
            1,
            "LCA_Disclosure_Data_FY2025.xlsx",
        ),
        (
            "dol_lca",
            "classified",
            "dol_lca-partial-ci-artifact",
            2026,
            2,
            True,
            False,
            1,
            "LCA_Disclosure_Data_FY2026_Q2.xlsx",
        ),
        (
            "dol_perm",
            "classified",
            "dol_perm-ci-artifact",
            2025,
            4,
            False,
            False,
            None,
            "PERM_Disclosure_Data_FY2025_Q4.xlsx",
        ),
        (
            "uscis_h1b",
            "resolved",
            "uscis-ci-artifact",
            2025,
            None,
            False,
            False,
            None,
            "H1B_Employer_Data_Hub_FY2025.csv",
        ),
        (
            "ipeds",
            "resolved",
            "ipeds-ci-artifact",
            2025,
            None,
            False,
            False,
            None,
            "HD2025.zip",
        ),
    ]
    candidates_by_source: dict[str, list[SourceArtifactCandidate]] = {}
    retrieved_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    for (
        source_id,
        layer,
        artifact_id,
        fiscal_year,
        fiscal_quarter,
        is_partial_period,
        is_quarter_partition,
        coverage_start_quarter,
        file_name,
    ) in specs:
        source = registry.get(source_id)
        path = (
            data_root
            / layer
            / "sources"
            / source_id
            / f"fy={fiscal_year}"
            / f"{artifact_id}.parquet"
        )
        frame = pl.read_parquet(path)
        download_url = f"{source.landing_page}#sanitized-ci-{artifact_id}"
        candidate = SourceArtifactCandidate(
            source_id=source_id,
            authority=source.authority,
            landing_page_url=source.landing_page,
            download_url=download_url,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            is_partial_period=is_partial_period,
            is_quarter_partition=is_quarter_partition,
            coverage_start_quarter=coverage_start_quarter,
            file_name=file_name,
            expected_format=Path(file_name).suffix.lstrip("."),
        )
        candidates_by_source.setdefault(source_id, []).append(candidate)
        store.upsert(
            ArtifactManifestRecord(
                source_artifact_id=artifact_id,
                source_id=source_id,
                authority=source.authority,
                landing_page_url=source.landing_page,
                download_url=download_url,
                retrieved_at=retrieved_at,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                is_partial_period=is_partial_period,
                is_quarter_partition=is_quarter_partition,
                coverage_start_quarter=coverage_start_quarter,
                file_name=file_name,
                mime_type="application/octet-stream",
                byte_size=path.stat().st_size,
                sha256=hashlib.sha256(artifact_id.encode("utf-8")).hexdigest(),
                record_layout_url=None,
                parser_version=source.parser_version,
                schema_version=source.schema_version,
                raw_row_count=frame.height,
                row_count=frame.height,
                column_count=frame.width,
                validation_status=ValidationStatus.PASSED,
                build_id=FIXTURE_BUILD_ID,
                raw_path=path,
                parquet_path=path,
                schema_diff_path=path.with_suffix(".schema-diff.json"),
            )
        )
    for source_id, candidates in candidates_by_source.items():
        write_json_atomic(
            output_root / "manifests" / "discovery" / f"{source_id}-latest.json",
            DiscoveryReport(
                source_id=source_id,
                discovered_at=retrieved_at,
                from_fiscal_year=2022,
                landing_page_url=candidates[0].landing_page_url,
                candidates=tuple(candidates),
                selected_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
            ),
        )


def _write_government_evidence(data_root: Path) -> None:
    lca_records = [
        {
            "fiscal_year": 2024,
            "employer_name_raw": "Aurora Research University",
            "legal_entity_id": "legal_aurora",
            "parent_organization_id": None,
            "state": "WI",
        },
        {
            "fiscal_year": 2025,
            "employer_name_raw": "Aurora Research University",
            "legal_entity_id": "legal_aurora",
            "parent_organization_id": None,
            "state": "WI",
        },
        {
            "fiscal_year": 2025,
            "employer_name_raw": "Beacon Technical Institute",
            "legal_entity_id": "legal_beacon",
            "parent_organization_id": None,
            "state": "CO",
        },
        {
            "fiscal_year": 2024,
            "employer_name_raw": "Orbit Research Labs LLC",
            "legal_entity_id": "legal_orbit_labs",
            "parent_organization_id": "parent_orbit",
            "state": "TX",
        },
        {
            "fiscal_year": 2025,
            "employer_name_raw": "Orbit Systems Inc",
            "legal_entity_id": "legal_orbit",
            "parent_organization_id": "parent_orbit",
            "state": "TX",
        },
    ]
    _write_source(
        data_root,
        "classified",
        "dol_lca",
        2025,
        _case_frame("dol_lca", lca_records, perm=False),
    )
    partial_lca = _case_frame(
        "dol_lca-partial",
        [
            {
                "fiscal_year": 2026,
                "fiscal_quarter": 2,
                "is_partial_period": True,
                "employer_name_raw": "Orbit Research Labs LLC",
                "legal_entity_id": "legal_orbit_labs",
                "parent_organization_id": "parent_orbit",
                "state": "TX",
            }
        ],
        perm=False,
    )
    _write_source(data_root, "classified", "dol_lca", 2026, partial_lca)

    perm_records = [
        {
            "fiscal_year": 2024,
            "employer_name_raw": "Aurora Research University",
            "legal_entity_id": "legal_aurora",
            "parent_organization_id": None,
            "state": "WI",
        },
        {
            "fiscal_year": 2025,
            "employer_name_raw": "Aurora Research University",
            "legal_entity_id": "legal_aurora",
            "parent_organization_id": None,
            "state": "WI",
        },
        {
            "fiscal_year": 2025,
            "employer_name_raw": "Orbit Systems Inc",
            "legal_entity_id": "legal_orbit",
            "parent_organization_id": "parent_orbit",
            "state": "TX",
        },
    ]
    _write_source(
        data_root,
        "classified",
        "dol_perm",
        2025,
        _case_frame("dol_perm", perm_records, perm=True),
    )

    uscis = pl.DataFrame(
        {
            "source_row_number": [1, 2, 3, 4, 5],
            "source_artifact_id": ["uscis-ci-artifact"] * 5,
            "source_file_name": ["uscis-sanitized-fixture.csv"] * 5,
            "ingested_at": ["2026-08-15T12:00:00+00:00"] * 5,
            "source_url": ["https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"]
            * 5,
            "source_sha256": ["c" * 64] * 5,
            "fiscal_year": [2024, 2025, 2024, 2025, 2026],
            "is_partial_period": [False, False, False, False, True],
            "employer_name_raw": [
                "Aurora Research University",
                "Aurora Research University",
                "Orbit Systems Inc",
                "Orbit Systems Inc",
                "Orbit Systems Inc",
            ],
            "legal_entity_id": [
                "legal_aurora",
                "legal_aurora",
                "legal_orbit",
                "legal_orbit",
                "legal_orbit",
            ],
            "parent_organization_id": [None, None, "parent_orbit", "parent_orbit", "parent_orbit"],
            "initial_approvals": [12, 15, 35, 42, 8],
            "initial_denials": [1, 0, 2, 1, 0],
            "continuing_approvals": [4, 6, 20, 25, 5],
            "continuing_denials": [0, 0, 1, 0, 0],
            "state": ["WI", "WI", "TX", "TX", "TX"],
            "city": ["Madison", "Madison", "Austin", "Austin", "Austin"],
            "zip_code": ["53706", "53706", "78701", "78701", "78701"],
        }
    )
    _write_source(data_root, "resolved", "uscis_h1b", 2025, uscis)


def _write_institution_evidence(data_root: Path) -> None:
    ipeds = pl.DataFrame(
        {
            "institution_id": ["ipeds:ci100001", "ipeds:ci100002"],
            "ipeds_unitid": ["ci100001", "ci100002"],
            "official_name": ["Aurora Research University", "Beacon Technical Institute"],
            "system_name": [None, None],
            "control": ["PUBLIC", "PRIVATE_NONPROFIT"],
            "sector": ["PUBLIC_FOUR_YEAR", "PRIVATE_NONPROFIT_FOUR_YEAR"],
            "city": ["Madison", "Denver"],
            "stabbr": ["WI", "CO"],
            "official_domain": ["aurora.example.edu", "beacon.example.edu"],
            "highest_degree": [
                "DOCTOR_RESEARCH_SCHOLARSHIP",
                "DOCTOR_RESEARCH_SCHOLARSHIP",
            ],
            "active_status": ["ACTIVE", "ACTIVE"],
            "legal_entity_id": ["legal_aurora", "legal_beacon"],
            "parent_organization_id": [None, None],
            "match_confidence": [1.0, 1.0],
            "review_status": ["AUTHORITATIVE_SOURCE_ID", "AUTHORITATIVE_SOURCE_ID"],
            "source_artifact_id": ["ipeds-ci-artifact", "ipeds-ci-artifact"],
            "directory_year": [2025, 2025],
        }
    )
    _write_source(data_root, "resolved", "ipeds", 2025, ipeds)

    processed = data_root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "institution_id": ["ipeds:ci100001", "ipeds:ci100002"],
            "survey_year": [2024, 2024],
            "total_rd": [120_000_000, 900_000_000],
            "federal_rd": [70_000_000, 610_000_000],
            "computing_rd": [35_000_000, 90_000_000],
            "engineering_rd": [45_000_000, 220_000_000],
            "rd_personnel": [550, 2_400],
            "survey_form": ["standard", "standard"],
        }
    ).write_parquet(processed / "herd_observations.parquet")

    source_url = "https://aurora.example.edu/immigration-policy"
    pl.DataFrame(
        {
            "policy_document_id": ["policy-ci-aurora"],
            "institution_id": ["ipeds:ci100001"],
            "document_type": ["employment_immigration_policy"],
            "title": ["Employment immigration policy (sanitized fixture)"],
            "url": [source_url],
            "official_domain": ["aurora.example.edu"],
            "retrieved_at": ["2026-08-15T12:00:00+00:00"],
            "http_status": [200],
            "content_type": ["text/html"],
            "content_sha256": ["a" * 64],
            "text_sha256": ["b" * 64],
            "published_or_updated_date": ["2026-07-01"],
            "raw_path": ["fixtures/policy/aurora.html"],
            "parsed_text_path": ["fixtures/policy/aurora.txt"],
            "is_current": [True],
            "parse_status": ["PARSED"],
            "discovery_method": ["SANITIZED_CI_FIXTURE"],
            "suspicious_text": [False],
            "cache_hit": [False],
        }
    ).write_parquet(processed / "policy_documents.parquet")

    fact_specs = [
        (
            "h1b_research_staff_eligible",
            "YES",
            "REVIEWED_ACCEPTED",
            "Research staff may be sponsored for H-1B status.",
        ),
        (
            "pr_research_staff_eligible",
            "LIMITED",
            "REVIEWED_ACCEPTED",
            "Permanent-residence support for research staff is subject to department approval.",
        ),
        (
            "perm_supported",
            "YES",
            "REVIEWED_ACCEPTED",
            "The university may support PERM cases for eligible positions.",
        ),
        (
            "eb1b_supported",
            "NOT_STATED",
            "REVIEWED_NOT_STATED",
            "The reviewed policy contains no statement about EB-1B support.",
        ),
    ]
    pl.DataFrame(
        [
            {
                "policy_fact_id": f"policy-ci-fact-{index}",
                "institution_id": "ipeds:ci100001",
                "policy_document_id": "policy-ci-aurora",
                "fact_type": fact_type,
                "fact_value": fact_value,
                "qualifier": (
                    "Subject to department approval" if fact_value == "LIMITED" else None
                ),
                "supporting_excerpt": excerpt,
                "section_or_page": "Eligibility",
                "source_url": source_url,
                "retrieved_at": "2026-08-15T12:00:00+00:00",
                "extractor_version": "sanitized-ci-fixture-v1",
                "model_name": "fixture-no-api-call",
                "model_response_id": f"fixture-response-{index}",
                "confidence": 1.0,
                "exact_excerpt_verified": True,
                "human_review_status": review_status,
                "reviewer_note": "Sanitized deterministic CI evidence.",
                "reviewer_id": "ci-fixture-reviewer",
                "reviewed_at": "2026-08-15T13:00:00+00:00",
                "contradiction_group_id": None,
                "valid_from": "2026-08-15T12:00:00+00:00",
                "valid_to": None,
                "is_current": True,
            }
            for index, (fact_type, fact_value, review_status, excerpt) in enumerate(
                fact_specs, start=1
            )
        ]
    ).write_parquet(processed / "policy_facts.parquet")


def _write_quality_checks(data_root: Path) -> None:
    pl.DataFrame(
        {
            "check_id": ["ci_fixture_contract", "ci_partial_period_disclosure"],
            "category": ["fixture", "freshness"],
            "status": ["PASS", "WARN"],
            "critical": [True, False],
            "value": [1.0, 2026.0],
            "threshold": ["sanitized fixture built end to end", "partial FY remains visible"],
            "details": [
                "All fixture presentation tables were generated from sanitized source rows.",
                "FY2026 Q2 is intentionally partial for warning-path coverage.",
            ],
            "build_id": [FIXTURE_BUILD_ID, FIXTURE_BUILD_ID],
            "checked_at": ["2026-08-15T13:00:00+00:00"] * 2,
        }
    ).write_parquet(data_root / "processed" / "quality_checks.parquet")


def build_phase10_ci_fixture(output_root: Path) -> Path:
    """Build and return a nonempty DuckDB fixture without network or private data."""

    selected_root = output_root.resolve()
    data_root = selected_root / "data"
    report_root = selected_root / "outputs"
    database_path = selected_root / "db" / "phase10-ci.duckdb"

    _write_dimensions(data_root)
    _write_government_evidence(data_root)
    _write_institution_evidence(data_root)
    _write_source_metadata(data_root, report_root)
    metrics = MetricsPipeline(
        data_root=data_root,
        output_root=report_root,
    ).build()
    _write_quality_checks(data_root)
    database = DuckDBBuilder(data_root=data_root, database_path=database_path).build()

    if metrics.employer_count < 3 or metrics.institution_count < 2:
        raise RuntimeError("Sanitized CI fixture did not produce the required nonzero coverage")
    if database.employer_count != metrics.employer_count:
        raise RuntimeError("DuckDB employer count does not match the generated metrics")
    if database.institution_count != metrics.institution_count:
        raise RuntimeError("DuckDB institution count does not match the generated metrics")
    return database_path


def main() -> None:
    """Build the fixture at a caller-selected, disposable location."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(".ci/phase10-fixture"),
        help="Disposable directory for generated fixture data and DuckDB.",
    )
    args = parser.parse_args()
    database_path = build_phase10_ci_fixture(args.output_root)
    print(
        json.dumps(
            {
                "build_id": FIXTURE_BUILD_ID,
                "database_path": str(database_path),
                "sanitized": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
