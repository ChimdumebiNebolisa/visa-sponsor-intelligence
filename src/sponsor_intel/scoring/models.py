"""Strict contracts for the versioned scoring configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SCORING_CONFIG_PATH = Path("configs/scoring.yaml")
DEFAULT_SCORING_V2_CONFIG_PATH = Path("configs/scoring_v2.yaml")


class Band(BaseModel):
    """One ordered score or confidence label threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    minimum: float


class StemOptConfig(BaseModel):
    """Current STEM OPT evidence formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    everify_active_score: float = Field(ge=0, le=100)
    everify_inactive_score: float = Field(ge=0, le=100)
    opt_only_score: float = Field(ge=0, le=100)
    opt_bonus: float = Field(ge=0, le=100)
    evidence_weights: dict[str, float]
    status_bands: list[Band]


class H1BHistoryConfig(BaseModel):
    """H-1B history subcomponent formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: dict[str, float]
    relevant_lca_volume_cap: int = Field(gt=0)
    initial_approval_volume_cap: int = Field(gt=0)
    active_year_cap: int = Field(gt=0)
    recency_penalty_per_year: float = Field(gt=0)
    approval_ratio_minimum_denominator: int = Field(gt=0)


class GreenCardHistoryConfig(BaseModel):
    """PERM history subcomponent formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: dict[str, float]
    relevant_perm_volume_cap: int = Field(gt=0)
    active_year_cap: int = Field(gt=0)
    recency_penalty_per_year: float = Field(gt=0)


class WeightedConfig(BaseModel):
    """Simple weighted component formula."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: dict[str, float]


class PolicySupportConfig(BaseModel):
    """Reviewed policy fact weights and explicit value mappings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_weights: dict[str, float]
    value_scores: dict[str, dict[str, float]]


class CompositeConfig(WeightedConfig):
    """Composite formula and missing-component behavior."""

    require_all_components: bool


class CompositeSet(BaseModel):
    """Named product composites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    immigration_evidence: CompositeConfig
    research_pathway: CompositeConfig


class GradeAwareWeightedConfig(WeightedConfig):
    """Weighted formula with an explicit minimum coverage for a grade."""

    grade_minimum_coverage: float = Field(ge=0, le=1)


class GradeAwareH1BHistoryConfig(H1BHistoryConfig):
    """V2 H-1B history formula and grade gate."""

    grade_minimum_coverage: float = Field(ge=0, le=1)


class GradeAwareGreenCardHistoryConfig(GreenCardHistoryConfig):
    """V2 green-card history formula and grade gate."""

    grade_minimum_coverage: float = Field(ge=0, le=1)


class GradeAwarePolicySupportConfig(PolicySupportConfig):
    """V2 reviewed-policy formula and grade gate."""

    grade_minimum_coverage: float = Field(ge=0, le=1)


class GradeAwareCompositeConfig(CompositeConfig):
    """V2 composite formula with explicit grade coverage."""

    grade_minimum_coverage: float = Field(ge=0, le=1)


