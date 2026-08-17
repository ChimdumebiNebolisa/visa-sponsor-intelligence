from datetime import UTC, datetime
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
    layer: str,
    source_id: str,
    fiscal_year: int,
    frame: pl.DataFrame,
) -> Path:
    artifact_ids = (
        frame["source_artifact_id"].unique().to_list()
        if "source_artifact_id" in frame.columns
        else [f"{source_id}-{fiscal_year}"]
    )
    if len(artifact_ids) != 1:
        raise ValueError("Fixture source files must contain exactly one source artifact")
    path = (
        data_root
        / layer
        / "sources"
        / source_id
        / f"fy={fiscal_year}"
        / f"{artifact_ids[0]}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    return path


def _write_active_manifests(
    output_root: Path,
    specs: list[tuple[str, int, int | None, bool, str, Path]],
) -> None:
    registry = SourceRegistry.from_yaml()
    manifest = ArtifactManifestStore(output_root / "manifests" / "source_artifacts.jsonl")
    candidates_by_source: dict[str, list[SourceArtifactCandidate]] = {}
    for source_id, fiscal_year, fiscal_quarter, is_partial, file_name, path in specs:
        artifact_id = path.stem
        source = registry.get(source_id)
        candidate = SourceArtifactCandidate(
            source_id=source_id,
            authority=source.authority,
            landing_page_url=source.landing_page,
            download_url=f"https://example.gov/{file_name}",
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            is_partial_period=is_partial,
            is_quarter_partition=False,
            coverage_start_quarter=1 if source_id == "dol_lca" else None,
            file_name=file_name,
            expected_format=Path(file_name).suffix.lstrip(".") or "xlsx",
        )
        candidates_by_source.setdefault(source_id, []).append(candidate)
        frame = pl.read_parquet(path)
        manifest.upsert(
            ArtifactManifestRecord(
                source_artifact_id=artifact_id,
                source_id=source_id,
                authority=source.authority,
                landing_page_url=source.landing_page,
                download_url=candidate.download_url,
                retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                is_partial_period=is_partial,
                is_quarter_partition=False,
                coverage_start_quarter=1 if source_id == "dol_lca" else None,
                file_name=file_name,
                mime_type="application/octet-stream",
                byte_size=path.stat().st_size,
                sha256="a" * 64,
                record_layout_url=None,
                parser_version=source.parser_version,
                schema_version=source.schema_version,
                raw_row_count=frame.height + 5,
                row_count=frame.height,
                column_count=frame.width,
                validation_status=ValidationStatus.PASSED,
                build_id="fixture",
                raw_path=path,
                parquet_path=path,
                schema_diff_path=path.with_suffix(".json"),
            )
        )
    for source_id, candidates in candidates_by_source.items():
        write_json_atomic(
            output_root / "manifests" / "discovery" / f"{source_id}-latest.json",
            DiscoveryReport(
                source_id=source_id,
                discovered_at=datetime(2026, 8, 14, tzinfo=UTC),
                from_fiscal_year=2022,
                landing_page_url=candidates[0].landing_page_url,
                candidates=tuple(candidates),
                selected_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
            ),
        )


def _dol_rows(source_id: str, *, perm: bool = False) -> pl.DataFrame:
    common: dict[str, list[object]] = {
        "source_row_number": [1, 2],
        "case_id": [f"{source_id}-1", f"{source_id}-2"],
        "source_artifact_id": [f"{source_id}-artifact"] * 2,
        "source_file_name": [f"{source_id}-fixture.csv"] * 2,
        "ingested_at": ["2026-08-14T00:00:00+00:00"] * 2,
        "fiscal_year": [2025, 2025],
        "fiscal_quarter": [4 if perm else None, 4 if perm else None],
        "is_partial_period": [False, False],
        "received_date": [None, None],
        "case_status": [
            "Certified",
            "Certified - Expired" if perm else "Certified - Withdrawn",
        ],
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
                "visa_class": ["H-1B", "H-1B"],
                "wage_from": [140_000, 90_000],
                "wage_to": [None, None],
                "wage_unit": ["Year", "Year"],
            }
        )
    return pl.DataFrame(common)


