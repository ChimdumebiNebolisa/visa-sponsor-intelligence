"""Tests for the typed authoritative source registry."""

from sponsor_intel.sources.registry import SourceRegistry


def test_registry_contains_phase_one_and_two_sources() -> None:
    registry = SourceRegistry.from_yaml()

    assert [source.id for source in registry.list()] == [
        "dol_lca",
        "dol_perm",
        "herd",
        "ipeds",
        "uscis_h1b",
    ]
    assert registry.get("dol_lca").official_domains == ("dol.gov",)
    assert registry.get("dol_perm").minimum_fiscal_year == 2022
    assert registry.get("ipeds").expected_formats == ("zip",)
    assert registry.get("uscis_h1b").artifact_url is not None
