"""Tests for canonical DOL artifact discovery."""

from pathlib import Path

import httpx
import pytest

from sponsor_intel.sources.discovery import discover_dol_artifacts
from sponsor_intel.sources.errors import SourceDiscoveryError, UnsafeSourceUrlError
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


def test_lca_discovery_selects_completed_partitions_and_latest_partial_snapshot() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    with OfficialHttpClient(config.official_domains, transport=_transport()) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2022)

    selected = {(item.fiscal_year, item.fiscal_quarter, item.file_name) for item in report.selected}
    assert selected == {
        (2025, 1, "LCA_Disclosure_Data_FY2025_Q1.xlsx"),
        (2025, 2, "LCA_Disclosure_Data_FY2025_Q2.xlsx"),
        (2025, 3, "LCA_Disclosure_Data_FY2025_Q3.xlsx"),
        (2025, 4, "LCA_Disclosure_Data_FY2025_Q4.xlsx"),
        (2026, 2, "LCA_Dislclosure_Data_FY2026_Q2.xlsx"),
    }
    assert all(item.record_layout_url for item in report.selected)
    assert all(not item.is_partial_period for item in report.selected if item.fiscal_year == 2025)
    assert all(item.is_quarter_partition for item in report.selected if item.fiscal_year == 2025)
    assert [
        (item.coverage_start_quarter, item.fiscal_quarter)
        for item in report.selected
        if item.fiscal_year == 2025
    ] == [(1, 1), (2, 2), (3, 3), (4, 4)]
    current = next(item for item in report.selected if item.fiscal_year == 2026)
    assert current.is_partial_period
    assert not current.is_quarter_partition
    assert current.coverage_start_quarter == 1


def test_lca_discovery_uses_all_fy2022_partitions_and_not_stale_current_snapshots() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2022_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2022 Q{quarter}</a>"
                for quarter in range(1, 5)
            ],
            '<a href="/files/LCA_Record_Layout_FY2022_Q4.pdf">LCA Record Layout FY2022 Q4</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2023_Q1.xlsx">LCA Disclosure Data FY2023 Q1</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2023_Q2.xlsx">LCA Disclosure Data FY2023 Q2</a>',
            '<a href="/files/LCA_Record_Layout_FY2023_Q2.pdf">LCA Record Layout FY2023 Q2</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2022)

    fy2022 = [item for item in report.selected if item.fiscal_year == 2022]
    assert [item.fiscal_quarter for item in fy2022] == [1, 2, 3, 4]
    assert [item.coverage_start_quarter for item in fy2022] == [1, 2, 3, 4]
    assert all(not item.is_partial_period for item in fy2022)
    assert all(item.is_quarter_partition for item in fy2022)
    fy2023 = [item for item in report.selected if item.fiscal_year == 2023]
    assert [item.fiscal_quarter for item in fy2023] == [2]
    assert fy2023[0].is_partial_period
    assert fy2023[0].coverage_start_quarter == 1


def test_lca_discovery_prefers_completed_annual_artifact_over_quarter_partitions() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2024_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2024 Q{quarter}</a>"
                for quarter in range(1, 5)
            ],
            '<a href="/files/LCA_Disclosure_Data_FY2024.xlsx">'
            "LCA Disclosure Data FY2024 Annual</a>",
            '<a href="/files/LCA_Record_Layout_FY2024.pdf">LCA Record Layout FY2024 Annual</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2025_Q2.xlsx">LCA Disclosure Data FY2025 Q2</a>',
            '<a href="/files/LCA_Record_Layout_FY2025_Q2.pdf">LCA Record Layout FY2025 Q2</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2024)

    completed = [item for item in report.selected if item.fiscal_year == 2024]
    assert [(item.fiscal_quarter, item.file_name) for item in completed] == [
        (None, "LCA_Disclosure_Data_FY2024.xlsx")
    ]
    assert not completed[0].is_quarter_partition
    assert completed[0].coverage_start_quarter == 1


def test_perm_discovery_preserves_old_and_new_form_files() -> None:
    config = SourceRegistry.from_yaml().get("dol_perm")
    with OfficialHttpClient(config.official_domains, transport=_transport()) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2024)

    assert {item.variant for item in report.selected} == {"standard", "new_form"}
    assert len(report.selected) == 2
    new_form = next(item for item in report.selected if item.variant == "new_form")
    assert new_form.record_layout_url is not None
    assert "New_Form" in new_form.record_layout_url


