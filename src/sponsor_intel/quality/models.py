"""Typed outputs for Phase 9 data-quality gates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QualityStatus = Literal["PASS", "WARN", "FAIL"]


class QualityCheck(BaseModel):
    """One visible, publication-aware quality assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str
    category: str
    status: QualityStatus
    critical: bool
    value: float | None
    threshold: str
    details: str


class QualityReport(BaseModel):
    """Complete quality result used by the UI and release workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_id: str
    generated_at: datetime
    passed: bool
    critical_failure_count: int = Field(ge=0)
    manifest_sha256: str
    metric_version: str | None
    score_version: str | None
    checks: tuple[QualityCheck, ...]
    checks_path: Path
    report_path: Path
    metadata_path: Path
