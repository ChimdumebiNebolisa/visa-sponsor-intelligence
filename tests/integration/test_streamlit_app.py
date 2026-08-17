"""End-to-end Streamlit checks over the sanitized Product A CI fixture."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from sponsor_intel.services import DuckDBExplorerService, EmployerFilters

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def product_a_ci_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Use the CI-built fixture when present, otherwise build one for local tests."""

    configured_root = os.environ.get("SPONSOR_INTEL_CI_FIXTURE_ROOT")
    if configured_root:
        database_path = Path(configured_root).resolve() / "db" / "phase10-ci.duckdb"
        if not database_path.is_file():
            raise AssertionError(f"CI fixture database is unavailable: {database_path}")
    else:
        fixture_root = tmp_path_factory.mktemp("product-a-ci-fixture")
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts/build_phase10_ci_fixture.py"),
                "--output-root",
                str(fixture_root),
            ],
            cwd=PROJECT_ROOT,
            check=True,
        )
        database_path = fixture_root / "db" / "phase10-ci.duckdb"
    st.cache_resource.clear()
    return database_path


@contextmanager
def _fixture_environment(database_path: Path) -> Iterator[None]:
    with (
        patch.dict(
            os.environ,
            {
                "SPONSOR_INTEL_DB_PATH": str(database_path),
                "SPONSOR_INTEL_DEPLOYMENT_MODE": "local",
                "SPONSOR_INTEL_REQUIRE_DATA": "true",
            },
        ),
        patch.object(sys, "path", [str(PROJECT_ROOT / "app"), *sys.path]),
    ):
        yield


def _run_page(relative_path: str, database_path: Path) -> AppTest:
    with _fixture_environment(database_path):
        return AppTest.from_file(
            str(PROJECT_ROOT / relative_path),
            default_timeout=20,
        ).run()


def _assert_clean(app: AppTest) -> None:
    assert [exception.message for exception in app.exception] == []


def test_fixture_exercises_product_a_service_contracts(
    product_a_ci_database: Path,
) -> None:
    service = DuckDBExplorerService(product_a_ci_database)
    try:
        status = service.get_status()
        assert status.phase == "Product A"
        assert status.score_version == "product_a_scores_v1"
        assert status.current_partial_fiscal_year == 2026
        assert status.current_partial_quarter == 2

        overview = service.get_overview()
        assert overview.legal_entity_count == 4
        assert overview.parent_organization_count == 1
        assert overview.institution_count == 2
        assert overview.relevant_lca_count > 0
        assert overview.relevant_certified_perm_count > 0

        employers = service.list_employers()
        assert employers.height >= 4
        assert set(employers["identity_scope"].unique()) == {"LEGAL_ENTITY", "PARENT_ROLLUP"}
        assert (
            employers[0, "overall_sponsorship_score"] >= employers[1, "overall_sponsorship_score"]
        )
        assert employers["overall_sponsorship_stars"].str.contains("%", literal=True).sum() == 0

        institutions = service.list_institutions()
        assert institutions["official_name"].to_list() == [
            "Aurora Research University",
            "Beacon Technical Institute",
        ]
        assert institutions[0, "total_rd"] < institutions[1, "total_rd"]
        assert (
            institutions[0, "overall_sponsorship_score"]
            >= institutions[1, "overall_sponsorship_score"]
        )

        detail = service.get_organization_detail("parent_orbit")
        assert detail is not None
        assert detail.legal_entities.height == 2
        assert {"official_url", "sha256", "schema_version"}.issubset(detail.provenance.columns)
        assert detail.provenance["official_url"].drop_nulls().len() > 0
        comparison = service.compare_organizations(("parent_orbit", "legal_aurora"))
        assert comparison.height == 2
        assert comparison["relevant_lca_count"].sum() > 0
        assert service.export_employers(EmployerFilters(search="Orbit"), "csv").startswith(
            b"organization_id,"
        )
        artifacts = service.get_data_health().source_artifacts
        assert {"download_url", "sha256", "normalized_row_count"}.issubset(artifacts.columns)
        assert artifacts["sha256"].drop_nulls().len() > 0
        lca_artifacts = artifacts.filter(artifacts["source_id"] == "dol_lca")
        assert lca_artifacts["coverage_start_quarter"].to_list() == [1, 1]
        assert not lca_artifacts["is_quarter_partition"].any()
    finally:
        service.close()


