"""Live Phase 6 ICE report contract (opt in with an environment flag)."""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl
import pytest

from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import SourceRegistry

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("SPONSOR_INTEL_RUN_NETWORK_TESTS") != "1",
        reason="Set SPONSOR_INTEL_RUN_NETWORK_TESTS=1 to access official sources",
    ),
]


def test_current_ice_opt_report_is_a_positive_200_employer_contract(
    tmp_path: Path,
) -> None:
    summary = IngestionPipeline(
        SourceRegistry.from_yaml(),
        data_root=tmp_path / "data",
        output_root=tmp_path / "outputs",
    ).ingest("sevp_opt", from_fiscal_year=2022)
    frame = pl.read_parquet(summary.records[0].parquet_path)
    totals = frame.filter(pl.col("program_type") == "OPT_OR_STEM_OPT")

    assert summary.records[0].download_url.startswith("https://www.ice.gov/")
    assert totals.height == 200
    assert totals["rank"].n_unique() == 200
    assert frame.filter(pl.col("reported_count") <= 0).is_empty()
    assert frame["is_positive"].all()
