"""Tests for the typed authoritative source registry."""

from sponsor_intel.sources.registry import SourceRegistry


def test_registry_contains_phase_one_sources() -> None:
    registry = SourceRegistry.from_yaml()

    assert [source.id for source in registry.list()] == ["dol_lca", "dol_perm"]
    assert registry.get("dol_lca").official_domains == ("dol.gov",)
    assert registry.get("dol_perm").minimum_fiscal_year == 2022