def _build_fixture(data_root: Path) -> None:
    artifact_specs: list[tuple[str, int, int | None, bool, str, Path]] = []
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
            "review_status": ["REVIEW_REQUIRED", "DETERMINISTIC", "DETERMINISTIC"],
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
            "review_status": ["MANUAL_OVERRIDE"],
            "notes": [None],
        }
    ).write_parquet(resolved / "parent_organizations.parquet")
    pl.DataFrame(
        {
            "alias_raw": ["Acme Labs", "State University Springfield"],
            "source_id": ["dol_lca", "dol_lca"],
            "city": ["San Jose", "Springfield"],
            "state": ["CA", "MO"],
            "match_method": ["REVIEWED_ALIAS", "EXACT_NAME_LOCATION_CONFLICT"],
            "match_score": [1.0, 0.0],
            "review_status": ["REVIEWED", "REVIEW_REQUIRED"],
            "match_status": ["MANUAL", "REVIEW_REQUIRED"],
            "occurrence_count": [2, 1],
            "legal_entity_id": ["legal_acme_labs", "legal_university"],
            "parent_organization_id": ["parent_acme", None],
            "candidate_legal_entity_id": [None, None],
        }
    ).write_parquet(resolved / "entity_aliases.parquet")

    lca_complete_path = _write_source(
        data_root, "classified", "dol_lca", 2025, _dol_rows("dol_lca")
    )
    artifact_specs.append(
        ("dol_lca", 2025, None, False, "LCA_Disclosure_Data_FY2025.xlsx", lca_complete_path)
    )
    partial_lca = (
        _dol_rows("dol_lca")
        .head(1)
        .with_columns(
            pl.lit("dol_lca-partial").alias("case_id"),
            pl.lit("dol_lca-partial-artifact").alias("source_artifact_id"),
            pl.lit(2026).alias("fiscal_year"),
            pl.lit(2).alias("fiscal_quarter"),
            pl.lit(True).alias("is_partial_period"),
        )
    )
    lca_partial_path = _write_source(data_root, "classified", "dol_lca", 2026, partial_lca)
    artifact_specs.append(
        ("dol_lca", 2026, 2, True, "LCA_Disclosure_Data_FY2026_Q2.xlsx", lca_partial_path)
    )
    perm_path = _write_source(
        data_root, "classified", "dol_perm", 2025, _dol_rows("dol_perm", perm=True)
    )
    artifact_specs.append(
        ("dol_perm", 2025, 4, False, "PERM_Disclosure_Data_FY2025_Q4.xlsx", perm_path)
    )

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
    for fiscal_year in (2025, 2026):
        uscis_year = uscis.filter(pl.col("fiscal_year") == fiscal_year)
        uscis_path = _write_source(
            data_root,
            "resolved",
            "uscis_h1b",
            fiscal_year,
            uscis_year,
        )
        artifact_specs.append(
            (
                "uscis_h1b",
                fiscal_year,
                None,
                fiscal_year == 2026,
                f"h1b_datahubexport-{fiscal_year}.csv",
                uscis_path,
            )
        )

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
    ipeds_path = _write_source(data_root, "resolved", "ipeds", 2025, ipeds)
    artifact_specs.append(("ipeds", 2025, None, False, "HD2025.zip", ipeds_path))
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
    _write_active_manifests(data_root.parent / "outputs", artifact_specs)


