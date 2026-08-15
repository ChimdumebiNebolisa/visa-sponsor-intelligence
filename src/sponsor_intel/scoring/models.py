"""Strict contracts for the versioned scoring configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SCORING_CONFIG_PATH = Path("configs/scoring.yaml")


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