def test_perm_discovery_selects_one_annual_artifact_per_form_variant_on_q4_tie() -> None:
    config = SourceRegistry.from_yaml().get("dol_perm")
    html = "".join(
        [
            '<a href="/files/PERM_Disclosure_Data_FY2024_Q4.xlsx">'
            "PERM Disclosure Data FY2024 Q4</a>",
            '<a href="/files/PERM_Disclosure_Data_FY2024.xlsx">'
            "PERM Disclosure Data FY2024 Annual</a>",
            '<a href="/files/PERM_Disclosure_Data_New_Form_FY2024_Q4.xlsx">'
            "PERM Disclosure Data New Form FY2024 Q4</a>",
            '<a href="/files/PERM_Disclosure_Data_New_Form_FY2024.xlsx">'
            "PERM Disclosure Data New Form FY2024 Annual</a>",
            '<a href="/files/PERM_Record_Layout_FY2024.pdf">PERM Record Layout FY2024 Annual</a>',
            '<a href="/files/PERM_New_Form_Record_Layout_FY2024.pdf">'
            "PERM New Form Record Layout FY2024 Annual</a>",
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2024)

    assert {(item.variant, item.fiscal_quarter, item.file_name) for item in report.selected} == {
        ("standard", None, "PERM_Disclosure_Data_FY2024.xlsx"),
        ("new_form", None, "PERM_Disclosure_Data_New_Form_FY2024.xlsx"),
    }
    assert {
        (item.variant, item.record_layout_url.rsplit("/", 1)[-1])
        for item in report.selected
        if item.record_layout_url is not None
    } == {
        ("standard", "PERM_Record_Layout_FY2024.pdf"),
        ("new_form", "PERM_New_Form_Record_Layout_FY2024.pdf"),
    }


def test_discovery_fails_closed_when_a_completed_year_lacks_a_final_snapshot() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2025_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2025 Q{quarter}</a>"
                for quarter in range(1, 4)
            ],
            '<a href="/files/LCA_Record_Layout_FY2025_Q3.pdf">LCA Record Layout FY2025 Q3</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2026_Q2.xlsx">LCA Disclosure Data FY2026 Q2</a>',
            '<a href="/files/LCA_Record_Layout_FY2026_Q2.pdf">LCA Record Layout FY2026 Q2</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with (
        OfficialHttpClient(config.official_domains, transport=transport) as client,
        pytest.raises(
            SourceDiscoveryError, match=r"FY2025 standard.*missing reviewed segment Q4-Q4"
        ),
    ):
        discover_dol_artifacts(config, client, from_fiscal_year=2025)


def test_lca_discovery_fails_closed_when_historical_partitions_are_incomplete() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            f'<a href="/files/LCA_Disclosure_Data_FY2025_Q{quarter}.xlsx">'
            f"LCA Disclosure Data FY2025 Q{quarter}</a>"
            for quarter in (1, 2, 4)
        ]
        + [
            '<a href="/files/LCA_Record_Layout_FY2025_Q4.pdf">LCA Record Layout FY2025 Q4</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2026_Q2.xlsx">LCA Disclosure Data FY2026 Q2</a>',
            '<a href="/files/LCA_Record_Layout_FY2026_Q2.pdf">LCA Record Layout FY2026 Q2</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with (
        OfficialHttpClient(config.official_domains, transport=transport) as client,
        pytest.raises(SourceDiscoveryError, match=r"FY2025.*missing reviewed segment Q3-Q3"),
    ):
        discover_dol_artifacts(config, client, from_fiscal_year=2025)


def test_lca_discovery_rejects_completed_q4_only_file_as_full_year_coverage() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            '<a href="/files/LCA_Disclosure_Data_FY2025_Q4.xlsx">LCA Disclosure Data FY2025 Q4</a>',
            '<a href="/files/LCA_Record_Layout_FY2025_Q4.pdf">LCA Record Layout FY2025 Q4</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with (
        OfficialHttpClient(config.official_domains, transport=transport) as client,
        pytest.raises(SourceDiscoveryError, match=r"FY2025.*missing reviewed segment Q1-Q1"),
    ):
        discover_dol_artifacts(config, client, from_fiscal_year=2025)


def test_lca_discovery_selects_reviewed_segments_for_latest_completed_year() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2025_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2025 Q{quarter}</a>"
                for quarter in range(1, 5)
            ],
            '<a href="/files/LCA_Record_Layout_FY2025_Q4.pdf">LCA Record Layout FY2025 Q4</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2025)

    assert [item.fiscal_quarter for item in report.selected] == [1, 2, 3, 4]
    assert [item.coverage_start_quarter for item in report.selected] == [1, 2, 3, 4]
    assert all(item.is_quarter_partition for item in report.selected)
    assert all(not item.is_partial_period for item in report.selected)


