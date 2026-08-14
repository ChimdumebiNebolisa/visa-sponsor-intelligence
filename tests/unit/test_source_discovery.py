"""Tests for canonical DOL artifact discovery."""

from pathlib import Path

import httpx

from sponsor_intel.sources.discovery import discover_dol_artifacts
from sponsor_intel.sources.errors import UnsafeSourceUrlError
from sponsor_intel.sources.federal_discovery import (
    discover_herd,
    discover_ipeds,
    discover_uscis_h1b,
)
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


def test_uscis_discovery_uses_current_hub_period_and_full_data_sheet() -> None:
    config = SourceRegistry.from_yaml().get("uscis_h1b")
    html = (
        "<p>Data from fiscal year 2009 through fiscal year 2026 (quarter 3).</p>"
        '<a href="/tools/reports/understanding-h1b">'
        "Understanding Our H-1B Employer Data Hub</a>"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_uscis_h1b(config, client, from_fiscal_year=2022)

    assert len(report.selected) == 5
    candidate = report.selected[-1]
    assert (candidate.fiscal_year, candidate.fiscal_quarter) == (2026, 3)
    assert candidate.is_partial_period is True
    assert candidate.download_url.endswith("Fiscal%20Year%20%20%20=2026")


def test_uscis_discovery_reports_reviewed_period_fallback_when_landing_is_blocked() -> None:
    config = SourceRegistry.from_yaml().get("uscis_h1b")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, text="Forbidden", request=request)
    )
    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_uscis_h1b(config, client, from_fiscal_year=2026)

    assert report.selected[0].fiscal_year == 2026
    assert report.warnings
    assert "blocked automated access" in report.warnings[0]


def test_ipeds_discovery_selects_latest_directory_with_dictionary() -> None:
    config = SourceRegistry.from_yaml().get("ipeds")
    html = "".join(
        [
            '<a href="/ipeds/complete-data-files/HD2024.zip">HD2024</a>',
            '<a href="/ipeds/complete-data-files/HD2024_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/HD2025.zip">HD2025</a>',
            '<a href="/ipeds/complete-data-files/HD2025_Dict.zip">Dictionary</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_ipeds(config, client, from_fiscal_year=2022)

    assert len(report.candidates) == 2
    assert report.selected[0].fiscal_year == 2025
    assert report.selected[0].record_layout_url is not None
    assert report.selected[0].record_layout_url.endswith("HD2025_Dict.zip")


def test_herd_discovery_requires_standard_and_short_pairs() -> None:
    config = SourceRegistry.from_yaml().get("herd")
    html = "".join(
        [
            '<a href="/821/assets/0/files/higher_education_r_and_d_2023.zip">2023</a>',
            '<a href="/821/assets/0/files/higher_education_r_and_d_2023_short.zip">short</a>',
            '<a href="/821/assets/0/files/higher_education_r_and_d_2024.zip">2024</a>',
            '<a href="/821/assets/0/files/higher_education_r_and_d_2024_short.zip">short</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_herd(config, client, from_fiscal_year=2024)

    assert {(item.fiscal_year, item.variant) for item in report.selected} == {
        (2024, "standard"),
        (2024, "short"),
    }
