"""Live DOL landing-page contract tests (opt in with an environment flag)."""

from __future__ import annotations

import os

import pytest

from sponsor_intel.sources.discovery import discover_dol_artifacts
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.registry import SourceRegistry

pytestmark = pytest.mark.network


@pytest.mark.skipif(
    os.getenv("SPONSOR_INTEL_RUN_NETWORK_TESTS") != "1",
    reason="Set SPONSOR_INTEL_RUN_NETWORK_TESTS=1 to access official sources",
)
@pytest.mark.parametrize("source_id", ["dol_lca", "dol_perm"])
def test_dol_disclosure_and_record_layout_are_discoverable(source_id: str) -> None:
    config = SourceRegistry.from_yaml().get(source_id)
    with OfficialHttpClient(config.official_domains) as client:
        report = discover_dol_artifacts(config, client, from_fiscal_year=2022)

    assert report.selected
    assert min(candidate.fiscal_year for candidate in report.selected) == 2022
    assert all(candidate.record_layout_url for candidate in report.selected)
    assert all(
        candidate.download_url.startswith("https://www.dol.gov/") for candidate in report.selected
    )
