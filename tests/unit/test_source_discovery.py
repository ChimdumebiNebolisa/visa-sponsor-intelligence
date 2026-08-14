"""Tests for canonical DOL artifact discovery."""

from pathlib import Path

import httpx

from sponsor_intel.sources.discovery import discover_dol_artifacts
from sponsor_intel.sources.errors import UnsafeSourceUrlError
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url
from sponsor_intel.sources.registry import SourceRegistry


def _transport() -> httpx.MockTransport:
    html = Path("tests/fixtures/dol_performance_sample.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.dol.gov"
        return httpx.Response(200, text=html, request=request)

    return httpx.MockTransport(handler)


def test_lca_discovery_selects_latest_quarter_and_handles_filename_typo() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    with OfficialHttpClient(config.official_domains, transport=_transport()) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2022)

    selected = {(item.fiscal_year, item.fiscal_quarter, item.file_name) for item in report.selected}
    assert selected == {
        (2025, 4, "LCA_Disclosure_Data_FY2025_Q4.xlsx"),
        (2026, 2, "LCA_Dislclosure_Data_FY2026_Q2.xlsx"),
    }
    assert all(item.record_layout_url for item in report.selected)
    assert next(item for item in report.selected if item.fiscal_year == 2026).is_partial_period


def test_perm_discovery_preserves_old_and_new_form_files() -> None:
    config = SourceRegistry.from_yaml().get("dol_perm")
    with OfficialHttpClient(config.official_domains, transport=_transport()) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2024)

    assert {item.variant for item in report.selected} == {"standard", "new_form"}
    assert len(report.selected) == 2
    new_form = next(item for item in report.selected if item.variant == "new_form")
    assert new_form.record_layout_url is not None
    assert "New_Form" in new_form.record_layout_url


def test_official_url_policy_rejects_untrusted_hosts() -> None:
    try:
        validate_official_url("https://evil.example/data.xlsx", ("dol.gov",))
    except UnsafeSourceUrlError as error:
        assert "outside the official source domains" in str(error)
    else:
        raise AssertionError("Untrusted source URL was accepted")
