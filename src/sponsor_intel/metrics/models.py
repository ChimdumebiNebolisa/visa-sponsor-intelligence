"""Typed results for the Phase 5 metrics build."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MetricsBuildSummary(BaseModel):
    """Persisted processed-layer counts and coverage metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    employer_count: int = Field(ge=0)
    institution_count: int = Field(ge=0)
    lca_case_count: int = Field(ge=0)
    perm_case_count: int = Field(ge=0)
    h1b_petition_row_count: int = Field(ge=0)
    latest_complete_fiscal_year: int | None
    current_partial_fiscal_year: int | None
    current_partial_quarter: int | None
    metric_version: str
    employer_metrics_path: Path
    institution_metrics_path: Path
    data_health_path: Path
    summary_path: Path