def test_metrics_database_services_and_exports(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    database_path = tmp_path / "db" / "fixture.duckdb"
    _build_fixture(data_root)

    metrics = MetricsPipeline(data_root=data_root, output_root=output_root).build()
    assert (data_root / "processed" / "employer_scores.parquet").is_file()
    assert (data_root / "processed" / "employer_scores_v2.parquet").is_file()
    assert (data_root / "processed" / "employer_scores_v1.parquet").is_file()
    assert (data_root / "processed" / "institution_scores_v1.parquet").is_file()
    employer_scores = pl.read_parquet(data_root / "processed" / "employer_scores.parquet")
    assert employer_scores["score_version"].unique().to_list() == ["product_a_scores_v1"]
    assert (
        employer_scores.filter(pl.col("organization_id") == "legal_acme")[
            "overall_sponsorship_status"
        ].item()
        == "UNRATED"
    )
    assert pl.read_parquet(data_root / "processed" / "employer_scores_v2.parquet")[
        "score_version"
    ].unique().to_list() == ["evidence_scores_v2_2026_08"]
    assert pl.read_parquet(data_root / "processed" / "employer_scores_v1.parquet")[
        "score_version"
    ].unique().to_list() == ["evidence_scores_v1_2026_08"]
    institution_metrics = pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")
    assert institution_metrics["score_version"].unique().to_list() == ["product_a_scores_v1"]
    assert institution_metrics["metric_version"].unique().to_list() == ["product_a_metrics_v1"]
    quality = QualityReporter(data_root=data_root, output_root=output_root).build()
    assert not quality.passed
    database = DuckDBBuilder(data_root=data_root, database_path=database_path).build()

    assert metrics.employer_count == 4
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
        artifact_counts = connection.execute(
            "SELECT raw_row_count, normalized_row_count FROM vw_source_artifacts"
        ).fetchall()
        h1b_variant = connection.execute(
            """
            SELECT relevant_certified_lca_count,
                relevant_certified_withdrawn_lca_count,
                weighted_relevant_lca_count
            FROM vw_h1b_trends
            WHERE organization_id = 'legal_university' AND identity_scope = 'LEGAL_ENTITY'
            """
        ).fetchone()
        perm_variant = connection.execute(
            """
            SELECT relevant_certified_perm_count,
                relevant_certified_expired_perm_count,
                weighted_relevant_perm_count
            FROM vw_perm_trends
            WHERE organization_id = 'legal_university' AND identity_scope = 'LEGAL_ENTITY'
            """
        ).fetchone()
        relevant_title_sources = connection.execute(
            """
            SELECT source_id FROM vw_relevant_titles
            WHERE organization_id = 'legal_university' AND identity_scope = 'LEGAL_ENTITY'
            ORDER BY source_id
            """
        ).fetchall()
    assert set(REQUIRED_VIEWS).issubset(views)
    assert artifact_counts
    assert all(raw == normalized + 5 for raw, normalized in artifact_counts)
    assert h1b_variant == (0, 1, 0.5)
    assert perm_variant == (0, 1, 0.5)
    assert relevant_title_sources == [("dol_lca",), ("dol_perm",)]

    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO lca_cases_resolved
            SELECT * REPLACE ('lca-unsuccessful' AS case_id, 'Denied' AS case_status)
            FROM lca_cases_resolved WHERE legal_entity_id = 'legal_acme_labs' LIMIT 1
            """
        )
        connection.execute(
            """
            INSERT INTO lca_cases_resolved
            SELECT * REPLACE ('lca-nontechnical' AS case_id, false AS technical_role)
            FROM lca_cases_resolved WHERE legal_entity_id = 'legal_acme_labs' LIMIT 1
            """
        )
        connection.execute(
            """
            INSERT INTO lca_cases_resolved
            SELECT * REPLACE ('lca-unscored-visa' AS case_id, 'H-1B1' AS visa_class)
            FROM lca_cases_resolved WHERE legal_entity_id = 'legal_acme_labs' LIMIT 1
            """
        )
        connection.execute(
            """
            INSERT INTO perm_cases_resolved
            SELECT * REPLACE ('perm-unsuccessful' AS case_id, 'Denied' AS case_status)
            FROM perm_cases_resolved WHERE legal_entity_id = 'legal_acme_labs' LIMIT 1
            """
        )
        connection.execute(
            """
            INSERT INTO perm_cases_resolved
            SELECT * REPLACE ('perm-nontechnical' AS case_id, false AS technical_role)
            FROM perm_cases_resolved WHERE legal_entity_id = 'legal_acme_labs' LIMIT 1
            """
        )

    service = DuckDBExplorerService(database_path)
    started = perf_counter()
    employers = service.list_employers(EmployerFilters(search="Acme Labs"))
    employer_elapsed = perf_counter() - started
    acme_labs = employers.filter(pl.col("organization_id") == "legal_acme_labs")
    assert acme_labs.height == 1
    assert acme_labs["identity_scope"].item() == "LEGAL_ENTITY"
    assert acme_labs["legal_entity_count"].item() == 1
    assert acme_labs["relevant_lca_count"].item() == 2
    assert acme_labs["relevant_certified_perm_count"].item() == 1
    assert acme_labs["everify_status"].item() == "UNKNOWN"
    assert employer_elapsed < 2

    institutions = service.list_institutions(InstitutionFilters(minimum_total_rd=100_000_000))
    assert institutions.height == 1
    assert institutions["cap_exemption_status"].item() == (
        "HIGHER_EDUCATION_CONTEXT_VERIFY_CAP_EXEMPTION"
    )
    assert institutions["research_staff_h1b_policy"].item() == "UNKNOWN"
    assert institutions["research_scale_star_rating"].item() == 5

    started = perf_counter()
    detail = service.get_organization_detail("parent_acme")
    detail_elapsed = perf_counter() - started
    assert detail is not None
    assert detail.summary.height == 1
    assert detail.legal_entities.height == 2
    assert detail.h1b_trends.height == 2
    assert detail.institutions.is_empty()
    assert {
        "case_id",
        "job_title_raw",
        "role_family",
        "program",
        "canonical_status",
        "worksite_city",
        "worksite_state",
        "wage_from",
        "wage_unit",
        "fiscal_year",
        "is_partial_period",
        "official_url",
        "sha256",
        "schema_version",
    }.issubset(detail.rating_supporting_cases.columns)
    assert {
        "lca-unsuccessful",
        "lca-nontechnical",
        "lca-unscored-visa",
        "perm-unsuccessful",
        "perm-nontechnical",
    }.isdisjoint(detail.rating_supporting_cases["case_id"].to_list())
    assert set(detail.rating_supporting_cases["canonical_status"]) <= {
        "CERTIFIED",
        "CERTIFIED-WITHDRAWN",
        "CERTIFIED-EXPIRED",
    }
    assert service.get_rating_supporting_cases("parent_acme", limit=1).height == 1
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
    assert institution_comparison["research_scale_star_rating"].item() == 5
    with pytest.raises(ValueError, match="at most five"):
        service.compare_organizations(tuple(f"organization-{index}" for index in range(6)))

    health = service.get_data_health()
    assert health.source_coverage.height >= 5
    assert {
        "raw_row_count",
        "normalized_row_count",
        "is_quarter_partition",
        "coverage_start_quarter",
    }.issubset(health.source_artifacts.columns)
    assert not health.source_artifacts["is_quarter_partition"].any()
    assert health.quality_checks.height > 0
    assert "FAIL" in health.quality_checks["status"].to_list()
    assert service.get_overview().unresolved_entity_match_count == 1
    assert service.get_evidence_review().entity.height == 1

    csv_export = service.export_employers(EmployerFilters(search="Acme"), "csv")
    parquet_export = service.export_employers(EmployerFilters(search="Acme"), "parquet")
    assert csv_export.startswith(b"organization_id,")
    assert pl.read_parquet(parquet_export).height >= 2
    service.close()


def test_candidate_evidence_distinguishes_partial_coverage_from_unresolved_identity(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    _build_fixture(data_root)

    resolved = data_root / "resolved"
    legal_entities_path = resolved / "legal_entities.parquet"
    legal_entities = pl.read_parquet(legal_entities_path)
    pl.concat(
        [
            legal_entities,
            pl.DataFrame(
                {
                    "legal_entity_id": [
                        "legal_unresolved_university",
                        "legal_unresolved_acme",
                    ],
                    "legal_name": ["State University Springfield", "Acme Labs West"],
                    "normalized_legal_name": [
                        "STATE UNIVERSITY SPRINGFIELD",
                        "ACME LABS WEST",
                    ],
                    "parent_organization_id": [None, None],
                    "city": ["Springfield", "Oakland"],
                    "state": ["MO", "CA"],
                    "postal_code": ["65806", "94612"],
                    "country": ["US", "US"],
                    "organization_type": ["UNKNOWN", "UNKNOWN"],
                    "institution_id": [None, None],
                    "created_by": ["FIXTURE", "FIXTURE"],
                    "review_status": ["REVIEW_REQUIRED", "REVIEW_REQUIRED"],
                }
            ),
        ],
        how="vertical_relaxed",
    ).write_parquet(legal_entities_path)

    aliases_path = resolved / "entity_aliases.parquet"
    aliases = pl.read_parquet(aliases_path)
    pl.concat(
        [
            aliases,
            pl.DataFrame(
                {
                    "alias_raw": ["State University Springfield", "Acme Labs West"],
                    "source_id": ["dol_lca", "dol_lca"],
                    "city": ["Springfield", "Oakland"],
                    "state": ["MO", "CA"],
                    "match_method": [
                        "EXACT_NAME_LOCATION_CONFLICT",
                        "FUZZY_CANDIDATE_UNMERGED",
                    ],
                    "match_score": [0.95, 0.91],
                    "review_status": ["REVIEW_REQUIRED", "REVIEW_REQUIRED"],
                    "match_status": ["REVIEW_REQUIRED", "REVIEW_REQUIRED"],
                    "occurrence_count": [1, 1],
                    "legal_entity_id": [
                        "legal_unresolved_university",
                        "legal_unresolved_acme",
                    ],
                    "parent_organization_id": [None, None],
                    "candidate_legal_entity_id": ["legal_university", "legal_acme_labs"],
                }
            ),
        ],
        how="vertical_relaxed",
    ).write_parquet(aliases_path)

    lca_path = next(
        (data_root / "classified" / "sources" / "dol_lca" / "fy=2025").glob("*.parquet")
    )
    lca = pl.read_parquet(lca_path).with_columns(
        pl.when(pl.col("case_id") == "dol_lca-2")
        .then(pl.lit("legal_unresolved_university"))
        .otherwise(pl.col("legal_entity_id"))
        .alias("legal_entity_id"),
        pl.when(pl.col("case_id") == "dol_lca-2")
        .then(pl.lit("State University Springfield"))
        .otherwise(pl.col("employer_name_raw"))
        .alias("employer_name_raw"),
        pl.when(pl.col("case_id") == "dol_lca-2")
        .then(pl.lit("Certified"))
        .otherwise(pl.col("case_status"))
        .alias("case_status"),
    )
    unresolved_acme = lca.head(1).with_columns(
        pl.lit("dol_lca-unresolved-acme").alias("case_id"),
        pl.lit("Acme Labs West").alias("employer_name_raw"),
        pl.lit("legal_unresolved_acme").alias("legal_entity_id"),
        pl.lit(None, dtype=pl.String).alias("parent_organization_id"),
        pl.lit("Certified").alias("case_status"),
    )
    pl.concat([lca, unresolved_acme], how="vertical_relaxed").write_parquet(lca_path)

    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    employer_metrics = pl.read_parquet(data_root / "processed" / "employer_metrics.parquet")
    target = employer_metrics.filter(pl.col("organization_id") == "legal_university")
    unresolved_source = employer_metrics.filter(
        pl.col("organization_id") == "legal_unresolved_university"
    )
    partial = employer_metrics.filter(pl.col("organization_id") == "legal_acme_labs")
    partial_parent = employer_metrics.filter(pl.col("organization_id") == "parent_acme")
    institution = pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")

    assert target["entity_resolution_valid"].item()
    assert not target["h1b_entity_resolution_valid"].item()
    assert target["h1b_entity_coverage_state"].item() == "UNRESOLVED_IDENTITY"
    assert target["entity_coverage_state"].item() == "UNRESOLVED_IDENTITY"
    assert target["has_unresolved_h1b_candidate_evidence"].item()
    assert target["weighted_relevant_lca_count"].item() == 0
    assert target["h1b_history_status"].item() == "UNRATED"
    assert target["green_card_history_status"].item() == "RATED"
    assert not target["has_unresolved_perm_candidate_evidence"].item()
    assert unresolved_source["weighted_relevant_lca_count"].item() == 1
    assert institution["has_unresolved_h1b_candidate_evidence"].item()
    assert institution["h1b_history_status"].item() == "UNRATED"
    for scored in (partial, partial_parent):
        assert scored["h1b_entity_coverage_state"].item() == "PARTIAL_ENTITY_COVERAGE"
        assert scored["entity_coverage_state"].item() == "PARTIAL_ENTITY_COVERAGE"
        assert scored["h1b_history_status"].item() == "RATED"
        assert scored["overall_sponsorship_status"].item() == "RATED"
        assert scored["weighted_relevant_lca_count"].item() == 2
        assert (
            "Rating is based on confirmed records. Additional ambiguous records were excluded."
            in scored["h1b_history_explanation"].item()
        )


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
    opt = pl.DataFrame(
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
    )
    opt_path = processed / "opt_employer_observations.parquet"
    opt.write_parquet(opt_path)
    opt_manifest_path = processed / "opt_artifact.parquet"
    opt.write_parquet(opt_manifest_path)
    _write_active_manifests(
        output_root,
        [
            (
                "sevp_opt",
                2024,
                None,
                False,
                "2024_Top200_Employers_OPT_STEM_OPT_Students.pdf",
                opt_manifest_path,
            )
        ],
    )

    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    selected_artifacts = pl.read_parquet(processed / "source_artifacts.parquet")
    assert selected_artifacts.filter(pl.col("source_id") == "sevp_opt")[
        "source_artifact_id"
    ].to_list() == ["opt_artifact"]
    DuckDBBuilder(data_root=data_root, database_path=database_path).build()
    service = DuckDBExplorerService(database_path)

    acme_rows = service.list_employers(EmployerFilters(search="Acme"))
    acme = acme_rows.filter(pl.col("organization_id") == "parent_acme")
    acme_legal_entities = acme_rows.filter(pl.col("identity_scope") == "LEGAL_ENTITY")
    university = service.list_employers(EmployerFilters(search="State University"))
    assert acme.height == 1
    assert acme_legal_entities.height == 2
    assert acme["everify_status"].item() == "CONFIRMED_ACTIVE"
    assert acme["known_opt_observation"].item() == "OBSERVED_POSITIVE"
    assert acme_legal_entities["everify_status"].to_list() == ["UNKNOWN", "UNKNOWN"]
    assert university["everify_status"].item() == "UNKNOWN"
    university_detail = service.get_organization_detail("legal_university")
    assert university_detail is not None
    assert university_detail.everify_evidence["enrollment_status"].to_list() == ["NO_MATCH"]
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
    rating_columns = [
        "h1b_history_score",
        "h1b_history_status",
        "h1b_history_star_rating",
        "green_card_history_score",
        "green_card_history_status",
        "green_card_history_star_rating",
        "overall_sponsorship_score",
        "overall_sponsorship_status",
        "overall_sponsorship_star_rating",
        "score_version",
    ]
    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    baseline_ratings = (
        pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")
        .filter(pl.col("institution_id") == "ipeds:100001")
        .select(rating_columns)
        .to_dicts()[0]
    )
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
    assert institutions["research_staff_h1b_policy"].item() == "YES"
    assert institutions["research_staff_permanent_residence_policy"].item() == "UNKNOWN"
    assert institutions["policy_review_status"].item() == "PARTIALLY_REVIEWED"
    assert institutions["policy_evidence_role"].item() == (
        "Supplemental; incomplete; not used in sponsorship ratings"
    )
    current_ratings = (
        pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")
        .filter(pl.col("institution_id") == "ipeds:100001")
        .select(rating_columns)
        .to_dicts()[0]
    )
    assert current_ratings == baseline_ratings
    detail = service.get_organization_detail("legal_university")
    assert detail is not None
    assert detail.policy_evidence["policy_fact_id"].to_list() == ["fact-accepted"]
    review = service.get_evidence_review()
    assert review.policy["policy_fact_id"].to_list() == ["fact-pending"]
    service.close()


def test_product_a_metrics_ignore_malformed_policy_sidecars(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    _build_fixture(data_root)
    processed = data_root / "processed"
    rating_columns = [
        "h1b_history_score",
        "h1b_history_status",
        "h1b_history_star_rating",
        "green_card_history_score",
        "green_card_history_status",
        "green_card_history_star_rating",
        "overall_sponsorship_score",
        "overall_sponsorship_status",
        "overall_sponsorship_star_rating",
    ]
    MetricsPipeline(data_root=data_root, output_root=output_root).build()
    baseline_ratings = pl.read_parquet(processed / "institution_metrics.parquet").select(
        rating_columns
    )
    (processed / "policy_documents.parquet").write_bytes(b"not a parquet file")
    pl.DataFrame({"legacy_policy_value": ["YES"]}).write_parquet(processed / "policy_facts.parquet")

    summary = MetricsPipeline(data_root=data_root, output_root=output_root).build()

    institution_metrics = pl.read_parquet(processed / "institution_metrics.parquet")
    assert summary.institution_count == 1
    assert institution_metrics.select(rating_columns).equals(baseline_ratings)
    assert institution_metrics["score_version"].unique().to_list() == ["product_a_scores_v1"]
    assert institution_metrics["research_staff_h1b_policy"].to_list() == ["UNKNOWN"]
    assert institution_metrics["policy_review_status"].to_list() == ["NOT_STARTED"]
    assert (
        "institution_policy"
        not in pl.read_parquet(processed / "data_health.parquet")["source_id"].to_list()
    )


def test_product_a_metrics_do_not_require_legacy_scoring_configs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    _build_fixture(data_root)

    summary = MetricsPipeline(
        data_root=data_root,
        output_root=output_root,
        scoring_config_path=tmp_path / "missing-scoring-v1.yaml",
        scoring_v2_config_path=tmp_path / "missing-scoring-v2.yaml",
    ).build()

    processed = data_root / "processed"
    employer_metrics = pl.read_parquet(processed / "employer_metrics.parquet")
    institution_metrics = pl.read_parquet(processed / "institution_metrics.parquet")
    assert summary.employer_count == employer_metrics.height
    assert employer_metrics["score_version"].unique().to_list() == ["product_a_scores_v1"]
    assert institution_metrics["score_version"].unique().to_list() == ["product_a_scores_v1"]
    assert (processed / "employer_scores.parquet").is_file()
    assert (processed / "institution_scores.parquet").is_file()
    assert not (processed / "employer_scores_v1.parquet").exists()
    assert not (processed / "institution_scores_v1.parquet").exists()
    assert not (processed / "employer_scores_v2.parquet").exists()


def test_product_a_database_ignores_corrupt_or_stale_policy_tables(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    baseline_database_path = tmp_path / "db" / "baseline.duckdb"
    policy_failure_database_path = tmp_path / "db" / "policy-failure.duckdb"
    _build_fixture(data_root)
    MetricsPipeline(data_root=data_root, output_root=output_root).build()

    baseline = DuckDBBuilder(
        data_root=data_root,
        database_path=baseline_database_path,
    ).build()
    with duckdb.connect(str(baseline_database_path), read_only=True) as connection:
        baseline_ratings = connection.execute(
            """
            SELECT organization_id, score_version,
                h1b_history_score, h1b_history_status, h1b_history_star_rating,
                green_card_history_score, green_card_history_status,
                green_card_history_star_rating,
                overall_sponsorship_score, overall_sponsorship_status,
                overall_sponsorship_star_rating
            FROM vw_employer_explorer
            ORDER BY organization_id
            """
        ).fetchall()

    processed = data_root / "processed"
    (processed / "policy_documents.parquet").write_bytes(b"not a parquet file")
    pl.DataFrame({"legacy_policy_value": ["YES"]}).write_parquet(processed / "policy_facts.parquet")
    (processed / "policy_review_queue.parquet").write_bytes(b"not a parquet file")

    rebuilt = DuckDBBuilder(
        data_root=data_root,
        database_path=policy_failure_database_path,
    ).build()

    assert rebuilt.employer_count == baseline.employer_count
    assert rebuilt.institution_count == baseline.institution_count
    with duckdb.connect(str(policy_failure_database_path), read_only=True) as connection:
        views = {
            row[0]
            for row in connection.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        }
        current_ratings = connection.execute(
            """
            SELECT organization_id, score_version,
                h1b_history_score, h1b_history_status, h1b_history_star_rating,
                green_card_history_score, green_card_history_status,
                green_card_history_star_rating,
                overall_sponsorship_score, overall_sponsorship_status,
                overall_sponsorship_star_rating
            FROM vw_employer_explorer
            ORDER BY organization_id
            """
        ).fetchall()
        score_versions = connection.execute(
            "SELECT DISTINCT score_version FROM vw_employer_explorer"
        ).fetchall()
        policy_evidence_count = connection.execute(
            "SELECT count(*) FROM vw_policy_evidence"
        ).fetchone()
        policy_review_count = connection.execute(
            "SELECT count(*) FROM vw_policy_review_queue"
        ).fetchone()
    assert set(REQUIRED_VIEWS).issubset(views)
    assert current_ratings == baseline_ratings
    assert score_versions == [("product_a_scores_v1",)]
    assert policy_evidence_count == (0,)
    assert policy_review_count == (0,)
