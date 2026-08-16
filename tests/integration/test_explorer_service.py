"""Integration contract for the Streamlit query-service boundary."""

from pathlib import Path

import duckdb

from sponsor_intel.services import ExplorerService, get_explorer_service


def test_foundation_service_is_a_read_only_explorer_boundary(tmp_path: Path) -> None:
    service = get_explorer_service(database_path=tmp_path / "missing.duckdb")
    status = service.get_status()

    assert isinstance(service, ExplorerService)
    assert status.data_available is False
    assert status.evidence_status == "UNKNOWN"
    assert status.phase == "Product A"
    assert "No presentation database has been built" in status.message
    assert "not sponsorship guarantees or legal advice" in status.disclaimer


def test_legacy_product_b_database_is_not_reported_as_product_a(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-product-b.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            """
            CREATE VIEW vw_employer_explorer AS SELECT
                'legacy-organization' AS organization_id,
                'evidence_metrics_v2_2026_08' AS metric_version,
                'evidence_scores_v2_2026_08' AS score_version,
                87.0 AS sponsorship_history_score,
                'A' AS sponsorship_history_grade,
                'A' AS h1b_history_grade,
                'B' AS green_card_history_grade
            """
        )

    service = get_explorer_service(database_path=database_path)
    status = service.get_status()

    assert isinstance(service, ExplorerService)
    assert status.data_available is False
    assert status.evidence_status == "UNKNOWN"
    assert status.phase == "Product A"
    assert "incompatible with Product A" in status.message


def test_streamlit_pages_do_not_issue_sql() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pages = [
        project_root / "app" / "Home.py",
        project_root / "app" / "pages" / "1_All_Employers.py",
        project_root / "app" / "pages" / "2_Research_Institutions.py",
        project_root / "app" / "pages" / "3_Organization_Detail.py",
        project_root / "app" / "pages" / "4_Compare.py",
        project_root / "app" / "pages" / "5_Evidence_Review.py",
        project_root / "app" / "pages" / "6_Data_Health.py",
    ]

    for page in pages:
        source = page.read_text(encoding="utf-8").upper()
        assert "SELECT " not in source
        assert "DUCKDB" not in source


def test_product_a_pages_do_not_restore_policy_or_research_pathway_ranking() -> None:
    project_root = Path(__file__).resolve().parents[2]
    active_rankings = [
        project_root / "app" / "Home.py",
        project_root / "app" / "pages" / "1_All_Employers.py",
        project_root / "app" / "pages" / "2_Research_Institutions.py",
        project_root / "app" / "pages" / "4_Compare.py",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in active_rankings)
    assert "decision_readiness" not in combined
    assert "research_pathway" not in combined
    assert "minimum policy" not in combined
