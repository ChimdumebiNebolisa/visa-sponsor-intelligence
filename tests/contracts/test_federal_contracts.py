"""Live Phase 2 federal-source contracts (opt in with an environment flag)."""

from __future__ import annotations

import os

import pytest

from sponsor_intel.sources.federal_discovery import (
    discover_herd,
    discover_ipeds,
    discover_uscis_h1b,
)
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.registry import SourceRegistry

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("SPONSOR_INTEL_RUN_NETWORK_TESTS") != "1",
        reason="Set SPONSOR_INTEL_RUN_NETWORK_TESTS=1 to access official sources",
    ),
]


def test_current_finalized_ipeds_hd_ic_pair_and_dictionaries_are_discoverable() -> None:
    config = SourceRegistry.from_yaml().get("ipeds")
    with OfficialHttpClient(config.official_domains) as client:
        report = discover_ipeds(config, client, from_fiscal_year=2022)

    assert len(report.selected) == 2
    assert {candidate.variant for candidate in report.selected} == {
        "directory_final",
        "characteristics_final",
    }
    assert len({candidate.fiscal_year for candidate in report.selected}) == 1
    assert all(candidate.record_layout_url is not None for candidate in report.selected)


def test_herd_standard_and_short_archives_are_discoverable() -> None:
    config = SourceRegistry.from_yaml().get("herd")
    with OfficialHttpClient(config.official_domains) as client:
        report = discover_herd(config, client, from_fiscal_year=2022)

    pairs = {(candidate.fiscal_year, candidate.variant) for candidate in report.selected}
    assert {
        (year, variant) for year in range(2022, 2025) for variant in ("standard", "short")
    } <= pairs


def test_uscis_full_data_sheet_returns_expected_csv_contract() -> None:
    config = SourceRegistry.from_yaml().get("uscis_h1b")
    with OfficialHttpClient(config.official_domains) as client:
        report = discover_uscis_h1b(config, client, from_fiscal_year=2026)
        with client.stream(report.selected[0].download_url) as response:
            prefix = next(response.iter_bytes(chunk_size=4096)).decode("utf-8")

    assert "Employer (Petitioner) Name" in prefix
    assert "Measure Names" in prefix
    assert ",2026," in prefix
