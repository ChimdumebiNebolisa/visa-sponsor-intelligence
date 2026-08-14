"""Integration contract for the Streamlit query-service boundary."""

from pathlib import Path

from sponsor_intel.services import ExplorerService, get_explorer_service


def test_foundation_service_is_a_read_only_explorer_boundary(tmp_path: Path) -> None:
    service = get_explorer_service(database_path=tmp_path / "missing.duckdb")
    status = service.get_status()

    assert isinstance(service, ExplorerService)
    assert status.data_available is False
    assert status.evidence_status == "UNKNOWN"
    assert "No presentation database has been built" in status.message
    assert "does not provide legal advice" in status.disclaimer


def test_streamlit_pages_do_not_issue_sql() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pages = [
        project_root / "app" / "Home.py",
        project_root / "app" / "pages" / "1_All_Employers.py",
        project_root / "app" / "pages" / "2_Research_Institutions.py",
        project_root / "app" / "pages" / "3_Organization_Detail.py",
    ]

    for page in pages:
        source = page.read_text(encoding="utf-8").upper()
        assert "SELECT " not in source
        assert "DUCKDB" not in source