class CorePolicyConfig(BaseModel):
    """Policy questions required for a decision-ready research profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_fact_types: tuple[str, ...]
    substantive_values: tuple[str, ...]
    accepted_review_status: str
    not_stated_review_status: str

    @model_validator(mode="after")
    def validate_core_profile(self) -> CorePolicyConfig:
        if len(self.required_fact_types) != 4 or len(set(self.required_fact_types)) != 4:
            raise ValueError("core_policy.required_fact_types must contain four unique fields")
        if not self.substantive_values or len(set(self.substantive_values)) != len(
            self.substantive_values
        ):
            raise ValueError("core_policy.substantive_values must be unique and non-empty")
        return self


class ScoringV2Config(BaseModel):
    """Complete reproducible Phase 10 evidence-readiness formula configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    reference_year: int = Field(ge=2022)
    stem_opt_readiness: StemOptConfig
    h1b_history: GradeAwareH1BHistoryConfig
    green_card_history: GradeAwareGreenCardHistoryConfig
    sponsorship_history: GradeAwareCompositeConfig
    research_strength: GradeAwareWeightedConfig
    policy_support: GradeAwarePolicySupportConfig
    research_pathway: GradeAwareCompositeConfig
    core_policy: CorePolicyConfig
    confidence_bands: list[Band]
    grade_bands: list[Band]

    @model_validator(mode="after")
    def validate_formula_weights(self) -> ScoringV2Config:
        weighted = {
            "stem_opt_readiness.evidence_weights": self.stem_opt_readiness.evidence_weights,
            "h1b_history.weights": self.h1b_history.weights,
            "green_card_history.weights": self.green_card_history.weights,
            "sponsorship_history.weights": self.sponsorship_history.weights,
            "research_strength.weights": self.research_strength.weights,
            "policy_support.fact_weights": self.policy_support.fact_weights,
            "research_pathway.weights": self.research_pathway.weights,
        }
        for name, weights in weighted.items():
            if not weights or any(weight <= 0 for weight in weights.values()):
                raise ValueError(f"{name} must contain only positive weights")
            if abs(sum(weights.values()) - 1.0) > 1e-9:
                raise ValueError(f"{name} weights must sum to 1.0")
        if set(self.policy_support.fact_weights) != set(self.policy_support.value_scores):
            raise ValueError("Every scored policy fact needs an explicit value mapping")
        expected_sponsorship = {"h1b_history", "green_card_history"}
        if set(self.sponsorship_history.weights) != expected_sponsorship:
            raise ValueError(
                "sponsorship_history.weights must contain h1b_history and green_card_history"
            )
        expected_pathway = {"sponsorship_history", "policy_support", "research_strength"}
        if set(self.research_pathway.weights) != expected_pathway:
            raise ValueError(
                "research_pathway.weights must contain sponsorship_history, policy_support, "
                "and research_strength"
            )
        for bands_name, bands in (
            ("confidence_bands", self.confidence_bands),
            ("grade_bands", self.grade_bands),
            ("stem_opt_readiness.status_bands", self.stem_opt_readiness.status_bands),
        ):
            if not bands or bands != sorted(bands, key=lambda band: band.minimum, reverse=True):
                raise ValueError(f"{bands_name} must be ordered by descending minimum")
        return self

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_SCORING_V2_CONFIG_PATH) -> ScoringV2Config:
        """Load and validate the V2 formula without reinterpreting V1."""

        if not path.is_file():
            raise ValueError(f"V2 scoring configuration is unavailable: {path}")
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("V2 scoring configuration must be a YAML mapping")
        return cls.model_validate(values)


class ScoringConfig(BaseModel):
    """Complete reproducible Phase 8 formula configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    reference_year: int = Field(ge=2022)
    stem_opt_readiness: StemOptConfig
    h1b_history: H1BHistoryConfig
    green_card_history: GreenCardHistoryConfig
    research_strength: WeightedConfig
    policy_support: PolicySupportConfig
    composites: CompositeSet
    confidence_bands: list[Band]
    grade_bands: list[Band]

    @model_validator(mode="after")
    def validate_formula_weights(self) -> ScoringConfig:
        weighted = {
            "stem_opt_readiness.evidence_weights": self.stem_opt_readiness.evidence_weights,
            "h1b_history.weights": self.h1b_history.weights,
            "green_card_history.weights": self.green_card_history.weights,
            "research_strength.weights": self.research_strength.weights,
            "policy_support.fact_weights": self.policy_support.fact_weights,
            "composites.immigration_evidence.weights": (
                self.composites.immigration_evidence.weights
            ),
            "composites.research_pathway.weights": self.composites.research_pathway.weights,
        }
        for name, weights in weighted.items():
            if not weights or any(weight <= 0 for weight in weights.values()):
                raise ValueError(f"{name} must contain only positive weights")
            if abs(sum(weights.values()) - 1.0) > 1e-9:
                raise ValueError(f"{name} weights must sum to 1.0")
        if set(self.policy_support.fact_weights) != set(self.policy_support.value_scores):
            raise ValueError("Every scored policy fact needs an explicit value mapping")
        for bands_name, bands in (
            ("confidence_bands", self.confidence_bands),
            ("grade_bands", self.grade_bands),
            ("stem_opt_readiness.status_bands", self.stem_opt_readiness.status_bands),
        ):
            if not bands or bands != sorted(bands, key=lambda band: band.minimum, reverse=True):
                raise ValueError(f"{bands_name} must be ordered by descending minimum")
        return self

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_SCORING_CONFIG_PATH) -> ScoringConfig:
        """Load and validate the sole source of scoring formulas."""

        if not path.is_file():
            raise ValueError(f"Scoring configuration is unavailable: {path}")
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Scoring configuration must be a YAML mapping")
        return cls.model_validate(values)
