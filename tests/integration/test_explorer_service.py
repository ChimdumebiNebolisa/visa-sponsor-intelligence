"""Integration contract for the Streamlit query-service boundary."""

from sponsor_intel.services import ExplorerService, get_explorer_service


def test_foundation_service_is_a_read_only_explorer_boundary() -> None:
    service = get_explorer_service()
    status = service.get_status()

    assert isinstance(service, ExplorerService)
    assert status.data_available is False
    assert status.evidence_status == "UNKNOWN"
    assert "No source data has been ingested" in status.message
    assert "does not provide legal advice" in status.disclaimer
