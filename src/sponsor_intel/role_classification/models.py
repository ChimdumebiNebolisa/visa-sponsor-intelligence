"""Typed role-taxonomy configuration and build results."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExactTitleOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    technical_role: bool | None
    role_family: str
    confidence: float = Field(ge=0, le=1)
    reviewed_by: str
    reviewed_at: str


class SocMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prefixes: tuple[str, ...]
    role_family: str
    confidence: float = Field(ge=0, le=1)


class TitleRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    patterns: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    role_family: str | None = None
    exclude_patterns: tuple[str, ...] = ()


class CombinedRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    patterns: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    exclude_patterns: tuple[str, ...] = ()
    soc_prefixes: tuple[str, ...]
    role_family: str


class RoleTaxonomyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    classification_version: str
    review_confidence_threshold: float = Field(ge=0, le=1)
    role_families: tuple[str, ...]
    exact_title_overrides: tuple[ExactTitleOverride, ...]
    soc_mappings: tuple[SocMapping, ...]
    positive_title_rules: tuple[TitleRule, ...]
    exclusion_rules: tuple[TitleRule, ...]
    combined_rules: tuple[CombinedRule, ...]
    ambiguous_title_patterns: tuple[str, ...]

    @model_validator(mode="after")
    def validate_families(self) -> RoleTaxonomyConfig:
        allowed = set(self.role_families)
        referenced = {item.role_family for item in self.exact_title_overrides} | {
            item.role_family for item in self.soc_mappings
        }
        referenced.update(
            item.role_family
            for item in (*self.positive_title_rules, *self.combined_rules)
            if item.role_family is not None
        )
        missing = referenced - allowed
        if missing:
            raise ValueError(f"Unknown role families: {sorted(missing)}")
        if not {"not_relevant", "ambiguous"}.issubset(allowed):
            raise ValueError("Taxonomy must include not_relevant and ambiguous")
        return self

    @classmethod
    def from_yaml(cls, path: Path = Path("configs/role_taxonomy.yaml")) -> RoleTaxonomyConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class RoleClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    technical_role: bool | None
    role_family: str
    role_confidence: float = Field(ge=0, le=1)
    classification_method: str
    classification_rule: str
    classification_version: str
    review_status: str


class RoleClassificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_count: int = Field(ge=0)
    unique_classification_count: int = Field(ge=0)
    technical_record_count: int = Field(ge=0)
    ambiguous_record_count: int = Field(ge=0)
    review_queue_count: int = Field(ge=0)
    family_counts: dict[str, int]
    method_counts: dict[str, int]
    classifications_path: Path
    review_queue_path: Path
    summary_path: Path


class RoleValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_count: int
    source_year_count: int
    employer_type_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    precision: float
    recall: float
    family_accuracy: float
    low_confidence_routed_rate: float
    passed: bool
