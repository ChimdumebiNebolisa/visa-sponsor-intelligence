from pathlib import Path

import polars as pl
import pytest

from sponsor_intel.entity_resolution.models import (
    EntityOverrides,
    EntityResolutionConfig,
    MatchStatus,
)
from sponsor_intel.entity_resolution.normalization import (
    core_name,
    name_acronym,
    normalize_name,
    normalize_state,
)
from sponsor_intel.entity_resolution.resolver import resolve_observations
from sponsor_intel.entity_resolution.validation import validate_gold_dataset


def _config() -> EntityResolutionConfig:
    return EntityResolutionConfig.from_yaml()


def _observations(
    *names: str,
    city: str = "AUSTIN",
    state: str = "TX",
    postal_code: str = "78701",
) -> pl.DataFrame:
    config = _config()
    rows = []
    for index, name in enumerate(names):
        normalized = normalize_name(name, config)
        rows.append(
            {
                "observation_id": f"observation-{index}",
                "alias_raw": name,
                "normalized_name": normalized,
                "core_name": core_name(normalized, config),
                "acronym": name_acronym(normalized),
                "source_id": "dol_lca",
                "city": city,
                "state": state,
                "postal_code": postal_code,
                "occurrence_count": 10 - index,
            }
        )
    return pl.DataFrame(rows)


def _ipeds() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "unitid": ["228778"],
            "instnm": ["University of Texas at Austin"],
            "city": ["Austin"],
            "stabbr": ["TX"],
            "zip": ["78712"],
            "f1sysnam": ["The University of Texas System"],
            "f1syscod": ["128010"],
        }
    )


def test_normalization_preserves_raw_and_normalizes_common_variants() -> None:
    config = _config()
    raw = "  Acmé Tech., Inc.  "

    normalized = normalize_name(raw, config)

    assert raw == "  Acmé Tech., Inc.  "
    assert normalized == "ACME TECHNOLOGY INC"
    assert core_name(normalized, config) == "ACME TECHNOLOGY"
    assert normalize_state("Washington") == "WA"
    assert normalize_state("District of Columbia") == "DC"
    assert normalize_state("MASSACHUSETTS MASSACHUSETTS") == "MA"
    assert normalize_state("TENNESSEE TENNESSEE(TN)") == "TN"
    assert normalize_state("NORTH CAROLINA WATAUGA COUNTY") == "NC"
    assert normalize_state("not a legal-employer state") == ""


def test_exact_name_is_deterministic() -> None:
    result = resolve_observations(
        _observations("University of Texas at Austin"),
        _ipeds(),
        _config(),
        EntityOverrides(schema_version="test"),
    )

    alias = result.aliases.row(0, named=True)
    assert alias["legal_entity_id"] == "legal_ipeds_228778"
    assert alias["match_status"] == MatchStatus.DETERMINISTIC


def test_exact_name_with_conflicting_legal_employer_state_routes_to_review() -> None:
    overrides = EntityOverrides.model_validate(
        {
            "schema_version": "test",
            "legal_entities": [
                {
                    "legal_entity_id": "legal_acme_labs",
                    "legal_name": "Acme Labs LLC",
                    "city": "Austin",
                    "state": "TX",
                    "organization_type": "RESEARCH",
                }
            ],
        }
    )

    same_location = resolve_observations(
        _observations("ACME LABS, LLC", city="AUSTIN", state="TX"),
        pl.DataFrame(),
        _config(),
        overrides,
    ).aliases.row(0, named=True)
    conflicting_location = resolve_observations(
        _observations("ACME LABS, LLC", city="BOSTON", state="MA"),
        pl.DataFrame(),
        _config(),
        overrides,
    )
    conflict_alias = conflicting_location.aliases.row(0, named=True)

    assert same_location["match_status"] == MatchStatus.DETERMINISTIC
    assert same_location["legal_entity_id"] == "legal_acme_labs"
    assert conflict_alias["match_status"] == MatchStatus.REVIEW_REQUIRED
    assert conflict_alias["match_method"] == "EXACT_NAME_LOCATION_CONFLICT_UNMERGED"
    assert conflict_alias["candidate_legal_entity_id"] == "legal_acme_labs"
    assert conflict_alias["legal_entity_id"] != "legal_acme_labs"
    assert conflict_alias["location_agreement"] is False
    assert conflicting_location.review_queue.height == 1


