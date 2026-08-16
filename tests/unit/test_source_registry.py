"""Tests for the typed authoritative source registry."""

from sponsor_intel.sources.registry import SourceRegistry


def test_registry_contains_phase_one_and_two_sources() -> None:
    registry = SourceRegistry.from_yaml()

    assert [source.id for source in registry.list()] == [
        "dol_lca",
        "dol_perm",
        "herd",
        "ipeds",
        "sevp_opt",
        "uscis_h1b",
    ]
    assert registry.get("dol_lca").official_domains == ("dol.gov",)
    assert registry.get("dol_perm").minimum_fiscal_year == 2022
    assert registry.get("ipeds").expected_formats == ("zip",)
    assert registry.get("uscis_h1b").artifact_url is not None


def test_dol_registry_exposes_product_a_canonical_fields() -> None:
    registry = SourceRegistry.from_yaml()
    lca = registry.get("dol_lca")
    perm = registry.get("dol_perm")

    assert {
        "employer_address_1",
        "employer_address_2",
        "employer_city",
        "employer_state",
        "employer_postal_code",
        "naics_code",
        "worker_positions",
        "prevailing_wage",
        "prevailing_wage_unit",
        "worksite_city",
    }.issubset(lca.optional_columns)
    assert {
        "employer_address_1",
        "employer_address_2",
        "employer_city",
        "employer_state",
        "employer_postal_code",
        "naics_code",
        "wage_from",
        "wage_to",
        "wage_unit",
        "prevailing_wage",
        "prevailing_wage_unit",
        "worksite_city",
        "minimum_education",
        "major_field",
        "experience_required",
        "experience_months",
    }.issubset(perm.optional_columns)
    assert "TOTAL_WORKER_POSITIONS" in lca.optional_columns["worker_positions"]
    assert "JOB_OPP_WAGE_FROM" in perm.optional_columns["wage_from"]
    assert "REQUIRED_EXPERIENCE" in perm.optional_columns["experience_required"]
