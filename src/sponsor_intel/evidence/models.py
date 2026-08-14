"""Typed Phase 6 build summaries."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class OptBuildSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_year: int
    employer_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    linked_employer_count: int = Field(ge=0)
    review_employer_count: int = Field(ge=0)
    observations_path: Path
    review_path: Path


class EVerifyBuildSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority_count: int = Field(ge=0)
    attempted_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)
    confirmed_active_count: int = Field(ge=0)
    confirmed_inactive_count: int = Field(ge=0)
    no_match_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    priorities_path: Path
    observations_path: Path
    review_path: Path


class Phase6BuildSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opt: OptBuildSummary
    everify: EVerifyBuildSummary
    database_path: Path
    summary_path: Path