def test_lca_fy2023_reviewed_segments_use_cumulative_q2_and_exclude_q1() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2023_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2023 Q{quarter}</a>"
                for quarter in range(1, 5)
            ],
            '<a href="/files/LCA_Record_Layout_FY2023_Q4.pdf">LCA Record Layout FY2023 Q4</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2024_Q1.xlsx">LCA Disclosure Data FY2024 Q1</a>',
            '<a href="/files/LCA_Record_Layout_FY2024_Q1.pdf">LCA Record Layout FY2024 Q1</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2023)

    completed = [item for item in report.selected if item.fiscal_year == 2023]
    assert [(item.coverage_start_quarter, item.fiscal_quarter) for item in completed] == [
        (1, 2),
        (3, 3),
        (4, 4),
    ]
    assert all("Q1" not in item.file_name for item in completed)


def test_lca_completed_year_without_reviewed_segment_contract_fails_closed() -> None:
    config = SourceRegistry.from_yaml().get("dol_lca")
    html = "".join(
        [
            *[
                f'<a href="/files/LCA_Disclosure_Data_FY2026_Q{quarter}.xlsx">'
                f"LCA Disclosure Data FY2026 Q{quarter}</a>"
                for quarter in range(1, 5)
            ],
            '<a href="/files/LCA_Record_Layout_FY2026_Q4.pdf">LCA Record Layout FY2026 Q4</a>',
            '<a href="/files/LCA_Disclosure_Data_FY2027_Q1.xlsx">LCA Disclosure Data FY2027 Q1</a>',
            '<a href="/files/LCA_Record_Layout_FY2027_Q1.pdf">LCA Record Layout FY2027 Q1</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    with (
        OfficialHttpClient(config.official_domains, transport=transport) as client,
        pytest.raises(SourceDiscoveryError, match=r"FY2026.*lack a reviewed.*contract"),
    ):
        discover_dol_artifacts(config, client, from_fiscal_year=2026)


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


def test_ipeds_discovery_selects_latest_finalized_hd_ic_pair_with_dictionaries() -> None:
    config = SourceRegistry.from_yaml().get("ipeds")
    html = "".join(
        [
            "<table><tr><td>Institutional Characteristics (IC)</td>"
            "<td>2025-26</td><td>2008-09 to 2023-24</td></tr></table>",
            '<a href="/ipeds/complete-data-files/HD2023.zip">HD2023</a>',
            '<a href="/ipeds/complete-data-files/HD2023_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/IC2023.zip">IC2023</a>',
            '<a href="/ipeds/complete-data-files/IC2023_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/HD2025.zip">HD2025</a>',
            '<a href="/ipeds/complete-data-files/HD2025_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/IC2025.zip">IC2025</a>',
            '<a href="/ipeds/complete-data-files/IC2025_Dict.zip">Dictionary</a>',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    with OfficialHttpClient(config.official_domains, transport=transport) as client:
        report = discover_ipeds(config, client, from_fiscal_year=2022)

    assert len(report.candidates) == 4
    assert {(item.fiscal_year, item.variant) for item in report.selected} == {
        (2023, "directory_final"),
        (2023, "characteristics_final"),
    }
    assert all(item.record_layout_url is not None for item in report.selected)
    assert report.warnings and "provisional" in report.warnings[0]


def test_ipeds_discovery_loads_official_all_years_view_when_default_is_current_only() -> None:
    config = SourceRegistry.from_yaml().get("ipeds")
    release_table = (
        "<table><tr><td>Institutional Characteristics (IC)</td>"
        "<td>2025-26</td><td>2008-09 to 2023-24</td></tr></table>"
    )
    current_html = "".join(
        [
            release_table,
            '<a href="/ipeds/complete-data-files/HD2025.zip">HD2025</a>',
            '<a href="/ipeds/complete-data-files/HD2025_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/IC2025.zip">IC2025</a>',
            '<a href="/ipeds/complete-data-files/IC2025_Dict.zip">Dictionary</a>',
        ]
    )
    all_years_html = "".join(
        [
            current_html,
            '<a href="/ipeds/complete-data-files/HD2023.zip">HD2023</a>',
            '<a href="/ipeds/complete-data-files/HD2023_Dict.zip">Dictionary</a>',
            '<a href="/ipeds/complete-data-files/IC2023.zip">IC2023</a>',
            '<a href="/ipeds/complete-data-files/IC2023_Dict.zip">Dictionary</a>',
        ]
    )
    requests: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        html = all_years_html if request.url.params.get("year") == "-1" else current_html
        return httpx.Response(200, text=html, request=request)

    with OfficialHttpClient(
        config.official_domains, transport=httpx.MockTransport(handler)
    ) as client:
        report = discover_ipeds(config, client, from_fiscal_year=2022)

    assert len(requests) == 2
    assert requests[1].params.get("surveyNumber") == "-1"
    assert {(item.fiscal_year, item.variant) for item in report.selected} == {
        (2023, "directory_final"),
        (2023, "characteristics_final"),
    }


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
