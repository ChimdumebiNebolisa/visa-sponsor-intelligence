"""Canonical Product A case-status expressions shared across processing engines."""

from __future__ import annotations

import re

import polars as pl

CASE_STATUS_HYPHEN_PATTERN = r"\s*-\s*"
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def canonical_case_status(column: str | pl.Expr = "case_status") -> pl.Expr:
    """Normalize case and whitespace around a status hyphen without broadening semantics."""

    expression = pl.col(column) if isinstance(column, str) else column
    return (
        expression.cast(pl.String, strict=False)
        .fill_null("")
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace_all(CASE_STATUS_HYPHEN_PATTERN, "-")
    )


def canonical_case_status_sql(column: str = "case_status") -> str:
    """Return the DuckDB equivalent of :func:`canonical_case_status` for a column."""

    if _SQL_IDENTIFIER.fullmatch(column) is None:
        raise ValueError(f"Unsupported SQL column identifier: {column}")
    return (
        "upper(regexp_replace(trim(coalesce("
        f"{column}, '')), '{CASE_STATUS_HYPHEN_PATTERN}', '-', 'g'))"
    )