def test_fuzzy_auto_requires_location_margin_and_no_suffix_conflict() -> None:
    overrides = EntityOverrides.from_yaml()
    result = resolve_observations(
        _observations("Amazon.com Services", city="SEATTLE", state="WA", postal_code="98101"),
        pl.DataFrame(),
        _config(),
        overrides,
    )

    alias = result.aliases.row(0, named=True)
    assert alias["match_status"] == MatchStatus.HIGH_CONFIDENCE_AUTO
    assert alias["legal_entity_id"] == "legal_amazon_com_services"

    conflict = resolve_observations(
        _observations(
            "Amazon.com Services Inc.",
            city="SEATTLE",
            state="WA",
            postal_code="98101",
        ),
        pl.DataFrame(),
        _config(),
        overrides,
    ).aliases.row(0, named=True)
    assert conflict["match_status"] == MatchStatus.REVIEW_REQUIRED
    assert conflict["legal_entity_id"] != "legal_amazon_com_services"


def test_review_candidate_remains_a_separate_legal_entity() -> None:
    config = _config()
    override = EntityOverrides.model_validate(
        {
            "schema_version": "test",
            "legal_entities": [
                {
                    "legal_entity_id": "legal_acme",
                    "legal_name": "Acme Analytics, Inc.",
                    "city": "Austin",
                    "state": "TX",
                    "organization_type": "TECHNOLOGY",
                }
            ],
        }
    )
    result = resolve_observations(
        _observations("Acme Analytics Partners"),
        pl.DataFrame(),
        config,
        override,
    )

    alias = result.aliases.row(0, named=True)
    assert alias["match_status"] == MatchStatus.REVIEW_REQUIRED
    assert alias["candidate_legal_entity_id"] == "legal_acme"
    assert alias["legal_entity_id"] != "legal_acme"
    assert result.review_queue.height == 1


def test_ipeds_parent_and_campus_legal_identity_never_collapse() -> None:
    result = resolve_observations(
        _observations("University of Texas at Austin"),
        _ipeds(),
        _config(),
        EntityOverrides(schema_version="test"),
    )
    legal = result.legal_entities.filter(pl.col("legal_entity_id") == "legal_ipeds_228778").row(
        0, named=True
    )

    assert legal["parent_organization_id"] is not None
    assert legal["parent_organization_id"] != legal["legal_entity_id"]
    assert (
        result.parent_organizations.filter(
            pl.col("parent_organization_id") == legal["parent_organization_id"]
        ).height
        == 1
    )


@pytest.mark.parametrize(
    "observed_name",
    [
        "The University of Texas System",
        "University of Texas at Austin Hospital",
        "University of Texas at Austin Laboratory",
    ],
)
def test_university_system_hospital_and_laboratory_scopes_remain_separate(
    observed_name: str,
) -> None:
    result = resolve_observations(
        _observations(observed_name),
        _ipeds(),
        _config(),
        EntityOverrides(schema_version="test"),
    )
    alias = result.aliases.row(0, named=True)

    assert alias["match_status"] == MatchStatus.REVIEW_REQUIRED
    assert alias["candidate_legal_entity_id"] == "legal_ipeds_228778"
    assert alias["legal_entity_id"] != "legal_ipeds_228778"


def test_parent_company_and_petitioning_subsidiaries_remain_separate() -> None:
    result = resolve_observations(
        _observations("AMAZON COM SERVICES LLC", "AMAZON WEB SERVICES INC"),
        pl.DataFrame(),
        _config(),
        EntityOverrides.from_yaml(),
    )
    aliases = {row["alias_raw"]: row for row in result.aliases.iter_rows(named=True)}

    services = aliases["AMAZON COM SERVICES LLC"]
    web_services = aliases["AMAZON WEB SERVICES INC"]
    assert services["match_status"] == web_services["match_status"] == MatchStatus.MANUAL_OVERRIDE
    assert services["legal_entity_id"] == "legal_amazon_com_services"
    assert web_services["legal_entity_id"] == "legal_amazon_web_services"
    assert services["legal_entity_id"] != web_services["legal_entity_id"]
    assert services["parent_organization_id"] == web_services["parent_organization_id"]


