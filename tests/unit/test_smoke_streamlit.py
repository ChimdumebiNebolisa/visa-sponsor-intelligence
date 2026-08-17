"""Contracts for Streamlit fixture and real-database smoke validation."""

import importlib.util
import sys
from pathlib import Path

import duckdb
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke_streamlit.py"
SPEC = importlib.util.spec_from_file_location("smoke_streamlit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT_PATH.parent))
try:
    SPEC.loader.exec_module(MODULE)
finally:
    sys.path.pop(0)
_verify_database = MODULE._verify_database


def _database_with_counts(path: Path, *, employers: int, institutions: int) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE employer_metrics AS
            SELECT range AS id, 'product_a_scores_v1' AS score_version FROM range(?)
            """,
            [employers],
        )
        connection.execute(
            "CREATE TABLE institution_metrics AS SELECT range AS id FROM range(?)", [institutions]
        )


def test_real_database_smoke_requires_nonzero_employers_and_institutions(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.duckdb"
    _database_with_counts(database_path, employers=1, institutions=0)

    with pytest.raises(RuntimeError, match="real database must contain employers and institutions"):
        _verify_database(database_path, real_database=True)


def test_real_database_smoke_returns_verified_counts(tmp_path: Path) -> None:
    database_path = tmp_path / "real.duckdb"
    _database_with_counts(database_path, employers=7, institutions=3)

    assert _verify_database(database_path, real_database=True) == (7, 3)


def test_real_database_smoke_rejects_stale_non_product_a_scores(tmp_path: Path) -> None:
    database_path = tmp_path / "stale.duckdb"
    _database_with_counts(database_path, employers=2, institutions=1)
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("UPDATE employer_metrics SET score_version = 'legacy_scores'")

    with pytest.raises(RuntimeError, match="only Product A rating rows"):
        _verify_database(database_path, real_database=True)
