"""Typed entity-resolution configuration and result models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MatchStatus(StrEnum):
    """Required entity-resolution match status."""

    DETERMINISTIC = "DETERMINISTIC"
    HIGH_CONFIDENCE_AUTO = "HIGH_CONFIDENCE_AUTO"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class EntityResolutionConfig(BaseModel):
    """Validated thresholds and name-normalization rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    normalization_version: str
    high_confidence_threshold: float = Field(ge=0, le=1)
    review_threshold: float = Field(ge=0, le=1)
    minimum_margin: float = Field(ge=0, le=1)
    candidate_limit: int = Field(ge=1, le=100)
    location_agreement_required_for_fuzzy_auto: bool
    legal_suffixes: tuple[str, ...]
    abbreviations: dict[str, str]

    @model_validator(mode="after")
    def validate_threshold_order(self) -> EntityResolutionConfig:
        if self.review_threshold >= self.high_confidence_threshold:
            raise ValueError("review_threshold must be below high_confidence_threshold")
        return self

    @classmethod
    def from_yaml(
        cls, path: Path = Path("configs/entity_resolution.yaml")
    ) -> EntityResolutionConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class ParentOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_organization_id: str
    canonical_name: str
    organization_type: str
    headquarters_state: str | None = None
    is_staffing_or_consulting: bool
    reviewed_by: str
    reviewed_at: str
    notes: str
    evidence_url: str | None = None
    evidence_source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    relationship_type: Literal["PARENT_ROLLUP"] = "PARENT_ROLLUP"


class LegalEntityOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    legal_entity_id: str
    legal_name: str
    parent_organization_id: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    organization_type: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    evidence_url: str | None = None
    evidence_source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    relationship_type: Literal["DIRECT_LEGAL_ENTITY"] = "DIRECT_LEGAL_ENTITY"


class AliasOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_name: str
    legal_entity_id: str
    source_id: str | None = None
    reviewed_by: str
    reviewed_at: str
    reason: str
    employer_city: str | None = None
    employer_state: str | None = None
    employer_postal_code: str | None = None
    evidence_url: str | None = None
    evidence_source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    relationship_type: Literal["DIRECT_LEGAL_ENTITY"] = "DIRECT_LEGAL_ENTITY"


class RejectionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_name: str
    candidate_legal_entity_id: str
    source_id: str | None = None
    reviewed_by: str
    reviewed_at: str
    reason: str


class EntityOverrides(BaseModel):
    """Auditable committed mapping decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    parent_organizations: tuple[ParentOverride, ...] = ()
    legal_entities: tuple[LegalEntityOverride, ...] = ()
    aliases: tuple[AliasOverride, ...] = ()
    rejections: tuple[RejectionOverride, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> EntityOverrides:
        parent_ids = [item.parent_organization_id for item in self.parent_organizations]
        legal_ids = [item.legal_entity_id for item in self.legal_entities]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("Duplicate parent_organization_id in overrides")
        if len(legal_ids) != len(set(legal_ids)):
            raise ValueError("Duplicate legal_entity_id in overrides")
        missing_parents = {
            item.parent_organization_id
            for item in self.legal_entities
            if item.parent_organization_id and item.parent_organization_id not in set(parent_ids)
        }
        if missing_parents:
            raise ValueError(f"Unknown parent override references: {sorted(missing_parents)}")
        missing_legal = {
            item.legal_entity_id
            for item in self.aliases
            if item.legal_entity_id not in set(legal_ids)
        }
        if missing_legal:
            raise ValueError(f"Unknown legal-entity alias references: {sorted(missing_legal)}")
        return self

    @classmethod
    def from_yaml(cls, path: Path = Path("configs/entity_overrides.yaml")) -> EntityOverrides:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class EntityResolutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_count: int = Field(ge=0)
    legal_entity_count: int = Field(ge=0)
    parent_organization_count: int = Field(ge=0)
    resolved_record_count: int = Field(ge=0)
    status_counts: dict[str, int]
    review_queue_count: int = Field(ge=0)
    legal_entities_path: Path
    parent_organizations_path: Path
    aliases_path: Path
    review_queue_path: Path
    summary_path: Path


class GoldValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_count: int
    category_counts: dict[str, int]
    auto_accepted_count: int
    auto_precision: float
    false_auto_accept_count: int
    parent_legal_collapse_count: int
    ambiguous_routed_count: int
    ambiguous_total_count: int
    passed: bool
