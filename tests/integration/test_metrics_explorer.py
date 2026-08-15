from pathlib import Path
from time import perf_counter

import duckdb
import polars as pl
import pytest

from sponsor_intel.database.builder import REQUIRED_VIEWS, DuckDBBuilder
from sponsor_intel.evidence.everify import EVERIFY_OBSERVATION_SCHEMA
from sponsor_intel.metrics.pipeline import MetricsPipeline
from sponsor_intel.quality import QualityReporter
from sponsor_intel.services import DuckDBExplorerService, EmployerFilters, InstitutionFilters


def _write_source(
    data_root: Path,
    layer: str,
    source_id: str,
    fiscal_year: int,
    frame: pl.DataFrame,
) -> None:
    path = data_root / layer / "sources" / source_id / f"fy={fiscal_year}" / "fixture.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _dol_rows(source_id: str, *, perm: bool = False) -> pl.DataFrame:
    common: dict[str, list[object]] = {
        "case_id": [f"{source_id}-1", f"{source_id}-2"],
        "source_artifact_id": [f"{source_id}-artifact"] * 2,
        "source_file_name": [f"{source_id}-fixture.csv"] * 2,
        "ingested_at": ["2026-08-14T00:00:00+00:00"] * 2,
        "fiscal_year": [2025, 2025],
        "fiscal_quarter": [4, 4],
        "is_partial_period": [False, False],
        "case_status": ["Certified", "Denied"],
        "decision_date": [None, None],
        "employer_name_raw": ["Acme Labs LLC", "State University"],
        "legal_entity_id": ["legal_acme_labs", "legal_university"],
        "parent_organization_id": ["parent_acme", None],
        "job_title_raw": ["Software Engineer", "Research Scientist"],
        "soc_code": ["15-1252.00", "15-1221.00"],
        "soc_title": ["Software Developers", "Computer Research Scientists"],
        "role_family": ["software_engineering", "computer_science_research"],
        "technical_role": [True, True],
        "role_confidence": [0.96, 0.95],
        "classification_method": ["SOC_MAPPING", "SOC_MAPPING"],
        "classification_version": ["role_taxonomy_v1"] * 2,
        "review_status": ["NOT_REQUIRED"] * 2,
        "worksite_state": ["CA", "IL"],
    }
    if perm:
        common.update(
            {
                "wage_offer_from": [150_000, 80_000],
                "wage_offer_to": [None, None],
                "wage_offer_unit_of_pay": ["Year", "Year"],
            }
        )
    else:
        common.update(
            {
                "wage_from": [140_000, 90_000],
                "wage_to": [None, None],
                "wage_unit": ["Year", "Year"],
            }
        )
    return pl.DataFrame(common)