def test_reviewed_major_employer_mappings_preserve_legal_scope_and_evidence() -> None:
    config = _config()
    observations = pl.concat(
        [
            _observations("Microsoft Corporation", city="REDMOND", state="WA", postal_code="98052"),
            _observations(
                "Google LLC", city="MOUNTAIN VIEW", state="CA", postal_code="94043"
            ).with_columns(pl.lit("observation-google").alias("observation_id")),
            _observations(
                "IBM Corporation", city="DURHAM", state="NC", postal_code="27709"
            ).with_columns(
                pl.lit("dol_perm").alias("source_id"),
                pl.lit("observation-ibm").alias("observation_id"),
            ),
            _observations(
                "IBM Corporation", city="ARODA", state="VA", postal_code="22709"
            ).with_columns(
                pl.lit("uscis_h1b").alias("source_id"),
                pl.lit("observation-ibm-ambiguous").alias("observation_id"),
            ),
            _observations(
                "Amazon.com Services LLC", city="SEATTLE", state="WA", postal_code="98121"
            ).with_columns(pl.lit("observation-amazon").alias("observation_id")),
            _observations(
                "Meta Platforms, Inc.", city="MENLO PARK", state="CA", postal_code="94025"
            ).with_columns(pl.lit("observation-meta").alias("observation_id")),
        ],
        how="vertical_relaxed",
    )

    result = resolve_observations(
        observations,
        pl.DataFrame(),
        config,
        EntityOverrides.from_yaml(),
    )
    aliases = {row["observation_id"]: row for row in result.aliases.iter_rows(named=True)}

    assert aliases["observation-0"]["legal_entity_id"] == "legal_microsoft_corporation"
    assert aliases["observation-google"]["legal_entity_id"] == "legal_google_llc"
    assert aliases["observation-ibm"]["legal_entity_id"] == "legal_ibm_corporation"
    assert aliases["observation-amazon"]["parent_organization_id"] == "parent_amazon"
    assert aliases["observation-meta"]["parent_organization_id"] == "parent_meta"
    assert aliases["observation-ibm-ambiguous"]["legal_entity_id"] != "legal_ibm_corporation"
    assert aliases["observation-ibm-ambiguous"]["candidate_legal_entity_id"] == (
        "legal_ibm_corporation"
    )
    assert aliases["observation-ibm"]["evidence_url"].startswith("https://www.ibm.com/")
    assert aliases["observation-ibm"]["decision_confidence"] == 0.99

    reviewed_children = result.legal_entities.filter(
        pl.col("parent_organization_id").is_in(
            ["parent_microsoft", "parent_alphabet", "parent_amazon", "parent_meta", "parent_ibm"]
        )
    )
    assert reviewed_children.filter(pl.col("created_by") != "MANUAL_OVERRIDE").is_empty()
    assert reviewed_children["evidence_url"].drop_nulls().len() == reviewed_children.height


def test_reviewed_alias_and_rejection_regressions() -> None:
    overrides = EntityOverrides.from_yaml()
    alias = resolve_observations(
        _observations("AMAZON COM SERVICES LLC"),
        pl.DataFrame(),
        _config(),
        overrides,
    ).aliases.row(0, named=True)
    rejection = resolve_observations(
        _observations("UT SYSTEM"),
        _ipeds(),
        _config(),
        overrides,
    ).aliases.row(0, named=True)

    assert alias["match_status"] == MatchStatus.MANUAL_OVERRIDE
    assert alias["legal_entity_id"] == "legal_amazon_com_services"
    assert rejection["legal_entity_id"] != "legal_ipeds_228778"


def test_gold_set_meets_precision_and_separation_acceptance() -> None:
    result = validate_gold_dataset(Path("tests/fixtures/entity_resolution_gold.csv"), _config())

    assert result.row_count >= 200
    assert result.auto_precision >= 0.99
    assert result.false_auto_accept_count == 0
    assert result.parent_legal_collapse_count == 0
    assert result.ambiguous_routed_count == result.ambiguous_total_count
    assert result.passed
