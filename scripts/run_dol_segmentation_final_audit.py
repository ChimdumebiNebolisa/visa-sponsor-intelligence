"""Independently audit the final Product A DOL LCA segmentation and supersessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from run_product_a_acceptance import (
    REVIEWED_LCA_COMPLETED_SEGMENTS,
    _lca_global_supersession_audit,
)

EXPECTED_SUPERSESSIONS = 71_151


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _fiscal_quarter(value: Any) -> int | None:
    if value is None:
        return None
    month = value.month
    return 1 if month >= 10 else 2 if month <= 3 else 3 if month <= 6 else 4


def _artifact_path(manifest: dict[str, Any], repository_root: Path) -> Path:
    path = Path(str(manifest["parquet_path"]))
    return path if path.is_absolute() else repository_root / path


def _audit_once(
    *,
    repository_root: Path,
    data_root: Path,
    database_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    artifacts = pl.read_parquet(data_root / "processed" / "source_artifacts.parquet").filter(
        pl.col("source_id") == "dol_lca"
    )
    selected = artifacts.sort(
        ["fiscal_year", "fiscal_quarter", "source_artifact_id"], nulls_last=True
    ).to_dicts()
    manifest_rows = _manifest_rows(manifest_path)
    manifest_by_id = {
        str(row["source_artifact_id"]): row
        for row in manifest_rows
        if row.get("source_artifact_id")
    }

    artifact_details: list[dict[str, Any]] = []
    frames: list[pl.DataFrame] = []
    errors: list[str] = []
    for row in selected:
        artifact_id = str(row["source_artifact_id"])
        manifest = manifest_by_id.get(artifact_id)
        if manifest is None:
            errors.append(f"{artifact_id}: missing manifest record")
            continue
        path = _artifact_path(manifest, repository_root)
        if not path.is_file():
            errors.append(f"{artifact_id}: missing immutable normalized staging artifact")
            continue
        frame = pl.read_parquet(
            path,
            columns=["source_row_number", "case_id", "decision_date", "case_status"],
        ).with_columns(
            pl.lit(artifact_id).alias("source_artifact_id"),
            pl.lit(int(row["fiscal_year"])).alias("fiscal_year"),
        )
        frames.append(frame)
        observed_quarters = sorted(
            {
                quarter
                for quarter in frame["decision_date"].map_elements(
                    _fiscal_quarter, return_dtype=pl.Int64
                )
                if quarter is not None
            }
        )
        start = int(row.get("coverage_start_quarter") or 1)
        end = int(row.get("fiscal_quarter") or 4)
        expected_quarters = list(range(start, end + 1))
        if observed_quarters != expected_quarters:
            errors.append(
                f"{artifact_id}: observed fiscal quarters {observed_quarters}; "
                f"expected {expected_quarters}"
            )
        artifact_details.append(
            {
                "source_artifact_id": artifact_id,
                "fiscal_year": int(row["fiscal_year"]),
                "fiscal_quarter": row.get("fiscal_quarter"),
                "coverage_start_quarter": row.get("coverage_start_quarter"),
                "is_partial_period": bool(row.get("is_partial_period")),
                "is_quarter_partition": bool(row.get("is_quarter_partition")),
                "row_count": frame.height,
                "decision_date_min": str(frame["decision_date"].min()),
                "decision_date_max": str(frame["decision_date"].max()),
                "observed_fiscal_quarters": observed_quarters,
            }
        )

    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in artifact_details:
        by_year.setdefault(int(row["fiscal_year"]), []).append(row)
    for year, expected in REVIEWED_LCA_COMPLETED_SEGMENTS.items():
        observed = {
            (row["coverage_start_quarter"], row["fiscal_quarter"]) for row in by_year.get(year, [])
        }
        if observed != expected or any(row["is_partial_period"] for row in by_year.get(year, [])):
            errors.append(
                f"FY{year}: observed completed segments {sorted(observed, key=str)}; "
                f"expected {sorted(expected, key=str)}"
            )
    partial = by_year.get(2026, [])
    if (
        len(partial) != 1
        or not partial[0]["is_partial_period"]
        or (partial[0]["coverage_start_quarter"], partial[0]["fiscal_quarter"]) != (1, 3)
    ):
        errors.append("FY2026: expected one cumulative Q1-Q3 partial snapshot")

    supersession = _lca_global_supersession_audit(
        selected,
        manifest_by_id,
        repository_root=repository_root,
    )
    errors.extend(str(value) for value in supersession["failures"])
    ledger_path = repository_root / "outputs/reports/entities/lca_superseded_source_rows.parquet"
    if not ledger_path.is_file():
        errors.append("Supersession ledger is missing")
        ledger = pl.DataFrame()
    else:
        ledger = pl.read_parquet(ledger_path)

    staging = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
    selected_row_count = staging.height
    manifest_row_count = sum(int(row.get("normalized_row_count") or 0) for row in selected)
    ledger_count = ledger.height
    if selected_row_count != manifest_row_count:
        errors.append(
            f"Selected staging rows {selected_row_count} != manifest rows {manifest_row_count}"
        )
    if supersession["permitted_supersessions"] != EXPECTED_SUPERSESSIONS:
        errors.append(
            f"Validated supersessions {supersession['permitted_supersessions']} "
            f"!= {EXPECTED_SUPERSESSIONS}"
        )
    if ledger_count != EXPECTED_SUPERSESSIONS:
        errors.append(
            f"Persisted supersession ledger rows {ledger_count} != {EXPECTED_SUPERSESSIONS}"
        )

    with duckdb.connect(str(database_path), read_only=True) as connection:
        active_row = connection.execute(
            "SELECT count(*), count(DISTINCT case_id) FROM lca_cases_resolved"
        ).fetchone()
        if active_row is None:
            raise RuntimeError("LCA active-count query returned no row")
        active_count, distinct_cases = (int(active_row[0]), int(active_row[1]))
        if ledger.is_empty():
            superseded_present = retained_missing = 0
        else:
            connection.register("_audit_ledger", ledger.to_arrow())
            superseded_row = connection.execute(
                """
                SELECT count(*) FROM _audit_ledger AS ledger
                JOIN lca_cases_resolved AS active
                  ON active.source_artifact_id = ledger.source_artifact_id
                 AND active.source_row_number = ledger.source_row_number
                """
            ).fetchone()
            retained_row = connection.execute(
                """
                SELECT count(*) FROM _audit_ledger AS ledger
                LEFT JOIN lca_cases_resolved AS active
                  ON active.source_artifact_id = ledger.superseding_source_artifact_id
                 AND active.source_row_number = ledger.superseding_source_row_number
                WHERE active.source_artifact_id IS NULL
                """
            ).fetchone()
            if superseded_row is None or retained_row is None:
                raise RuntimeError("LCA supersession materialization query returned no row")
            superseded_present = int(superseded_row[0])
            retained_missing = int(retained_row[0])
    if active_count != selected_row_count - ledger_count:
        errors.append(
            f"Active rows {active_count} != staging {selected_row_count} - ledger {ledger_count}"
        )
    if active_count != distinct_cases:
        errors.append(
            f"Active LCA rows {active_count} contain only {distinct_cases} distinct cases"
        )
    if superseded_present:
        errors.append(f"{superseded_present} superseded staging rows remain active")
    if retained_missing:
        errors.append(
            f"{retained_missing} latest valid determinations are missing from active data"
        )

    summary = {
        "passed": not errors,
        "completed_fiscal_years": sorted(REVIEWED_LCA_COMPLETED_SEGMENTS),
        "current_partial_fiscal_year": 2026,
        "selected_artifact_count": len(selected),
        "selected_staging_row_count": selected_row_count,
        "active_row_count": int(active_count),
        "active_distinct_case_count": int(distinct_cases),
        "duplicate_case_ids_in_staging": supersession["duplicate_case_ids"],
        "validated_supersession_count": supersession["permitted_supersessions"],
        "persisted_supersession_ledger_count": ledger_count,
        "superseded_rows_still_active": int(superseded_present),
        "retained_rows_missing": int(retained_missing),
        "immutable_staging_preserved": selected_row_count == manifest_row_count,
        "errors": errors,
        "artifacts": artifact_details,
    }
    return summary


def _canonical_fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DOL segmentation final audit",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Result: **{'PASS' if report['passed'] else 'FAIL'}**",
        "",
        "## Reconciliation",
        "",
        f"- Selected immutable staging rows: {report['selected_staging_row_count']:,}",
        "- Validated certified-to-certified-withdrawn supersessions: "
        f"{report['validated_supersession_count']:,}",
        f"- Active normalized rows: {report['active_row_count']:,}",
        f"- Active distinct case IDs: {report['active_distinct_case_count']:,}",
        f"- Superseded rows still active: {report['superseded_rows_still_active']:,}",
        f"- Latest valid determinations missing: {report['retained_rows_missing']:,}",
        f"- Deterministic audit rerun: {'PASS' if report['deterministic_rerun'] else 'FAIL'}",
        f"- Deterministic fingerprint: `{report['deterministic_fingerprint']}`",
        "",
        "## Selected segments",
        "",
        "| FY | Segment | Partial | Rows | Decision dates | Observed quarters |",
        "|---:|---|:---:|---:|---|---|",
    ]
    for row in report["artifacts"]:
        segment = (
            f"Q{row['coverage_start_quarter']}-Q{row['fiscal_quarter']}"
            if row["fiscal_quarter"] is not None
            else "Annual"
        )
        lines.append(
            f"| {row['fiscal_year']} | {segment} | {row['is_partial_period']} | "
            f"{row['row_count']:,} | {row['decision_date_min']} to "
            f"{row['decision_date_max']} | {row['observed_fiscal_quarters']} |"
        )
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {error}" for error in report["errors"])
    if not report["errors"]:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("db/immigration.duckdb"))
    parser.add_argument(
        "--manifest", type=Path, default=Path("outputs/manifests/source_artifacts.jsonl")
    )
    parser.add_argument("--output-root", type=Path, default=Path("outputs/reports/product-a"))
    args = parser.parse_args()
    repository_root = Path.cwd().resolve()
    first = _audit_once(
        repository_root=repository_root,
        data_root=args.data_root,
        database_path=args.database,
        manifest_path=args.manifest,
    )
    second = _audit_once(
        repository_root=repository_root,
        data_root=args.data_root,
        database_path=args.database,
        manifest_path=args.manifest,
    )
    first_fingerprint = _canonical_fingerprint(first)
    second_fingerprint = _canonical_fingerprint(second)
    report = first | {
        "generated_at": datetime.now(UTC).isoformat(),
        "deterministic_rerun": first_fingerprint == second_fingerprint,
        "deterministic_fingerprint": first_fingerprint,
    }
    report["passed"] = bool(report["passed"] and report["deterministic_rerun"])
    _write_atomic(
        args.output_root / "dol-segmentation-final-audit.json",
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    _write_atomic(
        args.output_root / "dol-segmentation-final-audit.md",
        _markdown(report),
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