def test_home_has_versions_top_lists_and_partial_warning(product_a_ci_database: Path) -> None:
    app = _run_page("app/Home.py", product_a_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Sponsorship Intelligence Explorer"]
    metrics = {metric.label: int(metric.value.replace(",", "")) for metric in app.metric}
    assert metrics["Legal entities"] > 0
    assert metrics["Institutions"] > 0
    assert metrics["Certified technical H-1B LCA"] > 0
    assert metrics["Certified technical PERM"] > 0
    assert any("FY2026 Q2 is partial" in warning.value for warning in app.warning)
    captions = " ".join(caption.value for caption in app.caption)
    assert "product_a_scores_v1" in captions
    assert len(app.dataframe) >= 3
    employer_table = app.dataframe[0].value
    institution_table = app.dataframe[1].value
    assert {
        "overall_sponsorship_star_label",
        "green_card_history_star_label",
        "h1b_history_star_label",
    }.issubset(employer_table.columns)
    assert {
        "overall_sponsorship_star_label",
        "green_card_history_star_label",
        "h1b_history_star_label",
        "research_scale_star_label",
    }.issubset(institution_table.columns)


def test_employer_explorer_displays_stars_and_exact_uscis_label(
    product_a_ci_database: Path,
) -> None:
    app = _run_page("app/pages/1_All_Employers.py", product_a_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["All Employers"]
    table = app.dataframe[0].value
    assert len(table) >= 4
    assert "overall_sponsorship_stars" in table.columns
    assert "overall_sponsorship_score" not in table.columns
    labels = [element.label for element in app.number_input]
    assert "Minimum employer-level H-1B initial approvals" in labels


def test_institution_explorer_defaults_to_sponsorship_not_research_scale(
    product_a_ci_database: Path,
) -> None:
    app = _run_page("app/pages/2_Research_Institutions.py", product_a_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Universities and Research Institutions"]
    table = app.dataframe[0].value
    assert table["official_name"].to_list() == [
        "Aurora Research University",
        "Beacon Technical Institute",
    ]
    assert table.iloc[0]["overall_sponsorship_stars"] != "UNKNOWN"
    assert "research_pathway_score" not in table.columns
    assert {
        "overall_sponsorship_star_label",
        "green_card_history_star_label",
        "h1b_history_star_label",
        "research_scale_star_label",
    }.issubset(table.columns)


def test_organization_detail_has_rating_reasons_and_raw_evidence(
    product_a_ci_database: Path,
) -> None:
    with _fixture_environment(product_a_ci_database):
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app/pages/3_Organization_Detail.py"),
            default_timeout=20,
        )
        app.query_params["organization_id"] = "parent_orbit"
        app.run()
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Organization Detail"]
    assert "Orbit Group" in [heading.value for heading in app.subheader]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Legal entities"] == "2"
    assert int(metrics["Certified technical H-1B LCA"].replace(",", "")) > 0
    assert int(metrics["Employer-level H-1B initial approvals"].replace(",", "")) > 0
    assert int(metrics["Certified technical PERM"].replace(",", "")) > 0
    assert len(app.tabs) == 6
    rendered = " ".join(element.value for element in app.markdown)
    assert "Why this rating" in rendered
    assert "What this does not prove" in rendered
    supporting = next(
        frame.value for frame in app.dataframe if "canonical_status" in frame.value.columns
    )
    assert len(supporting) > 0
    assert set(supporting["canonical_status"]) <= {
        "CERTIFIED",
        "CERTIFIED-WITHDRAWN",
        "CERTIFIED-EXPIRED",
    }


def test_organization_detail_context_omits_hidden_and_policy_era_scores(
    product_a_ci_database: Path,
) -> None:
    with _fixture_environment(product_a_ci_database):
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app/pages/3_Organization_Detail.py"),
            default_timeout=20,
        )
        app.query_params["organization_id"] = "legal_aurora"
        app.run()
    _assert_clean(app)
    context = next(
        frame.value
        for frame in app.dataframe
        if {"official_name", "research_scale_stars"}.issubset(frame.value.columns)
    )
    assert not any(column.endswith("_score") for column in context.columns)
    assert "research_pathway_score" not in context.columns
    assert not any("policy" in column for column in context.columns)
    assert {
        "overall_sponsorship_star_label",
        "green_card_history_star_label",
        "h1b_history_star_label",
        "research_scale_star_label",
    }.issubset(context.columns)


def test_comparison_renders_star_and_annual_evidence(product_a_ci_database: Path) -> None:
    with _fixture_environment(product_a_ci_database):
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app/pages/4_Compare.py"),
            default_timeout=20,
        ).run()
        app.text_input[0].input("a").run()
        options = app.multiselect[0].options
        app.multiselect[0].set_value(options[:2]).run()
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Compare Organizations"]
    ratings = app.dataframe[0].value
    assert "Overall Sponsorship" in ratings["Metric"].to_list()
    assert "Overall Sponsorship score" not in ratings["Metric"].to_list()
    observed = app.dataframe[1].value
    assert "Employer-level H-1B initial approvals" in observed["Metric"].to_list()
    assert len(app.dataframe) >= 5


def test_data_health_shows_build_and_score_versions(product_a_ci_database: Path) -> None:
    app = _run_page("app/pages/6_Data_Health.py", product_a_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Data Health"]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Publication gate"] == "PASS"
    assert metrics["Warnings"] == "1"
    assert metrics["Score version"] == "product_a_scores_v1"
    assert any(
        "FY2026 Q2 is partial and must not be compared directly" in warning.value
        for warning in app.warning
    )


def test_release_configuration_error_is_clear_and_user_safe() -> None:
    st.cache_resource.clear()
    try:
        with (
            patch.dict(
                os.environ,
                {
                    "SPONSOR_INTEL_DEPLOYMENT_MODE": "release",
                    "SPONSOR_INTEL_REQUIRE_DATA": "true",
                    "GITHUB_RELEASE_READ_TOKEN": "",
                },
            ),
            patch.object(sys, "path", [str(PROJECT_ROOT / "app"), *sys.path]),
        ):
            app = AppTest.from_file(str(PROJECT_ROOT / "app/Home.py"), default_timeout=20).run()
        _assert_clean(app)
        assert [error.value for error in app.error] == [
            "Unable to load a verified quality-approved data release."
        ]
        captions = " ".join(caption.value for caption in app.caption)
        assert "Deployment configuration is incomplete or invalid" in captions
        assert "GITHUB_RELEASE_READ_TOKEN" not in captions
    finally:
        st.cache_resource.clear()
