"""End-to-end Streamlit checks over the sanitized Phase 10 CI fixture."""

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
def phase10_ci_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Use the CI-built fixture when present, otherwise build one for local tests."""

    configured_root = os.environ.get("SPONSOR_INTEL_CI_FIXTURE_ROOT")
    if configured_root:
        database_path = Path(configured_root).resolve() / "db" / "phase10-ci.duckdb"
        if not database_path.is_file():
            raise AssertionError(f"CI fixture database is unavailable: {database_path}")
    else:
        fixture_root = tmp_path_factory.mktemp("phase10-ci-fixture")
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


def test_fixture_exercises_service_contracts_and_decision_ordering(
    phase10_ci_database: Path,
) -> None:
    service = DuckDBExplorerService(phase10_ci_database)
    try:
        overview = service.get_overview()
        assert overview.legal_entity_count == 4
        assert overview.parent_organization_count == 1
        assert overview.institution_count == 2
        assert overview.relevant_lca_count > 0
        assert overview.relevant_certified_perm_count > 0
        assert overview.reviewed_policy_institution_count == 1

        institutions = service.list_institutions()
        assert institutions["official_name"].to_list() == [
            "Aurora Research University",
            "Beacon Technical Institute",
        ]
        assert institutions[0, "decision_readiness_tier"] == "TIER_1_REVIEWED"
        assert "critical quality gate passed" in institutions[0, "decision_readiness_explanation"]
        assert institutions[0, "total_rd"] < institutions[1, "total_rd"]
        assert institutions[1, "research_staff_h1b_policy"] == "UNKNOWN"
        assert institutions[1, "research_staff_h1b_policy"] not in {"NO", "0"}

        detail = service.get_organization_detail("parent_orbit")
        assert detail is not None
        assert detail.legal_entities.height == 2
        comparison = service.compare_organizations(("parent_orbit", "legal_aurora"))
        assert comparison.height == 2
        assert comparison["relevant_lca_count"].sum() > 0
        assert service.export_employers(EmployerFilters(search="Orbit"), "csv").startswith(
            b"organization_id,"
        )

        status = service.get_status()
        assert status.current_partial_fiscal_year == 2026
        assert status.current_partial_quarter == 2
    finally:
        service.close()


def test_home_has_nonzero_data_and_partial_period_warning(phase10_ci_database: Path) -> None:
    app = _run_page("app/Home.py", phase10_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Sponsorship Intelligence Explorer"]
    metrics = {metric.label: int(metric.value.replace(",", "")) for metric in app.metric}
    assert metrics["Legal entities"] > 0
    assert metrics["Institutions"] > 0
    assert metrics["Relevant H-1B LCA"] > 0
    assert metrics["Relevant certified PERM"] > 0
    assert any("FY2026 Q2 is partial" in warning.value for warning in app.warning)
    assert app.dataframe[0].value.iloc[0]["official_name"] == "Aurora Research University"


def test_employer_explorer_keeps_missing_evidence_unknown(phase10_ci_database: Path) -> None:
    app = _run_page("app/pages/1_All_Employers.py", phase10_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["All Employers"]
    table = app.dataframe[0].value
    assert len(table) == 3
    beacon = table.loc[table["organization_name"] == "Beacon Technical Institute"].iloc[0]
    assert beacon["everify_status"] == "UNKNOWN"
    assert beacon["everify_status"] not in {"NO", "0"}


def test_institution_explorer_defaults_to_decision_readiness_not_rd(
    phase10_ci_database: Path,
) -> None:
    app = _run_page("app/pages/2_Research_Institutions.py", phase10_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Research Institutions"]
    table = app.dataframe[0].value
    assert table["official_name"].to_list() == [
        "Aurora Research University",
        "Beacon Technical Institute",
    ]
    beacon = table.loc[table["official_name"] == "Beacon Technical Institute"].iloc[0]
    assert beacon["research_staff_permanent_residence_policy"] == "UNKNOWN"
    assert beacon["research_staff_permanent_residence_policy"] != "NO"


def test_organization_detail_loads_parent_identity_and_evidence(
    phase10_ci_database: Path,
) -> None:
    with _fixture_environment(phase10_ci_database):
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
    assert int(metrics["Relevant LCA"].replace(",", "")) > 0
    assert int(metrics["USCIS initial approvals"].replace(",", "")) > 0
    assert int(metrics["Relevant certified PERM"].replace(",", "")) > 0
    assert len(app.tabs) == 6


def test_institution_detail_explanations_use_institution_evidence(
    phase10_ci_database: Path,
) -> None:
    with _fixture_environment(phase10_ci_database):
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app/pages/3_Organization_Detail.py"),
            default_timeout=20,
        )
        app.query_params["organization_id"] = "legal_aurora"
        app.run()
    _assert_clean(app)
    rendered = " ".join(element.value for element in app.markdown)
    assert "TIER_1_REVIEWED" in rendered
    assert "research strength has no score" not in rendered
    assert "PERM support is unknown" not in rendered
    assert "EB-1B support was reviewed but is not stated" in rendered


def test_comparison_renders_observations_and_scores(phase10_ci_database: Path) -> None:
    with _fixture_environment(phase10_ci_database):
        app = AppTest.from_file(
            str(PROJECT_ROOT / "app/pages/4_Compare.py"),
            default_timeout=20,
        ).run()
        app.text_input[0].input("a").run()
        options = app.multiselect[0].options
        app.multiselect[0].set_value(options[:2]).run()
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Compare Organizations"]
    assert len(app.dataframe) == 4
    observed = app.dataframe[0].value
    everify = observed.loc[observed["Metric"] == "E-Verify"].iloc[0, 1:].to_list()
    assert everify == ["UNKNOWN", "UNKNOWN"]
    assert all(value not in {"NO", "0"} for value in everify)
    scores = app.dataframe[2].value
    assert "Sponsorship history score" in scores["Metric"].to_list()


def test_data_health_has_quality_checks_and_partial_period_warning(
    phase10_ci_database: Path,
) -> None:
    app = _run_page("app/pages/6_Data_Health.py", phase10_ci_database)
    _assert_clean(app)
    assert [title.value for title in app.title] == ["Data Health"]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Publication gate"] == "PASS"
    assert metrics["Warnings"] == "1"
    assert len(app.dataframe[0].value) >= 5
    assert len(app.dataframe[1].value) == 2
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