def _build_fixture(data_root: Path) -> None:
    resolved = data_root / "resolved"
    resolved.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "legal_entity_id": ["legal_acme", "legal_acme_labs", "legal_university"],
            "legal_name": ["Acme Inc", "Acme Labs LLC", "State University"],
            "normalized_legal_name": ["ACME INC", "ACME LABS LLC", "STATE UNIVERSITY"],
            "parent_organization_id": ["parent_acme", "parent_acme", None],
            "city": ["San Jose", "San Jose", "Urbana"],
            "state": ["CA", "CA", "IL"],
            "postal_code": ["95113", "95113", "61801"],
            "country": ["US"] * 3,
            "organization_type": ["TECHNOLOGY", "TECHNOLOGY", "HIGHER_EDUCATION"],
            "institution_id": [None, None, "ipeds:100001"],
            "created_by": ["FIXTURE"] * 3,
            "review_status": ["REVIEWED"] * 3,
        }
    ).write_parquet(resolved / "legal_entities.parquet")
    pl.DataFrame(
        {
            "parent_organization_id": ["parent_acme"],
            "canonical_name": ["Acme"],
            "organization_type": ["TECHNOLOGY"],
            "headquarters_state": ["CA"],
            "is_staffing_or_consulting": [False],
            "created_by": ["FIXTURE"],
            "review_status": ["REVIEWED"],
            "notes": [None],
        }
    ).write_parquet(resolved / "parent_organizations.parquet")
    pl.DataFrame(
        {
            "alias_raw": ["Acme Labs"],
            "source_id": ["dol_lca"],
            "city": ["San Jose"],
            "state": ["CA"],
            "match_method": ["REVIEWED_ALIAS"],
            "match_score": [1.0],
            "review_status": ["REVIEWED"],
            "match_status": ["MANUAL"],
            "occurrence_count": [2],
            "legal_entity_id": ["legal_acme_labs"],
            "parent_organization_id": ["parent_acme"],
        }
    ).write_parquet(resolved / "entity_aliases.parquet")

    _write_source(data_root, "classified", "dol_lca", 2025, _dol_rows("dol_lca"))
    partial_lca = (
        _dol_rows("dol_lca")
        .head(1)
        .with_columns(
            pl.lit("dol_lca-partial").alias("case_id"),
            pl.lit(2026).alias("fiscal_year"),
            pl.lit(2).alias("fiscal_quarter"),
            pl.lit(True).alias("is_partial_period"),
        )
    )
    _write_source(data_root, "classified", "dol_lca", 2026, partial_lca)
    _write_source(data_root, "classified", "dol_perm", 2025, _dol_rows("dol_perm", perm=True))

    uscis = pl.DataFrame(
        {
            "source_row_number": [1, 2],
            "source_artifact_id": ["uscis-2025", "uscis-2026"],
            "source_file_name": ["uscis-2025.csv", "uscis-2026.csv"],
            "ingested_at": ["2026-08-14T00:00:00+00:00"] * 2,
            "fiscal_year": [2025, 2026],
            "is_partial_period": [False, True],
            "employer_name_raw": ["Acme Inc", "Acme Inc"],
            "legal_entity_id": ["legal_acme", "legal_acme"],
            "parent_organization_id": ["parent_acme", "parent_acme"],
            "initial_approvals": [10, 2],
            "initial_denials": [1, 0],
            "continuing_approvals": [5, 1],
            "continuing_denials": [0, 0],
            "state": ["CA", "CA"],
            "city": ["San Jose", "San Jose"],
            "zip_code": ["95113", "95113"],
        }
    )
    _write_source(data_root, "resolved", "uscis_h1b", 2025, uscis)

    ipeds = pl.DataFrame(
        {
            "institution_id": ["ipeds:100001"],
            "ipeds_unitid": ["100001"],
            "official_name": ["State University"],
            "system_name": [None],
            "control": ["PUBLIC"],
            "sector": ["PUBLIC_FOUR_YEAR"],
            "city": ["Urbana"],
            "stabbr": ["IL"],
            "official_domain": ["state.example.edu"],
            "highest_degree": ["DOCTOR_RESEARCH_SCHOLARSHIP"],
            "active_status": ["ACTIVE"],
            "legal_entity_id": ["legal_university"],
            "parent_organization_id": [None],
            "match_confidence": [1.0],
            "review_status": ["AUTHORITATIVE_SOURCE_ID"],
            "source_artifact_id": ["ipeds-2025"],
            "directory_year": [2025],
        }
    )
    _write_source(data_root, "resolved", "ipeds", 2025, ipeds)
    processed = data_root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "institution_id": ["ipeds:100001"],
            "survey_year": [2024],
            "total_rd": [500_000_000],
            "federal_rd": [300_000_000],
            "computing_rd": [25_000_000],
            "engineering_rd": [100_000_000],
            "rd_personnel": [1_000],
            "survey_form": ["standard"],
        }
    ).write_parquet(processed / "herd_observations.parquet")


