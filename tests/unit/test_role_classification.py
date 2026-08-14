from pathlib import Path

from sponsor_intel.role_classification.classifier import (
    RoleClassifier,
    normalize_soc_code,
    normalize_title,
)
from sponsor_intel.role_classification.models import RoleTaxonomyConfig
from sponsor_intel.role_classification.validation import validate_role_gold


def _classifier() -> RoleClassifier:
    return RoleClassifier(RoleTaxonomyConfig.from_yaml())


def test_title_and_soc_normalization() -> None:
    assert normalize_title("  DévOps / SRE Engineer ") == "DEVOPS SRE ENGINEER"
    assert normalize_soc_code("SOC 15-1252.00 Software Developers") == "15-1252.00"
    assert normalize_soc_code(None) == ""


def test_reviewed_override_precedes_soc_mapping() -> None:
    result = _classifier().classify("Manager JC50", "15-1252.00")

    assert result.technical_role is None
    assert result.role_family == "ambiguous"
    assert result.classification_method == "EXACT_REVIEWED_TITLE"
    assert result.review_status == "NEEDS_REVIEW"


def test_soc_mapping_precedes_generic_title_rules() -> None:
    result = _classifier().classify("Associate", "15-1252.00")

    assert result.technical_role is True
    assert result.role_family == "software_engineering"
    assert result.classification_method == "SOC_MAPPING"


def test_strong_title_exclusion_and_combined_rules() -> None:
    classifier = _classifier()

    data = classifier.classify("Data Engineer", None)
    intern = classifier.classify("Software Engineering Intern", None)
    combined = classifier.classify("Research Scientist Computational", "17-2071.00")

    assert data.role_family == "data_engineering"
    assert intern.technical_role is False
    assert intern.classification_method == "STRONG_EXCLUSION_PATTERN"
    assert combined.technical_role is True
    assert combined.role_family == "research_engineering"
    assert combined.classification_method == "COMBINED_SOC_TITLE"


def test_generic_research_engineering_and_medical_cases_are_controlled() -> None:
    classifier = _classifier()

    research = classifier.classify("Research Scientist", None)
    engineer = classifier.classify("Engineer", None)
    physician = classifier.classify("Resident Physician", "29-1229.00")

    assert research.technical_role is None
    assert engineer.technical_role is None
    assert physician.technical_role is False
    assert research.review_status == engineer.review_status == "NEEDS_REVIEW"


def test_missing_evidence_is_reviewed() -> None:
    result = _classifier().classify(None, None)

    assert result.technical_role is None
    assert result.role_confidence == 0
    assert result.review_status == "NEEDS_REVIEW"


def test_role_gold_meets_all_acceptance_targets() -> None:
    result = validate_role_gold(
        Path("tests/fixtures/role_classification_gold.csv"),
        RoleTaxonomyConfig.from_yaml(),
    )

    assert result.row_count == 750
    assert result.precision >= 0.95
    assert result.recall >= 0.90
    assert result.family_accuracy >= 0.90
    assert result.low_confidence_routed_rate == 1.0
    assert result.passed
