"""Typed results for DuckDB builds."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DatabaseBuildSummary(BaseModel):
    """Stable metadata returned after building the presentation database."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    database_path: Path
    employer_count: int = Field(ge=0)
    institution_count: int = Field(ge=0)
    view_names: tuple[str, ...]