def test_metrics_database_services_and_exports(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    database_path = tmp_path / "db" / "fixture.duckdb"
    _build_fixture(data_root)

    metrics = MetricsPipeline(data_root=data_root, output_root=output_root).build()
    assert (data_root / "processed" / "employer_scores.parquet").is_file()
    quality = QualityReporter(data_root=data_root, output_root=output_root).build()
    assert not quality.passed
    database = DuckDBBuilder(data_root=data_root, database_path=database_path).build()

    assert metrics.employer_count == 2
    assert metrics.institution_count == 1
    assert metrics.lca_case_count == 3
    assert metrics.latest_complete_fiscal_year == 2025
    assert metrics.current_partial_fiscal_year == 2026
    assert metrics.current_partial_quarter == 2
    assert set(database.view_names) == set(REQUIRED_VIEWS)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        views = {
            row[0]
            for row in connection.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        }
    assert set(REQUIRED_VIEWS).issubset(views)

    service = DuckDBExplorerService(database_path)
    started = perf_counter()
    employers = service.list_employers(EmployerFilters(search="Acme Labs"))
    employer_elapsed = perf_counter() - started
    assert employers.height == 1
    assert employers["legal_entity_count"].item() == 2
    assert employers["relevant_lca_count"].item() == 2
    assert employers["relevant_certified_perm_count"].item() == 1
    assert employers["everify_status"].item() == "UNKNOWN"
    assert employer_elapsed < 2

    institutions = service.list_institutions(InstitutionFilters(minimum_total_rd=100_000_000))
    assert institutions.height == 1
    assert institutions["cap_exemption_status"].item() == ("POTENTIALLY_CAP_EXEMPT_HIGHER_ED")
    assert institutions["research_staff_h1b_policy"].item() == "UNKNOWN"

    started = perf_counter()
    detail = service.get_organization_detail("parent_acme")
    detail_elapsed = perf_counter() - started
    assert detail is not None
    assert detail.summary.height == 1
    assert detail.legal_entities.height == 2
    assert detail.h1b_trends.height == 2
    assert detail.institutions.is_empty()
    assert detail_elapsed < 1

    institution_detail = service.get_organization_detail("legal_university")
    assert institution_detail is not None
    assert institution_detail.institutions.height == 1

    comparison = service.compare_organizations(("parent_acme", "legal_university"))
    assert comparison["organization_id"].to_list() == ["parent_acme", "legal_university"]
    assert (
        comparison.filter(pl.col("organization_id") == "parent_acme")["h1b_history_score"].item()
        is not None
    )
    institution_comparison = comparison.filter(pl.col("organization_id") == "legal_university")
    assert institution_comparison["research_institution"].item() == "State University"
    assert institution_comparison["research_strength_coverage"].item() == 1
    with pytest.raises(ValueError, match="at most five"):
        service.compare_organizations(tuple(f"organization-{index}" for index in range(6)))

    health = service.get_data_health()
    assert health.source_coverage.height >= 5
    assert health.quality_checks.height > 0
    assert "FAIL" in health.quality_checks["status"].to_list()

    csv_export = service.export_employers(EmployerFilters(search="Acme"), "csv")
    parquet_export = service.export_employers(EmployerFilters(search="Acme"), "parquet")
    assert csv_export.startswith(b"organization_id,")
    assert pl.read_parquet(parquet_export).height == 1
    service.close()


def test_phase6_evidence_enriches_signals_without_treating_no_match_as_no(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    database_path = tmp_path / "db" / "phase6.duckdb"
    _build_fixture(data_root)
    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    processed = data_root / "processed"
    everify_rows = [
        {
            "lookup_id": "everify_acme",
            "priority_rank": 1,
            "queried_name": "Acme Labs LLC",
            "legal_entity_id": "legal_acme_labs",
            "parent_organization_id": "parent_acme",
            "organization_id": "parent_acme",
            "state": "CA",
            "enrollment_status": "CONFIRMED_ACTIVE",
            "enrollment_date": "01/02/2020",
            "termination_date": None,
            "workforce_size": "100 to 499",
            "hiring_site_count": 3,
            "hiring_site_locations": "CA,TX",
            "matched_name": "Acme Labs LLC",
            "matched_dba": None,
            "retrieved_at": "2026-08-14T00:00:00+00:00",
            "match_confidence": 1.0,
            "match_method": "EXACT_EMPLOYER_NAME",
            "review_status": "NOT_REQUIRED",
            "review_reason": None,
            "source_url": "https://www.e-verify.gov/e-verify-employer-search",
            "source_evidence_json": "[]",
            "cache_hit": False,
            "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
        },
        {
            "lookup_id": "everify_university",
            "priority_rank": 2,
            "queried_name": "State University",
            "legal_entity_id": "legal_university",
            "parent_organization_id": None,
            "organization_id": "legal_university",
            "state": "IL",
            "enrollment_status": "NO_MATCH",
            "enrollment_date": None,
            "termination_date": None,
            "workforce_size": None,
            "hiring_site_count": None,
            "hiring_site_locations": None,
            "matched_name": None,
            "matched_dba": None,
            "retrieved_at": "2026-08-14T00:00:00+00:00",
            "match_confidence": 0.0,
            "match_method": "NO_RESULTS",
            "review_status": "NOT_REQUIRED",
            "review_reason": "No result is not evidence of non-enrollment",
            "source_url": "https://www.e-verify.gov/e-verify-employer-search",
            "source_evidence_json": "[]",
            "cache_hit": False,
            "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
        },
    ]
    pl.DataFrame(everify_rows, schema=EVERIFY_OBSERVATION_SCHEMA).write_parquet(
        processed / "everify_observations.parquet"
    )
    pl.DataFrame(
        {
            "observation_id": ["opt_acme_total"],
            "source_artifact_id": ["opt_artifact"],
            "source_id": ["sevp_opt"],
            "report_year": [2024],
            "rank": [1],
            "employer_name_raw": ["Acme"],
            "program_type": ["OPT_OR_STEM_OPT"],
            "reported_count": [1234],
            "is_positive": [True],
            "source_url": ["https://www.ice.gov/report.pdf"],
            "landing_page_url": ["https://www.ice.gov/sevis/whats-new"],
            "retrieved_at": ["2026-08-14T00:00:00+00:00"],
            "source_sha256": ["a" * 64],
            "coverage_note": ["Positive-only Top 200 coverage"],
            "evidence_class": ["OBSERVED_GOVERNMENT_RECORD"],
            "employer_name_normalized": ["ACME"],
            "legal_entity_id": [None],
            "parent_organization_id": ["parent_acme"],
            "organization_id": ["parent_acme"],
            "match_method": ["EXACT_PARENT_NAME"],
            "match_confidence": [1.0],
            "review_status": ["NOT_REQUIRED"],
            "review_reason": [None],
        }
    ).write_parquet(processed / "opt_employer_observations.parquet")

    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    DuckDBBuilder(data_root=data_root, database_path=database_path).build()
    service = DuckDBExplorerService(database_path)

    acme = service.list_employers(EmployerFilters(search="Acme"))
    university = service.list_employers(EmployerFilters(search="State University"))
    assert acme["everify_status"].item() == "CONFIRMED_ACTIVE"
    assert acme["known_opt_observation"].item() == "OBSERVED_POSITIVE"
    assert university["everify_status"].item() == "UNKNOWN"
    assert university["everify_lookup_status"].item() == "NO_MATCH"
    detail = service.get_organization_detail("parent_acme")
    assert detail is not None
    assert detail.everify_evidence.height == 1
    assert detail.opt_evidence.height == 1
    service.close()


def test_phase7_reviewed_policy_enriches_metrics_database_and_detail(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    database_path = tmp_path / "db" / "phase7.duckdb"
    _build_fixture(data_root)
    processed = data_root / "processed"
    source_url = "https://state.example.edu/h1b-policy"
    pl.DataFrame(
        {
            "policy_document_id": ["doc-state"],
            "institution_id": ["ipeds:100001"],
            "document_type": ["h1b_sponsorship_policy"],
            "title": ["H-1B sponsorship policy"],
            "url": [source_url],
            "official_domain": ["state.example.edu"],
            "retrieved_at": ["2026-08-14T00:00:00+00:00"],
            "http_status": [200],
            "content_type": ["text/html"],
            "content_sha256": ["a" * 64],
            "text_sha256": ["b" * 64],
            "published_or_updated_date": ["2026-08-01"],
            "raw_path": ["data/raw/policy/a.html"],
            "parsed_text_path": ["data/staging/policy/b.txt"],
            "is_current": [True],
            "parse_status": ["PARSED"],
            "discovery_method": ["REVIEWED_SEED"],
            "suspicious_text": [False],
            "cache_hit": [False],
        }
    ).write_parquet(processed / "policy_documents.parquet")
    pl.DataFrame(
        {
            "policy_fact_id": ["fact-accepted", "fact-pending"],
            "institution_id": ["ipeds:100001", "ipeds:100001"],
            "policy_document_id": ["doc-state", "doc-state"],
            "fact_type": [
                "h1b_research_staff_eligible",
                "pr_research_staff_eligible",
            ],
            "fact_value": ["YES", "YES"],
            "qualifier": [None, None],
            "supporting_excerpt": [
                "The university sponsors research staff for H-1B status.",
                "Permanent residence cases require separate review.",
            ],
            "section_or_page": ["Eligibility", "Permanent residence"],
            "source_url": [source_url, source_url],
            "retrieved_at": ["2026-08-14T00:00:00+00:00"] * 2,
            "extractor_version": ["policy_extractor_v1"] * 2,
            "model_name": ["test-model"] * 2,
            "model_response_id": ["resp-1", "resp-1"],
            "confidence": [0.98, 0.92],
            "exact_excerpt_verified": [True, True],
            "human_review_status": ["REVIEWED_ACCEPTED", "NEEDS_REVIEW"],
            "reviewer_note": ["Official source and exact excerpt checked.", None],
            "contradiction_group_id": [None, None],
            "valid_from": ["2026-08-14T00:00:00+00:00"] * 2,
            "valid_to": [None, None],
            "is_current": [True, True],
        }
    ).write_parquet(processed / "policy_facts.parquet")

    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    DuckDBBuilder(data_root=data_root, database_path=database_path).build()
    service = DuckDBExplorerService(database_path)

    institutions = service.list_institutions(InstitutionFilters(search="State University"))
    assert service.get_overview().reviewed_policy_institution_count == 1
    assert institutions["research_staff_h1b_policy"].item() == "YES"
    assert institutions["research_staff_permanent_residence_policy"].item() == "UNKNOWN"
    assert institutions["policy_review_status"].item() == "REVIEWED"
    assert "REVIEWED_OFFICIAL_POLICY" in institutions["evidence_classes"].item()
    detail = service.get_organization_detail("legal_university")
    assert detail is not None
    assert detail.policy_evidence["policy_fact_id"].to_list() == ["fact-accepted"]
    review = service.get_evidence_review()
    assert review.policy["policy_fact_id"].to_list() == ["fact-pending"]
    service.close()
