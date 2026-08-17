"""Generate the authoritative Product A real-data acceptance report family.

The runner is intentionally read-only with respect to source, processed, and DuckDB inputs. It
independently checks the active Product A score contract, reconciles rating ingredients to raw
evidence, verifies selected artifacts against the source manifest and immutable raw files, and
writes only the nine reports required by ``PRODUCT_A_SPEC.md``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb
import polars as pl

from sponsor_intel.sources.manifests import write_json_atomic

EXPECTED_METRIC_VERSION = "product_a_metrics_v1"
EXPECTED_SCORE_VERSION = "product_a_scores_v1"
EXPECTED_COUNT_PERCENTILE_CAP = 0.95
_CANONICAL_CASE_STATUS_SQL = (
    r"upper(regexp_replace(trim(coalesce(case_status, '')), '\s*-\s*', '-', 'g'))"
)

REPORT_FILES = (
    "source-selection.md",
    "source-selection.json",
    "score-distribution.md",
    "score-distribution.json",
    "validation.md",
    "validation.csv",
    "unresolved-entities.csv",
    "acceptance.md",
    "acceptance.json",
)

REQUIRED_PROCESSED_TABLES = (
    "data_health",
    "employer_metrics",
    "h1b_petitions_resolved",
    "herd_observations",
    "institution_metrics",
    "institutions",
    "lca_cases_resolved",
    "legal_entities",
    "parent_organizations",
    "perm_cases_resolved",
    "source_artifacts",
)

REQUIRED_VIEWS = {
    "vw_data_health",
    "vw_employer_explorer",
    "vw_entity_review_queue",
    "vw_everify_evidence",
    "vw_h1b_trends",
    "vw_institution_explorer",
    "vw_opt_evidence",
    "vw_organization_detail",
    "vw_perm_trends",
    "vw_quality_checks",
    "vw_relevant_titles",
    "vw_source_artifacts",
}

REQUIRED_SOURCE_IDS = {
    "dol_lca",
    "dol_perm",
    "herd",
    "ipeds",
    "sevp_opt",
    "uscis_h1b",
}

OFFICIAL_SOURCE_DOMAINS = {
    "dol_lca": ("dol.gov",),
    "dol_perm": ("dol.gov",),
    "herd": ("nsf.gov",),
    "ipeds": ("ed.gov",),
    "sevp_opt": ("ice.gov",),
    "uscis_h1b": ("uscis.gov", "uscis.dhs.gov"),
}

REVIEWED_LCA_COMPLETED_SEGMENTS = {
    2022: {(1, 1), (2, 2), (3, 3), (4, 4)},
    2023: {(1, 2), (3, 3), (4, 4)},
    2024: {(1, 1), (2, 2), (3, 3), (4, 4)},
    2025: {(1, 1), (2, 2), (3, 3), (4, 4)},
}

VALIDATION_FIELDS = (
    "target",
    "category",
    "selection_status",
    "organization_id",
    "identity_scope",
    "organization_name",
    "institution_id",
    "legal_entity_id",
    "legal_entity_name",
    "parent_organization_id",
    "parent_organization_name",
    "raw_employer_names",
    "legal_address_examples",
    "worksite_examples",
    "relevant_certified_lca_count",
    "relevant_certified_withdrawn_lca_count",
    "weighted_relevant_lca_count",
    "relevant_certified_perm_count",
    "relevant_certified_expired_perm_count",
    "weighted_relevant_perm_count",
    "employer_level_h1b_initial_approvals",
    "relevant_job_families",
    "raw_title_examples",
    "case_statuses",
    "source_artifact_ids",
    "entity_coverage_state",
    "h1b_entity_coverage_state",
    "perm_entity_coverage_state",
    "h1b_history_stars",
    "h1b_history_star_label",
    "green_card_history_stars",
    "green_card_history_star_label",
    "overall_sponsorship_stars",
    "overall_sponsorship_star_label",
    "research_scale_stars",
    "research_scale_star_label",
    "h1b_history_explanation",
    "green_card_history_explanation",
    "overall_sponsorship_explanation",
    "everify_status",
    "supplemental_exclusion_evidence",
    "ambiguity_note",
)


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    """One independently evaluated Product A invariant."""

    check_id: str
    requirement: str
    passed: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class EmployerTarget:
    label: str
    aliases: tuple[str, ...]
    scope: str
    optional: bool = False


EMPLOYER_TARGETS = (
    EmployerTarget("Microsoft legal entity", ("Microsoft Corporation",), "LEGAL_ENTITY"),
    EmployerTarget("Microsoft parent rollup", ("Microsoft",), "PARENT_ROLLUP"),
    EmployerTarget("Google legal entity", ("Google LLC",), "LEGAL_ENTITY"),
    EmployerTarget("Google parent rollup", ("Google", "Alphabet"), "PARENT_ROLLUP"),
    EmployerTarget("Amazon legal entity", ("Amazon.com Services LLC",), "LEGAL_ENTITY"),
    EmployerTarget("Amazon parent rollup", ("Amazon",), "PARENT_ROLLUP"),
    EmployerTarget("Meta", ("Meta Platforms, Inc.",), "LEGAL_ENTITY"),
    EmployerTarget("Meta parent rollup", ("Meta Platforms",), "PARENT_ROLLUP"),
    EmployerTarget(
        "IBM",
        ("IBM Corporation", "International Business Machines Corporation"),
        "LEGAL_ENTITY",
    ),
    EmployerTarget("IBM parent rollup", ("IBM",), "PARENT_ROLLUP"),
    EmployerTarget(
        "Smart Data Solutions",
        ("Smart Data Solutions LLC",),
        "LEGAL_ENTITY",
        optional=True,
    ),
)

INSTITUTION_TARGETS = (
    "Massachusetts Institute of Technology",
    "Carnegie Mellon University",
    "Rice University",
    "University of Michigan-Ann Arbor",
    "University of Illinois Urbana-Champaign",
    "University of Washington-Seattle Campus",
)


class AcceptanceInputError(RuntimeError):
    """Raised when acceptance cannot inspect a real, materialized build."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(text.rstrip())
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _write_csv_atomic(path: Path, rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        field: (
                            json.dumps(_json_safe(row.get(field)), sort_keys=True)
                            if isinstance(row.get(field), (list, tuple, dict))
                            else _json_safe(row.get(field))
                        )
                        for field in fields
                    }
                )
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(is_junction and is_junction(path))


def _remove_flat_report_directory(path: Path) -> None:
    """Remove only one validated, flat report-family directory."""

    if not path.exists():
        return
    if path.parent == path or _is_link_or_junction(path) or not path.is_dir():
        raise AcceptanceInputError(f"Unsafe report output directory: {path}")
    entries = list(path.iterdir())
    unsafe_entries = [
        entry for entry in entries if _is_link_or_junction(entry) or not entry.is_file()
    ]
    if unsafe_entries:
        raise AcceptanceInputError(
            "Report output must be a flat directory of regular files; refusing to replace "
            f"{path}: {unsafe_entries}"
        )
    for entry in entries:
        entry.unlink()
    path.rmdir()


def _prepare_report_destination(path: Path) -> None:
    if path.parent == path or not path.name:
        raise AcceptanceInputError(f"Unsafe report output directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(path.parent) or not path.parent.is_dir():
        raise AcceptanceInputError(f"Unsafe report output parent: {path.parent}")
    _remove_flat_report_directory(path)


def _temporary_report_directory(output_root: Path) -> Path:
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}-acceptance-", dir=output_root.parent)
    ).resolve()
    if temporary.parent != output_root.parent or _is_link_or_junction(temporary):
        _remove_flat_report_directory(temporary)
        raise AcceptanceInputError(f"Unsafe temporary report directory: {temporary}")
    return temporary


def _publish_report_directory(temporary: Path, output_root: Path) -> None:
    observed = {entry.name for entry in temporary.iterdir() if entry.is_file()}
    non_files = [entry for entry in temporary.iterdir() if not entry.is_file()]
    expected = set(REPORT_FILES)
    if observed != expected or non_files:
        raise AcceptanceInputError(
            "Generated Product A report family is not exact: "
            f"missing={sorted(expected - observed)}; extras={sorted(observed - expected)}; "
            f"non_files={non_files}"
        )
    if output_root.exists():
        raise AcceptanceInputError(
            f"Report output was recreated during acceptance; refusing replacement: {output_root}"
        )
    os.replace(temporary, output_root)


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AcceptanceInputError(f"Source manifest is unavailable: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise AcceptanceInputError(
                f"Invalid source manifest JSON at {path}:{line_number}"
            ) from error
        if not isinstance(value, dict):
            raise AcceptanceInputError(f"Manifest row {line_number} is not an object")
        rows.append(value)
    if not rows:
        raise AcceptanceInputError(f"Source manifest is empty: {path}")
    return rows


def _table_columns(connection: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    try:
        return {str(row[0]) for row in connection.execute(f'DESCRIBE "{relation}"').fetchall()}
    except duckdb.Error:
        return set()


def _frame(
    connection: duckdb.DuckDBPyConnection, query: str, parameters: Sequence[Any] = ()
) -> pl.DataFrame:
    return connection.execute(query, list(parameters)).pl()


def _scalar(connection: duckdb.DuckDBPyConnection, query: str) -> Any:
    row = connection.execute(query).fetchone()
    return None if row is None else row[0]


def _normalized_name(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _artifact_period_label(row: dict[str, Any]) -> str:
    year = row.get("fiscal_year")
    quarter = row.get("fiscal_quarter")
    label = f"FY{year}" if year is not None else "UNKNOWN"
    if quarter is not None:
        label += f" Q{quarter}"
    if row.get("is_quarter_partition"):
        start_quarter = row.get("coverage_start_quarter")
        end_quarter = row.get("fiscal_quarter")
        coverage = (
            f"Q{start_quarter}-Q{end_quarter}"
            if start_quarter is not None and end_quarter is not None
            else "unknown bounds"
        )
        state = f"complete-year coverage segment {coverage}"
    else:
        state = "partial" if row.get("is_partial_period") else "complete"
    return f"{label} ({state})"


def _is_official_url(value: Any, source_id: str) -> bool:
    parsed = urlparse(str(value or ""))
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in OFFICIAL_SOURCE_DOMAINS.get(source_id, ())
    )


def _resolve_record_path(value: Any, *, repository_root: Path) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    return path if path.is_absolute() else repository_root / path


_LCA_ACCEPTANCE_IDENTITY_TRANSLATION = str.maketrans("", "", "\ufffd\u2013\u201c\u2019\u00bf")


def _stable_source_identity(value: Any, *, postal_code: bool = False) -> str:
    repaired = str(value or "").replace("\u00c3\u00b6", "\u00f6")
    normalized = " ".join(
        repaired.translate(_LCA_ACCEPTANCE_IDENTITY_TRANSLATION).split()
    ).casefold()
    if postal_code and len(normalized) == 4 and normalized.isascii() and normalized.isdigit():
        return f"0{normalized}"
    return normalized


def _lca_global_supersession_audit(
    selected: list[dict[str, Any]],
    manifest_by_id: dict[str, dict[str, Any]],
    *,
    repository_root: Path,
) -> dict[str, Any]:
    scans: list[pl.LazyFrame] = []
    failures: list[str] = []
    required_columns = {
        "case_id",
        "case_status",
        "decision_date",
        "source_row_number",
        "employer_name_raw",
    }
    identity_columns = (
        "employer_name_raw",
        "visa_class",
        "employer_address_1",
        "employer_address_2",
        "employer_city",
        "employer_state",
        "employer_postal_code",
    )
    for row in selected:
        if row.get("source_id") != "dol_lca":
            continue
        artifact_id = str(row.get("source_artifact_id") or "")
        manifest = manifest_by_id.get(artifact_id)
        parquet_path = _resolve_record_path(
            manifest.get("parquet_path") if manifest else None,
            repository_root=repository_root,
        )
        if parquet_path is None or not parquet_path.is_file():
            failures.append(f"{artifact_id}:missing normalized Parquet {parquet_path}")
            continue
        source = pl.scan_parquet(parquet_path)
        available = set(source.collect_schema().names())
        missing = required_columns - available
        if missing:
            failures.append(f"{artifact_id}:missing columns {sorted(missing)}")
            continue
        scans.append(
            source.select(
                pl.lit(artifact_id).alias("source_artifact_id"),
                pl.col("source_row_number").cast(pl.Int64, strict=False),
                pl.col("case_id").cast(pl.String, strict=False),
                pl.lit(int(row["fiscal_year"])).alias("fiscal_year"),
                pl.col("case_status").cast(pl.String, strict=False),
                pl.col("decision_date").cast(pl.Date, strict=False),
                *[
                    (
                        pl.col(column).cast(pl.String, strict=False)
                        if column in available
                        else pl.lit(None, dtype=pl.String).alias(column)
                    )
                    for column in identity_columns
                ],
            )
        )
    retained: list[dict[str, Any]] = []
    duplicate_case_ids = 0
    if scans:
        combined = pl.concat(scans, how="vertical_relaxed")
        duplicate_keys = (
            combined.group_by("case_id").len().filter(pl.col("len") > 1).select("case_id")
        )
        overlaps = combined.join(duplicate_keys, on="case_id", how="inner").collect()
        duplicate_case_ids = overlaps.get_column("case_id").n_unique()
        for case_rows in overlaps.partition_by("case_id", maintain_order=True):
            ordered = case_rows.sort(["decision_date", "source_artifact_id", "source_row_number"])
            rows = ordered.to_dicts()
            case_id = str(rows[0].get("case_id") or "").strip()
            fiscal_years = sorted({int(row["fiscal_year"]) for row in rows})
            if len(rows) != 2:
                failures.append(
                    f"{case_id}:unsupported selected-artifact overlap across fiscal years "
                    f"{fiscal_years}"
                )
                continue
            earlier, later = rows
            earlier_status = re.sub(
                r"\s*-\s*", "-", str(earlier.get("case_status") or "").strip().upper()
            )
            later_status = re.sub(
                r"\s*-\s*", "-", str(later.get("case_status") or "").strip().upper()
            )
            stable_identity = all(
                _stable_source_identity(
                    earlier.get(column), postal_code=column == "employer_postal_code"
                )
                == _stable_source_identity(
                    later.get(column), postal_code=column == "employer_postal_code"
                )
                for column in identity_columns
            )
            earlier_date = earlier.get("decision_date")
            later_date = later.get("decision_date")
            valid = bool(
                earlier.get("source_artifact_id") != later.get("source_artifact_id")
                and case_id
                and earlier.get("source_row_number") is not None
                and later.get("source_row_number") is not None
                and earlier_date is not None
                and later_date is not None
                and earlier_date < later_date
                and earlier_status == "CERTIFIED"
                and later_status == "CERTIFIED-WITHDRAWN"
                and _stable_source_identity(earlier.get("employer_name_raw"))
                and stable_identity
            )
            if not valid:
                failures.append(
                    f"{case_id}:unsupported selected-artifact overlap across fiscal years "
                    f"{fiscal_years}"
                )
                continue
            retained.append(
                {
                    "fiscal_year": int(earlier["fiscal_year"]),
                    "superseding_fiscal_year": int(later["fiscal_year"]),
                    "case_id": case_id,
                    "superseded_source_artifact_id": str(earlier["source_artifact_id"]),
                    "superseded_source_row_number": int(earlier["source_row_number"]),
                    "retained_source_artifact_id": str(later["source_artifact_id"]),
                    "retained_source_row_number": int(later["source_row_number"]),
                }
            )
    return {
        "duplicate_case_ids": duplicate_case_ids,
        "permitted_supersessions": len(retained),
        "failures": failures,
        "retained_rows": retained,
    }


def _source_selection(
    artifacts: pl.DataFrame,
    manifest_rows: list[dict[str, Any]],
    *,
    repository_root: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], list[AcceptanceCheck]]:
    selected = [_json_safe(row) for row in artifacts.to_dicts()]
    manifest_by_id = {
        str(row.get("source_artifact_id")): row
        for row in manifest_rows
        if row.get("source_artifact_id")
    }
    details: list[dict[str, Any]] = []
    missing_manifest: list[str] = []
    metadata_mismatches: list[str] = []
    raw_failures: list[str] = []
    invalid_provenance: list[str] = []
    for selected_row in selected:
        artifact_id = str(selected_row.get("source_artifact_id") or "")
        manifest = manifest_by_id.get(artifact_id)
        raw_path: Path | None = None
        raw_verified = False
        if manifest is None:
            missing_manifest.append(artifact_id)
        else:
            for column in (
                "source_id",
                "download_url",
                "sha256",
                "fiscal_year",
                "fiscal_quarter",
                "is_partial_period",
                "is_quarter_partition",
                "coverage_start_quarter",
            ):
                if str(selected_row.get(column)) != str(manifest.get(column)):
                    metadata_mismatches.append(f"{artifact_id}:{column}")
            for selected_column, manifest_column in (
                ("raw_row_count", "raw_row_count"),
                ("normalized_row_count", "row_count"),
            ):
                if selected_row.get(selected_column) != manifest.get(manifest_column):
                    metadata_mismatches.append(f"{artifact_id}:{selected_column}")
            raw_path = _resolve_record_path(
                manifest.get("raw_path"), repository_root=repository_root
            )
            if raw_path is None or not raw_path.is_file():
                raw_failures.append(f"{artifact_id}:missing raw file {raw_path}")
            else:
                observed_sha = _sha256(raw_path)
                raw_verified = observed_sha == str(selected_row.get("sha256"))
                if not raw_verified:
                    raw_failures.append(f"{artifact_id}:checksum mismatch")
        source_id = str(selected_row.get("source_id") or "")
        url = str(selected_row.get("download_url") or "")
        checksum = str(selected_row.get("sha256") or "")
        raw_rows = int(selected_row.get("raw_row_count") or 0)
        normalized_rows = int(selected_row.get("normalized_row_count") or 0)
        if (
            not _is_official_url(url, source_id)
            or not _is_official_url(selected_row.get("landing_page_url"), source_id)
            or (
                selected_row.get("record_layout_url")
                and not _is_official_url(selected_row.get("record_layout_url"), source_id)
            )
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            or raw_rows <= 0
            or normalized_rows <= 0
            or selected_row.get("validation_status") == "FAILED"
        ):
            invalid_provenance.append(artifact_id)
        details.append(
            selected_row
            | {
                "period": _artifact_period_label(selected_row),
                "raw_path": str(raw_path) if raw_path is not None else None,
                "raw_checksum_verified": raw_verified,
            }
        )

    source_ids = {str(row.get("source_id")) for row in selected}
    missing_sources = sorted(REQUIRED_SOURCE_IDS - source_ids)
    supersession_audit = _lca_global_supersession_audit(
        selected,
        manifest_by_id,
        repository_root=repository_root,
    )

    def source_rows(source_id: str) -> list[dict[str, Any]]:
        return [row for row in selected if row.get("source_id") == source_id]

    selection_errors: list[str] = []
    for source_id in ("dol_lca", "dol_perm", "uscis_h1b"):
        rows = source_rows(source_id)
        years = sorted({int(row["fiscal_year"]) for row in rows}) if rows else []
        if not years or years[0] != 2022 or years != list(range(2022, max(years) + 1)):
            selection_errors.append(f"{source_id}: non-contiguous FY2022-onward coverage {years}")
        partial_years = sorted(
            {int(row["fiscal_year"]) for row in rows if row.get("is_partial_period")}
        )
        if len(partial_years) > 1 or (partial_years and partial_years[0] != max(years)):
            selection_errors.append(f"{source_id}: invalid partial years {partial_years}")

    for source_id in ("dol_lca", "dol_perm"):
        by_year: dict[int, list[dict[str, Any]]] = {}
        for row in source_rows(source_id):
            by_year.setdefault(int(row["fiscal_year"]), []).append(row)
        for year, rows in by_year.items():
            quarters = {row.get("fiscal_quarter") for row in rows}
            if len({str(row.get("file_name")) for row in rows}) != len(rows):
                selection_errors.append(f"{source_id}: FY{year} repeats a selected file")
            partial = any(bool(row.get("is_partial_period")) for row in rows)
            if partial:
                if len(rows) != 1 or any(row.get("is_quarter_partition") for row in rows):
                    selection_errors.append(
                        f"{source_id}: FY{year} partial period must be one cumulative artifact"
                    )
                continue
            if source_id == "dol_lca":
                partitioned = [bool(row.get("is_quarter_partition")) for row in rows]
                if any(partitioned):
                    segments = {
                        (row.get("coverage_start_quarter"), row.get("fiscal_quarter"))
                        for row in rows
                    }
                    reviewed_segments = REVIEWED_LCA_COMPLETED_SEGMENTS.get(year)
                    if not all(partitioned) or segments != reviewed_segments:
                        selection_errors.append(
                            f"dol_lca: FY{year} completed segments do not match the reviewed "
                            f"coverage contract; observed={sorted(segments, key=str)}, "
                            f"expected={sorted(reviewed_segments or set(), key=str)}"
                        )
                elif len(rows) != 1 or quarters != {None}:
                    selection_errors.append(
                        f"dol_lca: FY{year} requires one annual artifact or a reviewed "
                        "Q1-Q4 coverage-segment contract"
                    )
            else:
                if len(quarters) != 1 or quarters not in ({4}, {None}):
                    selection_errors.append(
                        f"dol_perm: FY{year} complete period is not annual/Q4 ({quarters})"
                    )
                if any(row.get("is_quarter_partition") for row in rows):
                    selection_errors.append(
                        f"dol_perm: FY{year} must not use LCA coverage-segment semantics"
                    )

    perm_2024 = source_rows("dol_perm")
    perm_2024 = [row for row in perm_2024 if int(row.get("fiscal_year") or 0) == 2024]
    perm_variant_metadata: dict[str, list[str]] = {}
    perm_variant_errors: list[str] = []
    for row in perm_2024:
        artifact_id = str(row.get("source_artifact_id") or "")
        manifest = manifest_by_id.get(artifact_id)
        parquet_path = _resolve_record_path(
            manifest.get("parquet_path") if manifest else None,
            repository_root=repository_root,
        )
        if parquet_path is None or not parquet_path.is_file():
            perm_variant_errors.append(f"{artifact_id}: missing normalized Parquet")
            continue
        schema = pl.read_parquet_schema(parquet_path)
        if "form_version" not in schema:
            perm_variant_errors.append(f"{artifact_id}: missing form_version metadata")
            continue
        variants = sorted(
            {
                str(value).strip().casefold().replace("-", "_").replace(" ", "_")
                for value in (
                    pl.scan_parquet(parquet_path)
                    .select(pl.col("form_version").drop_nulls().unique())
                    .collect()["form_version"]
                    .to_list()
                )
                if str(value).strip()
            }
        )
        perm_variant_metadata[artifact_id] = variants
        if len(variants) != 1:
            perm_variant_errors.append(
                f"{artifact_id}: expected one explicit form_version, observed {variants}"
            )
    observed_perm_variants = {
        variants[0] for variants in perm_variant_metadata.values() if len(variants) == 1
    }
    perm_variants_ok = (
        len(perm_2024) == 2
        and not perm_variant_errors
        and observed_perm_variants == {"standard", "new_form"}
    )

    uscis_by_year: dict[int, int] = {}
    for row in source_rows("uscis_h1b"):
        year = int(row["fiscal_year"])
        uscis_by_year[year] = uscis_by_year.get(year, 0) + 1
    duplicate_uscis_years = {year: count for year, count in uscis_by_year.items() if count != 1}

    ipeds_rows = source_rows("ipeds")
    ipeds_directories = [
        row for row in ipeds_rows if str(row.get("file_name") or "").upper().startswith("HD")
    ]
    ipeds_characteristics = [
        row for row in ipeds_rows if str(row.get("file_name") or "").upper().startswith("IC")
    ]
    ipeds_years = {int(row["fiscal_year"]) for row in ipeds_rows}
    ipeds_dictionaries = all(
        str(row.get("record_layout_url") or "").startswith("https://")
        and "dict" in str(row.get("record_layout_url") or "").casefold()
        for row in ipeds_rows
    )
    ipeds_hd_ic = (
        len(ipeds_rows) == 2
        and len(ipeds_directories) == 1
        and len(ipeds_characteristics) == 1
        and len(ipeds_years) == 1
        and ipeds_dictionaries
    )

    herd_by_year: dict[int, list[str]] = {}
    for row in source_rows("herd"):
        herd_by_year.setdefault(int(row["fiscal_year"]), []).append(
            str(row.get("file_name") or "").casefold()
        )
    herd_errors = [
        year
        for year, names in herd_by_year.items()
        if not any("short" in name for name in names)
        or not any("short" not in name for name in names)
    ]
    herd_years = sorted(herd_by_year)
    herd_coverage_ok = (
        bool(herd_years)
        and herd_years[0] == 2022
        and herd_years == list(range(2022, max(herd_years) + 1))
        and not herd_errors
    )

    checks = [
        AcceptanceCheck(
            "source_manifest_parity",
            "Every selected artifact matches a source-manifest record",
            not missing_manifest and not metadata_mismatches,
            f"{len(selected)} selected; missing={missing_manifest}; "
            f"mismatches={metadata_mismatches}.",
        ),
        AcceptanceCheck(
            "source_raw_checksums",
            "Every selected immutable raw artifact exists and matches SHA-256",
            not raw_failures,
            f"{len(selected) - len(raw_failures)}/{len(selected)} verified; "
            f"failures={raw_failures}.",
        ),
        AcceptanceCheck(
            "source_provenance",
            "Selected artifacts retain official URLs, checksums, row counts, and valid schemas",
            not invalid_provenance and not missing_sources,
            f"missing sources={missing_sources}; invalid artifacts={invalid_provenance}.",
        ),
        AcceptanceCheck(
            "dol_cumulative_selection",
            "DOL complete years use an annual period or a reviewed exact-coverage LCA segment set; "
            "current years use one latest period",
            not selection_errors,
            "; ".join(selection_errors)
            if selection_errors
            else "Annual/segment selections are structurally valid.",
        ),
        AcceptanceCheck(
            "lca_global_state_supersession",
            "Repeated LCA cases globally are only stable chronological certified-withdrawn updates",
            not supersession_audit["failures"]
            and supersession_audit["duplicate_case_ids"]
            == supersession_audit["permitted_supersessions"],
            f"duplicate case IDs={supersession_audit['duplicate_case_ids']}; permitted "
            f"supersessions={supersession_audit['permitted_supersessions']}; "
            f"failures={supersession_audit['failures'][:10]}.",
        ),
        AcceptanceCheck(
            "perm_form_variants",
            "Distinct standard and new-form FY2024 PERM variants are proven by form metadata",
            perm_variants_ok,
            f"FY2024 artifact form_version metadata={perm_variant_metadata}; "
            f"errors={perm_variant_errors}.",
        ),
        AcceptanceCheck(
            "uscis_one_artifact_per_year",
            "USCIS selects one employer-level artifact per fiscal year",
            not duplicate_uscis_years,
            f"per-year selected counts={uscis_by_year}.",
        ),
        AcceptanceCheck(
            "ipeds_finalized_hd_ic",
            "Latest finalized IPEDS HD directory and IC characteristics are selected",
            ipeds_hd_ic and not any(row.get("is_partial_period") for row in ipeds_rows),
            f"IPEDS files: {[row.get('file_name') for row in ipeds_rows]}; "
            f"years={sorted(ipeds_years)}; dictionaries={ipeds_dictionaries}.",
        ),
        AcceptanceCheck(
            "herd_full_short_coverage",
            "HERD full and short files are selected for each year from 2022 onward",
            herd_coverage_ok,
            f"years={herd_years}; incomplete full/short years={herd_errors}.",
        ),
    ]
    warnings = [
        {
            "source_artifact_id": row.get("source_artifact_id"),
            "source_id": row.get("source_id"),
            "validation_status": row.get("validation_status"),
        }
        for row in selected
        if row.get("validation_status") == "WARNING"
    ]
    return (
        {
            "manifest_path": str(manifest_path),
            "manifest_record_count": len(manifest_rows),
            "selected_artifact_count": len(selected),
            "selected_sources": sorted(source_ids),
            "validation_warnings": warnings,
            "lca_global_supersessions": {
                "duplicate_case_ids": supersession_audit["duplicate_case_ids"],
                "permitted_supersessions": supersession_audit["permitted_supersessions"],
                "failure_count": len(supersession_audit["failures"]),
            },
            "_retained_lca_supersessions": supersession_audit["retained_rows"],
            "artifacts": details,
        },
        checks,
    )


def _recency_expr(last_year_column: str) -> pl.Expr:
    last_year = pl.col(last_year_column).cast(pl.Int64)
    latest_complete = pl.col("latest_complete_immigration_fiscal_year").cast(pl.Int64)
    current_partial = pl.col("current_partial_immigration_fiscal_year").cast(pl.Int64)
    lag = (latest_complete - last_year).clip(lower_bound=0)
    return (
        pl.when(last_year.is_null())
        .then(0.0)
        .when(current_partial.is_not_null() & (last_year == current_partial))
        .then(100.0)
        .when(lag == 0)
        .then(100.0)
        .when(lag == 1)
        .then(75.0)
        .when(lag == 2)
        .then(50.0)
        .when(lag == 3)
        .then(25.0)
        .otherwise(0.0)
    )


def _nearest_p95_cap(frame: pl.DataFrame, column: str, eligible: pl.Expr) -> float:
    values = (
        frame.filter(eligible.fill_null(False)).get_column(column).cast(pl.Float64).drop_nulls()
    )
    values = values.filter(values >= 0)
    if values.is_empty():
        return 0.0
    result = values.quantile(EXPECTED_COUNT_PERCENTILE_CAP, interpolation="nearest")
    return round(float(result or 0.0), 6)


def _constant_metadata_matches(frame: pl.DataFrame, column: str, expected: float) -> bool:
    values = frame.get_column(column).cast(pl.Float64)
    return (
        values.null_count() == 0
        and values.n_unique() == 1
        and values.len() > 0
        and float(values[0]) == expected
    )


def _log_component(value_column: str, cap: float) -> pl.Expr:
    denominator = math.log1p(max(cap, 1.0))
    return (
        pl.col(value_column).cast(pl.Float64).fill_null(0.0).clip(lower_bound=0).log1p()
        / denominator
        * 100
    ).clip(0.0, 100.0)


def _expected_star(score: pl.Expr) -> pl.Expr:
    return (
        pl.when(score >= 80)
        .then(pl.lit(5, dtype=pl.Int8))
        .when(score >= 65)
        .then(pl.lit(4, dtype=pl.Int8))
        .when(score >= 45)
        .then(pl.lit(3, dtype=pl.Int8))
        .when(score >= 25)
        .then(pl.lit(2, dtype=pl.Int8))
        .when(score > 0)
        .then(pl.lit(1, dtype=pl.Int8))
        .otherwise(pl.lit(None, dtype=pl.Int8))
    )


def _formula_contract(frame: pl.DataFrame) -> tuple[dict[str, int], dict[str, float]]:
    required = {
        "entity_resolution_valid",
        "entity_coverage_state",
        "h1b_entity_resolution_valid",
        "h1b_entity_coverage_state",
        "perm_entity_resolution_valid",
        "perm_entity_coverage_state",
        "green_card_history_score",
        "green_card_history_star_label",
        "green_card_history_star_rating",
        "green_card_history_stars",
        "green_card_history_status",
        "green_card_volume_p95_cap",
        "h1b_history_score",
        "h1b_history_star_label",
        "h1b_history_star_rating",
        "h1b_history_stars",
        "h1b_history_status",
        "h1b_volume_p95_cap",
        "has_unresolved_h1b_candidate_evidence",
        "has_unresolved_perm_candidate_evidence",
        "identity_scope",
        "initial_approvals",
        "lca_complete_active_years",
        "lca_complete_fiscal_year_count",
        "lca_relevant_job_family_count",
        "lca_source_valid",
        "last_relevant_lca_activity_year",
        "last_relevant_perm_activity_year",
        "latest_complete_immigration_fiscal_year",
        "current_partial_immigration_fiscal_year",
        "overall_sponsorship_score",
        "overall_sponsorship_star_label",
        "overall_sponsorship_star_rating",
        "overall_sponsorship_stars",
        "overall_sponsorship_status",
        "perm_complete_active_years",
        "perm_complete_fiscal_year_count",
        "perm_relevant_job_family_count",
        "perm_source_valid",
        "score_count_percentile_cap",
        "uscis_initial_approvals_p95_cap",
        "uscis_source_valid",
        "weighted_relevant_lca_count",
        "weighted_relevant_perm_count",
    }
    missing = required - set(frame.columns)
    if missing:
        return (
            {
                "missing_required_columns": len(missing),
                "cap_mismatches": 3,
                "percentile_metadata_mismatches": 1,
                "formula_mismatches": frame.height,
                "rating_mismatches": frame.height,
                "explanation_mismatches": frame.height,
            },
            {},
        )

    legal_entity_scope = pl.col("identity_scope") == "LEGAL_ENTITY"
    lca_valid = pl.col("lca_source_valid").fill_null(False) & pl.col(
        "h1b_entity_resolution_valid"
    ).fill_null(False)
    perm_valid = pl.col("perm_source_valid").fill_null(False) & pl.col(
        "perm_entity_resolution_valid"
    ).fill_null(False)
    uscis_valid = pl.col("uscis_source_valid").fill_null(False)
    entity_valid = pl.col("entity_resolution_valid").fill_null(False)
    recomputed_caps = {
        "h1b_volume_p95_cap": _nearest_p95_cap(
            frame,
            "weighted_relevant_lca_count",
            lca_valid & legal_entity_scope,
        ),
        "green_card_volume_p95_cap": _nearest_p95_cap(
            frame,
            "weighted_relevant_perm_count",
            perm_valid & legal_entity_scope,
        ),
        "uscis_initial_approvals_p95_cap": _nearest_p95_cap(
            frame,
            "initial_approvals",
            entity_valid & uscis_valid & legal_entity_scope,
        ),
    }
    cap_mismatches = sum(
        not _constant_metadata_matches(frame, column, expected)
        for column, expected in recomputed_caps.items()
    )
    percentile_metadata_mismatches = int(
        not _constant_metadata_matches(
            frame,
            "score_count_percentile_cap",
            EXPECTED_COUNT_PERCENTILE_CAP,
        )
    )
    h1b_observed = pl.col("weighted_relevant_lca_count").fill_null(0.0) > 0
    perm_observed = pl.col("weighted_relevant_perm_count").fill_null(0.0) > 0
    h1b_denominator = pl.when(uscis_valid).then(1.0).otherwise(0.95)
    h1b = (
        _log_component("weighted_relevant_lca_count", recomputed_caps["h1b_volume_p95_cap"]) * 0.45
        + pl.when(pl.col("lca_complete_fiscal_year_count") > 0)
        .then(pl.col("lca_complete_active_years") / pl.col("lca_complete_fiscal_year_count") * 100)
        .otherwise(0.0)
        * 0.25
        + _recency_expr("last_relevant_lca_activity_year") * 0.15
        + (pl.col("lca_relevant_job_family_count") / 5 * 100).clip(0.0, 100.0) * 0.10
        + pl.when(uscis_valid)
        .then(
            _log_component("initial_approvals", recomputed_caps["uscis_initial_approvals_p95_cap"])
            * 0.05
        )
        .otherwise(0.0)
    ) / h1b_denominator
    expected_h1b = (
        pl.when(~lca_valid)
        .then(pl.lit(None, dtype=pl.Float64))
        .when(~h1b_observed)
        .then(0.0)
        .otherwise(h1b.clip(0.0, 100.0).round(2))
    )
    green = (
        _log_component("weighted_relevant_perm_count", recomputed_caps["green_card_volume_p95_cap"])
        * 0.45
        + pl.when(pl.col("perm_complete_fiscal_year_count") > 0)
        .then(
            pl.col("perm_complete_active_years") / pl.col("perm_complete_fiscal_year_count") * 100
        )
        .otherwise(0.0)
        * 0.25
        + _recency_expr("last_relevant_perm_activity_year") * 0.15
        + (pl.col("perm_relevant_job_family_count") / 5 * 100).clip(0.0, 100.0) * 0.15
    )
    expected_green = (
        pl.when(~perm_valid)
        .then(pl.lit(None, dtype=pl.Float64))
        .when(~perm_observed)
        .then(0.0)
        .otherwise(green.clip(0.0, 100.0).round(2))
    )
    evaluated = frame.with_columns(
        expected_h1b.alias("_expected_h1b"),
        expected_green.alias("_expected_green"),
    ).with_columns(
        pl.when(pl.col("_expected_h1b").is_not_null() & pl.col("_expected_green").is_not_null())
        .then((pl.col("_expected_h1b") * 0.4 + pl.col("_expected_green") * 0.6).round(2))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("_expected_overall")
    )

    def numeric_mismatch(actual: str, expected: str) -> pl.Expr:
        return (pl.col(actual).is_null() != pl.col(expected).is_null()) | (
            pl.col(actual).is_not_null()
            & pl.col(expected).is_not_null()
            & ((pl.col(actual) - pl.col(expected)).abs() > 0.011)
        )

    formula_mismatches = evaluated.filter(
        numeric_mismatch("h1b_history_score", "_expected_h1b")
        | numeric_mismatch("green_card_history_score", "_expected_green")
        | numeric_mismatch("overall_sponsorship_score", "_expected_overall")
    ).height

    evaluated = evaluated.with_columns(
        _expected_star(pl.col("_expected_h1b")).alias("_expected_h1b_star"),
        _expected_star(pl.col("_expected_green")).alias("_expected_green_star"),
        _expected_star(pl.col("_expected_overall")).alias("_expected_overall_star"),
    )
    rating_mismatches = 0
    for prefix, expected, no_observed_text in (
        ("h1b_history", "_expected_h1b", "No observed technical H-1B history"),
        (
            "green_card_history",
            "_expected_green",
            "No observed technical PERM history",
        ),
        (
            "overall_sponsorship",
            "_expected_overall",
            "No observed technical sponsorship history",
        ),
    ):
        expected_status = (
            pl.when(pl.col(expected).is_null())
            .then(pl.lit("UNRATED"))
            .when(pl.col(expected) == 0)
            .then(pl.lit("NO_OBSERVED_HISTORY"))
            .otherwise(pl.lit("RATED"))
        )
        expected_star = (
            f"_expected_{'green' if prefix == 'green_card_history' else prefix.split('_')[0]}_star"
        )
        expected_label = (
            pl.when(pl.col(expected).is_null())
            .then(pl.lit("Unrated"))
            .when(pl.col(expected) == 0)
            .then(pl.lit(no_observed_text))
            .otherwise(pl.format("{} out of 5 stars", pl.col(expected_star)))
        )
        expected_stars = (
            pl.when(pl.col(expected).is_null())
            .then(pl.lit("Unrated"))
            .when(pl.col(expected) == 0)
            .then(pl.lit(no_observed_text))
            .when(pl.col(expected_star) == 5)
            .then(pl.lit("★★★★★"))
            .when(pl.col(expected_star) == 4)
            .then(pl.lit("★★★★☆"))
            .when(pl.col(expected_star) == 3)
            .then(pl.lit("★★★☆☆"))
            .when(pl.col(expected_star) == 2)
            .then(pl.lit("★★☆☆☆"))
            .when(pl.col(expected_star) == 1)
            .then(pl.lit("★☆☆☆☆"))
            .otherwise(pl.lit(None, dtype=pl.String))
        )
        rating_mismatches += evaluated.filter(
            (
                pl.col(f"{prefix}_status").fill_null("__NULL__")
                != expected_status.fill_null("__NULL__")
            )
            | (pl.col(f"{prefix}_star_rating").is_null() != pl.col(expected_star).is_null())
            | (
                pl.col(f"{prefix}_star_rating").is_not_null()
                & (pl.col(f"{prefix}_star_rating") != pl.col(expected_star))
            )
            | (
                pl.col(f"{prefix}_star_label").fill_null("__NULL__")
                != expected_label.fill_null("__NULL__")
            )
            | (
                pl.col(f"{prefix}_stars").fill_null("__NULL__")
                != expected_stars.fill_null("__NULL__")
            )
        ).height
    explanation_mismatches = 0
    for prefix in ("h1b_history", "green_card_history", "overall_sponsorship"):
        column = f"{prefix}_explanation"
        if column not in evaluated.columns:
            explanation_mismatches += evaluated.height
        else:
            explanation_mismatches += evaluated.filter(
                pl.col(column).is_null() | (pl.col(column).str.strip_chars() == "")
            ).height
    return (
        {
            "missing_required_columns": 0,
            "cap_mismatches": cap_mismatches,
            "percentile_metadata_mismatches": percentile_metadata_mismatches,
            "formula_mismatches": formula_mismatches,
            "rating_mismatches": rating_mismatches,
            "explanation_mismatches": explanation_mismatches,
        },
        recomputed_caps,
    )


def _program_aggregate_mismatches(connection: duckdb.DuckDBPyConnection, *, program: str) -> int:
    if program == "lca":
        table = "lca_cases_resolved"
        base = "technical_role IS TRUE AND upper(trim(coalesce(visa_class, ''))) = 'H-1B'"
        full_status = "CERTIFIED"
        half_status = "CERTIFIED-WITHDRAWN"
        full_column = "relevant_certified_lca_count"
        half_column = "relevant_certified_withdrawn_lca_count"
    elif program == "perm":
        table = "perm_cases_resolved"
        base = "technical_role IS TRUE"
        full_status = "CERTIFIED"
        half_status = "CERTIFIED-EXPIRED"
        full_column = "relevant_certified_perm_count"
        half_column = "relevant_certified_expired_perm_count"
    else:
        raise ValueError(program)
    status = _CANONICAL_CASE_STATUS_SQL
    query = f"""
        WITH scoped AS (
            SELECT organization_id, 'LEGAL_ENTITY' AS identity_scope, * EXCLUDE (organization_id)
            FROM {table} WHERE organization_id IS NOT NULL
            UNION ALL
            SELECT parent_organization_id AS organization_id, 'PARENT_ROLLUP' AS identity_scope,
                * EXCLUDE (organization_id)
            FROM {table} WHERE parent_organization_id IS NOT NULL
        ), expected AS (
            SELECT organization_id, identity_scope,
                count(*) AS case_count,
                count_if({base} AND {status} = '{full_status}')
                    AS full_count,
                count_if({base} AND {status} = '{half_status}')
                    AS half_count,
                full_count + 0.5 * half_count AS weighted_count,
                count(DISTINCT CASE WHEN {base}
                    AND {status} IN ('{full_status}', '{half_status}')
                    AND NOT is_partial_period THEN fiscal_year END) AS complete_active_years,
                count(DISTINCT CASE WHEN {base}
                    AND {status} IN ('{full_status}', '{half_status}')
                    THEN role_family END) AS family_count
            FROM scoped GROUP BY organization_id, identity_scope
        ), actual AS (
            SELECT organization_id, identity_scope,
                {program}_case_count AS case_count,
                {full_column} AS full_count,
                {half_column} AS half_count,
                weighted_relevant_{program}_count AS weighted_count,
                {program}_complete_active_years AS complete_active_years,
                {program}_relevant_job_family_count AS family_count
            FROM employer_metrics
        )
        SELECT count(*)
        FROM expected FULL OUTER JOIN actual USING (organization_id, identity_scope)
        WHERE coalesce(expected.case_count, 0) != coalesce(actual.case_count, 0)
           OR coalesce(expected.full_count, 0) != coalesce(actual.full_count, 0)
           OR coalesce(expected.half_count, 0) != coalesce(actual.half_count, 0)
           OR abs(coalesce(expected.weighted_count, 0) - coalesce(actual.weighted_count, 0)) > .001
           OR coalesce(expected.complete_active_years, 0)
                != coalesce(actual.complete_active_years, 0)
           OR coalesce(expected.family_count, 0) != coalesce(actual.family_count, 0)
    """
    return int(_scalar(connection, query) or 0)


def _uscis_aggregate_mismatches(connection: duckdb.DuckDBPyConnection) -> int:
    query = """
        WITH scoped AS (
            SELECT organization_id, 'LEGAL_ENTITY' AS identity_scope, * EXCLUDE (organization_id)
            FROM h1b_petitions_resolved WHERE organization_id IS NOT NULL
            UNION ALL
            SELECT parent_organization_id AS organization_id, 'PARENT_ROLLUP' AS identity_scope,
                * EXCLUDE (organization_id)
            FROM h1b_petitions_resolved WHERE parent_organization_id IS NOT NULL
        ), expected AS (
            SELECT organization_id, identity_scope, count(*) AS employer_year_rows,
                sum(initial_approvals) AS initial_approvals
            FROM scoped GROUP BY organization_id, identity_scope
        ), actual AS (
            SELECT organization_id, identity_scope, uscis_employer_year_rows AS employer_year_rows,
                initial_approvals
            FROM employer_metrics
        )
        SELECT count(*)
        FROM expected FULL OUTER JOIN actual USING (organization_id, identity_scope)
        WHERE coalesce(expected.employer_year_rows, 0) != coalesce(actual.employer_year_rows, 0)
           OR coalesce(expected.initial_approvals, 0) != coalesce(actual.initial_approvals, 0)
    """
    return int(_scalar(connection, query) or 0)


def _rating_distribution(frame: pl.DataFrame, prefix: str) -> dict[str, Any]:
    status_column = f"{prefix}_status"
    star_column = f"{prefix}_star_rating"
    statuses = {
        str(row[status_column]): int(row["len"])
        for row in frame.group_by(status_column).len().sort(status_column).iter_rows(named=True)
    }
    stars = {
        str(int(row[star_column])): int(row["len"])
        for row in frame.filter(pl.col(star_column).is_not_null())
        .group_by(star_column)
        .len()
        .sort(star_column)
        .iter_rows(named=True)
    }
    return {
        "status": statuses,
        "stars": stars,
        "rated": statuses.get("RATED", 0),
        "no_observed_history": statuses.get("NO_OBSERVED_HISTORY", 0),
        "unrated": statuses.get("UNRATED", 0),
    }


def _distinct_values(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    column: str,
    predicate: str,
    organization_id: str,
    *,
    limit: int = 20,
) -> list[str]:
    if re.fullmatch(r"[a-z_][a-z0-9_]*", column) and column not in _table_columns(
        connection, table
    ):
        return []
    rows = connection.execute(
        f"""
        SELECT DISTINCT cast({column} AS VARCHAR) AS value
        FROM {table}
        WHERE {predicate} AND {column} IS NOT NULL AND trim(cast({column} AS VARCHAR)) != ''
        ORDER BY value LIMIT {int(limit)}
        """,
        [organization_id],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _evidence_snapshot(
    connection: duckdb.DuckDBPyConnection, organization_id: str, identity_scope: str
) -> dict[str, list[str]]:
    predicate = (
        "cast(parent_organization_id AS VARCHAR) = ?"
        if identity_scope == "PARENT_ROLLUP"
        else "cast(organization_id AS VARCHAR) = ?"
    )
    tables = ("lca_cases_resolved", "perm_cases_resolved", "h1b_petitions_resolved")
    raw_names = sorted(
        {
            value
            for table in tables
            for value in _distinct_values(
                connection, table, "employer_name_raw", predicate, organization_id
            )
        }
    )[:30]
    artifacts = sorted(
        {
            value
            for table in tables
            for value in _distinct_values(
                connection, table, "source_artifact_id", predicate, organization_id
            )
        }
    )
    titles = sorted(
        {
            value
            for table in ("lca_cases_resolved", "perm_cases_resolved")
            for value in _distinct_values(
                connection, table, "job_title_raw", predicate, organization_id, limit=12
            )
        }
    )[:20]
    families = sorted(
        {
            value
            for table in ("lca_cases_resolved", "perm_cases_resolved")
            for value in _distinct_values(
                connection, table, "role_family", predicate, organization_id
            )
        }
    )
    statuses = sorted(
        {
            value
            for table in ("lca_cases_resolved", "perm_cases_resolved")
            for value in _distinct_values(
                connection, table, "case_status", predicate, organization_id
            )
        }
    )
    legal_addresses: list[str] = []
    worksites: list[str] = []
    for table in ("lca_cases_resolved", "perm_cases_resolved"):
        columns = _table_columns(connection, table)
        employer_parts = [
            column
            for column in (
                "employer_address_1",
                "employer_city",
                "employer_state",
                "employer_postal_code",
            )
            if column in columns
        ]
        worksite_parts = [
            column for column in ("worksite_city", "worksite_state") if column in columns
        ]
        if employer_parts:
            expression = "concat_ws(', ', " + ", ".join(employer_parts) + ")"
            legal_addresses.extend(
                _distinct_values(connection, table, expression, predicate, organization_id, limit=6)
            )
        if worksite_parts:
            expression = "concat_ws(', ', " + ", ".join(worksite_parts) + ")"
            worksites.extend(
                _distinct_values(connection, table, expression, predicate, organization_id, limit=6)
            )
    return {
        "raw_employer_names": raw_names,
        "legal_address_examples": sorted(set(legal_addresses))[:10],
        "worksite_examples": sorted(set(worksites))[:10],
        "relevant_job_families": families,
        "raw_title_examples": titles,
        "case_statuses": statuses,
        "source_artifact_ids": artifacts,
    }


def _validation_row(
    connection: duckdb.DuckDBPyConnection,
    row: dict[str, Any] | None,
    *,
    target: str,
    category: str,
    status: str,
    ambiguity_note: str,
) -> dict[str, Any]:
    if row is None:
        return {
            field: (
                target
                if field == "target"
                else category
                if field == "category"
                else status
                if field == "selection_status"
                else ambiguity_note
                if field == "ambiguity_note"
                else []
                if field
                in {
                    "raw_employer_names",
                    "legal_address_examples",
                    "worksite_examples",
                    "relevant_job_families",
                    "raw_title_examples",
                    "case_statuses",
                    "source_artifact_ids",
                }
                else None
            )
            for field in VALIDATION_FIELDS
        }
    organization_id = str(row.get("organization_id") or "")
    identity_scope = str(row.get("identity_scope") or "LEGAL_ENTITY")
    snapshot = _evidence_snapshot(connection, organization_id, identity_scope)
    result = {field: row.get(field) for field in VALIDATION_FIELDS}
    result.update(snapshot)
    result.update(
        {
            "target": target,
            "category": category,
            "selection_status": status,
            "organization_id": organization_id,
            "identity_scope": identity_scope,
            "organization_name": row.get("organization_name") or row.get("official_name"),
            "legal_entity_name": row.get("legal_entity_name") or row.get("legal_employer_name"),
            "employer_level_h1b_initial_approvals": row.get("initial_approvals"),
            "supplemental_exclusion_evidence": (
                "Persisted sponsorship scores matched an independent DOL/USCIS-only formula; "
                "E-Verify, OPT, IPEDS, HERD, cap context, and policy fields were excluded."
            ),
            "ambiguity_note": ambiguity_note,
        }
    )
    return _json_safe(result)


def _candidate_rows(
    frame: pl.DataFrame,
    *,
    names: tuple[str, ...],
    name_column: str,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    aliases = {_normalized_name(name) for name in names}
    candidates = frame
    if scope is not None and "identity_scope" in candidates.columns:
        candidates = candidates.filter(pl.col("identity_scope") == scope)
    return [
        row for row in candidates.to_dicts() if _normalized_name(row.get(name_column)) in aliases
    ]


def _representative_validation(
    connection: duckdb.DuckDBPyConnection,
    employers: pl.DataFrame,
    institutions: pl.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    unresolved_targets: list[dict[str, Any]] = []
    trusted_legal_ids = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT legal_entity_id FROM legal_entities
            WHERE review_status IN (
                'AUTHORITATIVE_SOURCE_ID', 'DETERMINISTIC', 'HIGH_CONFIDENCE_AUTO',
                'MANUAL_OVERRIDE'
            )
            """
        ).fetchall()
    }
    reviewed_legal_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT legal_entity_id FROM legal_entities "
            "WHERE created_by = 'MANUAL_OVERRIDE' AND review_status = 'MANUAL_OVERRIDE'"
        ).fetchall()
    }
    for target in EMPLOYER_TARGETS:
        candidates = _candidate_rows(
            employers,
            names=target.aliases,
            name_column="organization_name",
            scope=target.scope,
        )
        if target.optional:
            candidates = [
                row for row in candidates if str(row.get("legal_entity_id")) in trusted_legal_ids
            ]
        reviewed_candidates = [
            row for row in candidates if str(row.get("legal_entity_id")) in reviewed_legal_ids
        ]
        if len(reviewed_candidates) == 1:
            primary = reviewed_candidates[0]
            excluded = [row for row in candidates if row is not primary]
            note = ""
            if excluded:
                excluded_ids = [str(row.get("organization_id")) for row in excluded]
                note = (
                    "Reviewed legal entity selected; additional exact-name candidates were "
                    f"excluded from its rating: {excluded_ids}"
                )
            rows.append(
                _validation_row(
                    connection,
                    primary,
                    target=target.label,
                    category="company",
                    status="VALIDATED",
                    ambiguity_note=note,
                )
            )
            for candidate in excluded:
                rows.append(
                    _validation_row(
                        connection,
                        candidate,
                        target=target.label,
                        category="company",
                        status="AMBIGUOUS_CANDIDATE",
                        ambiguity_note=note,
                    )
                )
            if excluded:
                unresolved_targets.append(
                    {
                        "record_type": "REPRESENTATIVE_VALIDATION",
                        "target": target.label,
                        "raw_name": " | ".join(target.aliases),
                        "match_status": "REVIEW_REQUIRED",
                        "review_status": "REVIEW_REQUIRED",
                        "resolution_reason": note,
                    }
                )
        elif len(candidates) == 1:
            rows.append(
                _validation_row(
                    connection,
                    candidates[0],
                    target=target.label,
                    category="company",
                    status="VALIDATED",
                    ambiguity_note="",
                )
            )
        elif candidates:
            candidate_ids = [str(row.get("organization_id")) for row in candidates]
            note = f"Multiple exact candidates retained without forcing a match: {candidate_ids}"
            for candidate in candidates:
                rows.append(
                    _validation_row(
                        connection,
                        candidate,
                        target=target.label,
                        category="company",
                        status="AMBIGUOUS_CANDIDATE",
                        ambiguity_note=note,
                    )
                )
            unresolved_targets.append(
                {
                    "record_type": "REPRESENTATIVE_VALIDATION",
                    "target": target.label,
                    "raw_name": " | ".join(target.aliases),
                    "match_status": "REVIEW_REQUIRED",
                    "review_status": "REVIEW_REQUIRED",
                    "resolution_reason": note,
                }
            )
        else:
            status = "OPTIONAL_NOT_CONFIDENTLY_RESOLVED" if target.optional else "UNRESOLVED"
            note = "No exact, scope-correct candidate was found; no match was forced."
            rows.append(
                _validation_row(
                    connection,
                    None,
                    target=target.label,
                    category="company",
                    status=status,
                    ambiguity_note=note,
                )
            )
            unresolved_targets.append(
                {
                    "record_type": "REPRESENTATIVE_VALIDATION",
                    "target": target.label,
                    "raw_name": " | ".join(target.aliases),
                    "match_status": status,
                    "review_status": status,
                    "resolution_reason": note,
                }
            )

    for target_name in INSTITUTION_TARGETS:
        candidates = _candidate_rows(
            institutions,
            names=(target_name,),
            name_column="official_name",
        )
        if len(candidates) == 1:
            rows.append(
                _validation_row(
                    connection,
                    candidates[0],
                    target=target_name,
                    category="institution",
                    status="VALIDATED",
                    ambiguity_note="",
                )
            )
        else:
            note = (
                "No exact institution identity was found; no match was forced."
                if not candidates
                else "Multiple exact institution identities were retained without forcing one."
            )
            candidate_rows = candidates or [None]
            for candidate in candidate_rows:
                rows.append(
                    _validation_row(
                        connection,
                        candidate,
                        target=target_name,
                        category="institution",
                        status="UNRESOLVED" if not candidates else "AMBIGUOUS_CANDIDATE",
                        ambiguity_note=note,
                    )
                )
            unresolved_targets.append(
                {
                    "record_type": "REPRESENTATIVE_VALIDATION",
                    "target": target_name,
                    "raw_name": target_name,
                    "match_status": "UNRESOLVED" if not candidates else "REVIEW_REQUIRED",
                    "review_status": "UNRESOLVED" if not candidates else "REVIEW_REQUIRED",
                    "resolution_reason": note,
                }
            )

    excluded_names = {
        token
        for target in EMPLOYER_TARGETS
        for alias in target.aliases
        for token in _normalized_name(alias).split()
        if len(token) >= 4
    }
    smaller = employers.filter(
        (pl.col("identity_scope") == "LEGAL_ENTITY")
        & pl.col("legal_entity_id").is_in(sorted(trusted_legal_ids))
        & ~pl.col("is_higher_education").fill_null(False)
        & (pl.col("overall_sponsorship_status") == "RATED")
        & (
            pl.col("weighted_relevant_lca_count").fill_null(0)
            + pl.col("weighted_relevant_perm_count").fill_null(0)
        ).is_between(2, 100)
    )
    smaller_candidates = [
        row
        for row in smaller.sort(
            ["overall_sponsorship_score", "organization_name"],
            descending=[True, False],
        ).to_dicts()
        if not excluded_names.intersection(_normalized_name(row.get("organization_name")).split())
    ][:2]
    for index in range(2):
        if index < len(smaller_candidates):
            candidate = smaller_candidates[index]
            label = f"Smaller technical employer {index + 1}: {candidate['organization_name']}"
            rows.append(
                _validation_row(
                    connection,
                    candidate,
                    target=label,
                    category="smaller_company",
                    status="SELECTED_REAL_RESULT",
                    ambiguity_note=(
                        "Selected deterministically from rated legal entities with 2-100 "
                        "weighted cases."
                    ),
                )
            )
        else:
            label = f"Smaller technical employer {index + 1}"
            note = "No qualifying real-data candidate was available."
            rows.append(
                _validation_row(
                    connection,
                    None,
                    target=label,
                    category="smaller_company",
                    status="UNRESOLVED",
                    ambiguity_note=note,
                )
            )
            unresolved_targets.append(
                {
                    "record_type": "REPRESENTATIVE_VALIDATION",
                    "target": label,
                    "match_status": "UNRESOLVED",
                    "review_status": "UNRESOLVED",
                    "resolution_reason": note,
                }
            )

    high_research = institutions.filter(
        (pl.col("research_scale_status") == "RATED")
        & (pl.col("research_scale_star_rating") >= 4)
        & (
            pl.col("overall_sponsorship_star_rating").is_null()
            | (pl.col("overall_sponsorship_star_rating") <= 2)
        )
    ).sort(
        ["research_scale_score", "overall_sponsorship_score", "official_name"],
        descending=[True, False, False],
        nulls_last=True,
    )
    high_row = high_research.row(0, named=True) if high_research.height else None
    if high_row is not None:
        rows.append(
            _validation_row(
                connection,
                high_row,
                target=f"High HERD / weak sponsorship contrast: {high_row['official_name']}",
                category="institution_contrast",
                status="SELECTED_REAL_RESULT",
                ambiguity_note="Selected by high Research Scale and weak/no observed sponsorship.",
            )
        )

    stronger = institutions.filter(
        (pl.col("overall_sponsorship_status") == "RATED")
        & (pl.col("research_scale_status") == "RATED")
        & (pl.col("research_scale_star_rating") <= 3)
        & (pl.col("overall_sponsorship_star_rating") >= 3)
    )
    if high_row is not None and high_row.get("overall_sponsorship_score") is not None:
        stronger = stronger.filter(
            pl.col("overall_sponsorship_score") > float(high_row["overall_sponsorship_score"])
        )
    stronger = stronger.sort(
        ["overall_sponsorship_score", "research_scale_score", "official_name"],
        descending=[True, False, False],
    )
    stronger_row = stronger.row(0, named=True) if stronger.height else None
    if stronger_row is not None:
        rows.append(
            _validation_row(
                connection,
                stronger_row,
                target=(
                    "Stronger sponsorship / lower research contrast: "
                    f"{stronger_row['official_name']}"
                ),
                category="institution_contrast",
                status="SELECTED_REAL_RESULT",
                ambiguity_note=(
                    "Selected by stronger observed sponsorship and lower Research Scale."
                ),
            )
        )

    for label, selected in (
        ("High HERD / weak sponsorship contrast", high_row),
        ("Stronger sponsorship / lower research contrast", stronger_row),
    ):
        if selected is None:
            note = "No institution met the conservative contrast criteria; no example was forced."
            rows.append(
                _validation_row(
                    connection,
                    None,
                    target=label,
                    category="institution_contrast",
                    status="UNRESOLVED",
                    ambiguity_note=note,
                )
            )
            unresolved_targets.append(
                {
                    "record_type": "REPRESENTATIVE_VALIDATION",
                    "target": label,
                    "match_status": "UNRESOLVED",
                    "review_status": "UNRESOLVED",
                    "resolution_reason": note,
                }
            )
    return rows, unresolved_targets


def _entity_review_rows(
    connection: duckdb.DuckDBPyConnection, target_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    fields = (
        "record_type",
        "target",
        "alias_id",
        "observation_id",
        "source_id",
        "raw_name",
        "city",
        "state",
        "postal_code",
        "candidate_legal_entity_id",
        "legal_entity_id",
        "parent_organization_id",
        "match_method",
        "match_score",
        "candidate_margin",
        "match_status",
        "review_status",
        "resolution_reason",
        "occurrence_count",
    )
    rows: list[dict[str, Any]] = []
    if _table_columns(connection, "vw_entity_review_queue"):
        frame = _frame(connection, "SELECT * FROM vw_entity_review_queue")
        for source in frame.to_dicts():
            row = {field: source.get(field) for field in fields}
            row.update(
                {
                    "record_type": "ENTITY_REVIEW_QUEUE",
                    "target": None,
                    "raw_name": source.get("alias_raw"),
                }
            )
            rows.append(row)
    rows.extend(target_rows)
    return [{field: row.get(field) for field in fields} for row in rows]


def _source_markdown(report: dict[str, Any], checks: list[AcceptanceCheck]) -> str:
    lines = [
        "# Product A source selection",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Source | Period | Complete/partial | File | Official URL | SHA-256 | "
        "Raw rows | Normalized rows | Validation | Raw verified |",
        "|---|---|---|---|---|---|---:|---:|---|---|",
    ]
    for row in report["artifacts"]:
        lines.append(
            "| {source_id} | {period} | {state} | {file_name} | [official artifact]({url}) | "
            "`{sha}` | {raw:,} | {normalized:,} | {validation} | {verified} |".format(
                source_id=row.get("source_id"),
                period=row.get("period"),
                state="partial" if row.get("is_partial_period") else "complete",
                file_name=str(row.get("file_name") or "").replace("|", "\\|"),
                url=row.get("download_url"),
                sha=row.get("sha256"),
                raw=int(row.get("raw_row_count") or 0),
                normalized=int(row.get("normalized_row_count") or 0),
                validation=row.get("validation_status"),
                verified="yes" if row.get("raw_checksum_verified") else "no",
            )
        )
    lines.extend(["", "## Selection checks", "", "| Check | Result | Evidence |", "|---|---|---|"])
    for check in checks:
        lines.append(
            f"| `{check.check_id}` | {'PASS' if check.passed else 'FAIL'} | "
            f"{check.evidence.replace('|', '\\|')} |"
        )
    return "\n".join(lines)


def _distribution_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Product A score distribution",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"Metric version: `{report['metric_version']}`  ",
        f"Score version: `{report['score_version']}`",
        "",
        "## Product counts",
        "",
        "| Measure | Count |",
        "|---|---:|",
    ]
    for key, value in report["counts"].items():
        lines.append(f"| {key.replace('_', ' ').title()} | {int(value):,} |")
    for population in ("employers", "institutions"):
        lines.extend(
            [
                "",
                f"## {population.title()}",
                "",
                "| Rating | Rated | No observed | Unrated | Stars |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for prefix, distribution in report[population].items():
            lines.append(
                f"| {prefix.replace('_', ' ').title()} | {distribution['rated']:,} | "
                f"{distribution['no_observed_history']:,} | {distribution['unrated']:,} | "
                f"{json.dumps(distribution['stars'], sort_keys=True)} |"
            )
    return "\n".join(lines)


def _validation_markdown(rows: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Product A representative validation",
        "",
        f"Generated: `{generated_at}`",
        "",
        "Ambiguous identities are listed as candidates and are not promoted to a forced match.",
        "",
        "| Target | Status | Organization | Scope | LCA | PERM | Initial approvals | H-1B | "
        "Green card | Overall | Research Scale |",
        "|---|---|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {target} | {status} | {name} | {scope} | {lca} | {perm} | {uscis} | {h1b} | "
            "{green} | {overall} | {research} |".format(
                target=str(row.get("target") or "").replace("|", "\\|"),
                status=row.get("selection_status") or "",
                name=str(row.get("organization_name") or "—").replace("|", "\\|"),
                scope=row.get("identity_scope") or "—",
                lca=row.get("relevant_certified_lca_count") or 0,
                perm=row.get("relevant_certified_perm_count") or 0,
                uscis=row.get("employer_level_h1b_initial_approvals") or 0,
                h1b=str(row.get("h1b_history_star_label") or "—").replace("|", "\\|"),
                green=str(row.get("green_card_history_star_label") or "—").replace("|", "\\|"),
                overall=str(row.get("overall_sponsorship_star_label") or "—").replace("|", "\\|"),
                research=str(row.get("research_scale_star_label") or "—").replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "Every populated row was inspected against raw employer names, legal and worksite "
            "locations, relevant titles/families/statuses, and source artifact IDs. Persisted "
            "sponsorship scores were independently recomputed without E-Verify, OPT, IPEDS, "
            "HERD, cap-exemption, or policy fields.",
        ]
    )
    return "\n".join(lines)


def _acceptance_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Product A acceptance",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"Result: **{'PASS' if report['passed'] else 'FAIL'}** "
        f"({report['passed_count']}/{report['check_count']} checks passed)",
        "",
        "| Check | Result | Requirement | Evidence |",
        "|---|---|---|---|",
    ]
    for check in report["checks"]:
        lines.append(
            f"| `{check['check_id']}` | {'PASS' if check['passed'] else 'FAIL'} | "
            f"{str(check['requirement']).replace('|', '\\|')} | "
            f"{str(check['evidence']).replace('|', '\\|')} |"
        )
    if report["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    return "\n".join(lines)


def run_acceptance(
    *,
    data_root: Path = Path("data"),
    database: Path = Path("db/immigration.duckdb"),
    output_root: Path = Path("outputs/reports/product-a"),
    manifest_path: Path = Path("outputs/manifests/source_artifacts.jsonl"),
) -> dict[str, Any]:
    """Inspect one materialized build and write the exact Product A report family."""

    data_root = data_root.resolve()
    database = database.resolve()
    requested_output_root = output_root.absolute()
    if requested_output_root.exists() and _is_link_or_junction(requested_output_root):
        raise AcceptanceInputError(
            f"Report output must not be a symbolic link or junction: {requested_output_root}"
        )
    output_root = requested_output_root.resolve()
    manifest_path = manifest_path.resolve()
    repository_root = data_root.parent
    _prepare_report_destination(output_root)
    if not database.is_file() or database.stat().st_size <= 0:
        raise AcceptanceInputError(f"DuckDB is missing or empty: {database}")
    processed = data_root / "processed"
    missing_parquet = [
        f"{name}.parquet"
        for name in REQUIRED_PROCESSED_TABLES
        if not (processed / f"{name}.parquet").is_file()
    ]
    if missing_parquet:
        raise AcceptanceInputError(f"Required processed Parquet is missing: {missing_parquet}")

    manifest_rows = _read_manifest(manifest_path)
    employers = pl.read_parquet(processed / "employer_metrics.parquet")
    institution_metrics = pl.read_parquet(processed / "institution_metrics.parquet")
    institution_identities = pl.read_parquet(processed / "institutions.parquet")
    herd_observations = pl.read_parquet(processed / "herd_observations.parquet")
    artifacts = pl.read_parquet(processed / "source_artifacts.parquet")
    if employers.is_empty() or institution_metrics.is_empty() or artifacts.is_empty():
        raise AcceptanceInputError(
            "Employer, institution, and selected-artifact inputs must be nonzero"
        )

    generated_at = datetime.now(UTC).isoformat()
    source_report, source_checks = _source_selection(
        artifacts,
        manifest_rows,
        repository_root=repository_root,
        manifest_path=manifest_path,
    )
    expected_lca_supersessions = source_report.pop("_retained_lca_supersessions", [])
    source_report["generated_at"] = generated_at
    source_report["passed"] = all(check.passed for check in source_checks)
    source_report["checks"] = [asdict(check) for check in source_checks]

    checks: list[AcceptanceCheck] = list(source_checks)
    warnings = [
        f"{row['source_id']} artifact {row['source_artifact_id']} retained a manifest warning."
        for row in source_report["validation_warnings"]
    ]
    with duckdb.connect(str(database), read_only=True) as connection:
        views = {
            str(row[0])
            for row in connection.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        }
        missing_views = sorted(REQUIRED_VIEWS - views)
        checks.append(
            AcceptanceCheck(
                "presentation_database",
                "The nonzero DuckDB exposes every Product A presentation view",
                not missing_views
                and int(_scalar(connection, "SELECT count(*) FROM vw_employer_explorer") or 0) > 0
                and int(_scalar(connection, "SELECT count(*) FROM vw_institution_explorer") or 0)
                > 0,
                f"database bytes={database.stat().st_size:,}; missing views={missing_views}.",
            )
        )

        lca_segment_date_errors = int(
            _scalar(
                connection,
                """
                WITH segment_rows AS (
                    SELECT
                        l.source_artifact_id,
                        a.fiscal_year,
                        a.coverage_start_quarter,
                        a.fiscal_quarter,
                        try_cast(l.decision_date AS DATE) AS decision_date
                    FROM lca_cases_resolved l
                    JOIN source_artifacts a USING (source_artifact_id)
                    WHERE a.source_id = 'dol_lca'
                      AND a.is_quarter_partition IS TRUE
                )
                SELECT count(*)
                FROM segment_rows
                WHERE decision_date IS NULL
                   OR coverage_start_quarter NOT BETWEEN 1 AND 4
                   OR fiscal_quarter NOT BETWEEN 1 AND 4
                   OR decision_date < CASE coverage_start_quarter
                        WHEN 1 THEN make_date(fiscal_year - 1, 10, 1)
                        WHEN 2 THEN make_date(fiscal_year, 1, 1)
                        WHEN 3 THEN make_date(fiscal_year, 4, 1)
                        WHEN 4 THEN make_date(fiscal_year, 7, 1)
                      END
                   OR decision_date > CASE fiscal_quarter
                        WHEN 1 THEN make_date(fiscal_year - 1, 12, 31)
                        WHEN 2 THEN make_date(fiscal_year, 3, 31)
                        WHEN 3 THEN make_date(fiscal_year, 6, 30)
                        WHEN 4 THEN make_date(fiscal_year, 9, 30)
                      END
                """,
            )
            or 0
        )
        lca_global_duplicate_case_ids = int(
            _scalar(
                connection,
                """
                SELECT count(*)
                FROM (
                    SELECT l.case_id
                    FROM lca_cases_resolved l
                    GROUP BY l.case_id
                    HAVING count(*) > 1
                )
                """,
            )
            or 0
        )
        checks.append(
            AcceptanceCheck(
                "lca_partition_integrity",
                "Completed LCA coverage segments are date-bounded and resolved case IDs are "
                "globally unique",
                lca_segment_date_errors == 0 and lca_global_duplicate_case_ids == 0,
                f"out-of-segment rows={lca_segment_date_errors}; "
                f"global duplicate case IDs={lca_global_duplicate_case_ids}.",
            )
        )

        supersession_schema = {
            "fiscal_year": pl.Int64,
            "superseding_fiscal_year": pl.Int64,
            "case_id": pl.String,
            "superseded_source_artifact_id": pl.String,
            "superseded_source_row_number": pl.Int64,
            "retained_source_artifact_id": pl.String,
            "retained_source_row_number": pl.Int64,
        }
        expected_supersessions = pl.DataFrame(
            expected_lca_supersessions,
            schema=supersession_schema,
        )
        connection.register("_expected_lca_supersessions", expected_supersessions)
        missing_retained_states = int(
            _scalar(
                connection,
                """
                SELECT count(*)
                FROM _expected_lca_supersessions e
                LEFT JOIN lca_cases_resolved l
                  ON l.fiscal_year = e.superseding_fiscal_year
                 AND l.case_id = e.case_id
                 AND l.source_artifact_id = e.retained_source_artifact_id
                 AND l.source_row_number = e.retained_source_row_number
                WHERE l.case_id IS NULL
                """,
            )
            or 0
        )
        superseded_states_present = int(
            _scalar(
                connection,
                """
                SELECT count(*)
                FROM _expected_lca_supersessions e
                JOIN lca_cases_resolved l
                  ON l.fiscal_year = e.fiscal_year
                 AND l.case_id = e.case_id
                 AND l.source_artifact_id = e.superseded_source_artifact_id
                 AND l.source_row_number = e.superseded_source_row_number
                """,
            )
            or 0
        )
        processed_duplicate_case_ids = int(
            _scalar(
                connection,
                """
                SELECT count(*)
                FROM (
                    SELECT case_id
                    FROM lca_cases_resolved
                    GROUP BY case_id
                    HAVING count(*) > 1
                )
                """,
            )
            or 0
        )
        connection.unregister("_expected_lca_supersessions")
        checks.append(
            AcceptanceCheck(
                "lca_supersession_materialization",
                "Resolved LCA evidence retains only each validated latest case state",
                missing_retained_states == 0
                and superseded_states_present == 0
                and processed_duplicate_case_ids == 0,
                f"expected supersessions={expected_supersessions.height}; missing retained "
                f"states={missing_retained_states}; superseded states still present="
                f"{superseded_states_present}; global duplicate case IDs="
                f"{processed_duplicate_case_ids}.",
            )
        )

        parquet_db_mismatches: dict[str, tuple[int, int]] = {}
        for table in REQUIRED_PROCESSED_TABLES:
            parquet_count = (
                pl.scan_parquet(processed / f"{table}.parquet").select(pl.len()).collect().item()
            )
            database_count = int(_scalar(connection, f"SELECT count(*) FROM {table}") or 0)
            if parquet_count != database_count:
                parquet_db_mismatches[table] = (parquet_count, database_count)
        checks.append(
            AcceptanceCheck(
                "database_parquet_parity",
                "DuckDB tables exactly match the processed Parquet build",
                not parquet_db_mismatches,
                f"row-count mismatches={parquet_db_mismatches}.",
            )
        )

        def expected_candidate_conflicts(
            *, source_id: str, evidence_table: str, evidence_predicate: str
        ) -> set[str]:
            rows = connection.execute(
                f"""
                WITH review_aliases AS (
                    SELECT DISTINCT
                        legal_entity_id AS unresolved_legal_entity_id,
                        candidate_legal_entity_id
                    FROM entity_aliases
                    WHERE source_id = ?
                      AND legal_entity_id IS NOT NULL
                      AND candidate_legal_entity_id IS NOT NULL
                      AND legal_entity_id <> candidate_legal_entity_id
                      AND upper(trim(cast(match_status AS VARCHAR))) = 'REVIEW_REQUIRED'
                      AND upper(trim(cast(review_status AS VARCHAR))) = 'REVIEW_REQUIRED'
                ),
                qualifying_entities AS (
                    SELECT DISTINCT legal_entity_id AS unresolved_legal_entity_id
                    FROM {evidence_table}
                    WHERE legal_entity_id IS NOT NULL AND ({evidence_predicate})
                ),
                candidate_entities AS (
                    SELECT DISTINCT r.candidate_legal_entity_id
                    FROM review_aliases r
                    JOIN qualifying_entities q USING (unresolved_legal_entity_id)
                ),
                expected AS (
                    SELECT candidate_legal_entity_id AS organization_id
                    FROM candidate_entities
                    UNION
                    SELECT l.parent_organization_id AS organization_id
                    FROM candidate_entities c
                    JOIN legal_entities l
                      ON l.legal_entity_id = c.candidate_legal_entity_id
                    JOIN parent_organizations p USING (parent_organization_id)
                    WHERE l.parent_organization_id IS NOT NULL
                      AND l.review_status IN (
                          'DETERMINISTIC', 'HIGH_CONFIDENCE_AUTO', 'MANUAL_OVERRIDE'
                      )
                      AND p.review_status IN (
                          'DETERMINISTIC', 'HIGH_CONFIDENCE_AUTO', 'MANUAL_OVERRIDE'
                      )
                )
                SELECT organization_id FROM expected ORDER BY organization_id
                """,
                [source_id],
            ).fetchall()
            return {str(row[0]) for row in rows}

        expected_h1b_conflicts = expected_candidate_conflicts(
            source_id="dol_lca",
            evidence_table="lca_cases_resolved",
            evidence_predicate=(
                "technical_role IS TRUE "
                "AND upper(trim(coalesce(visa_class, ''))) = 'H-1B' "
                f"AND {_CANONICAL_CASE_STATUS_SQL} IN "
                "('CERTIFIED', 'CERTIFIED-WITHDRAWN')"
            ),
        )
        expected_perm_conflicts = expected_candidate_conflicts(
            source_id="dol_perm",
            evidence_table="perm_cases_resolved",
            evidence_predicate=(
                "technical_role IS TRUE "
                f"AND {_CANONICAL_CASE_STATUS_SQL} IN "
                "('CERTIFIED', 'CERTIFIED-EXPIRED')"
            ),
        )
        actual_h1b_conflicts = {
            str(row[0])
            for row in connection.execute(
                "SELECT organization_id FROM employer_metrics "
                "WHERE has_unresolved_h1b_candidate_evidence IS TRUE"
            ).fetchall()
        }
        actual_perm_conflicts = {
            str(row[0])
            for row in connection.execute(
                "SELECT organization_id FROM employer_metrics "
                "WHERE has_unresolved_perm_candidate_evidence IS TRUE"
            ).fetchall()
        }
        h1b_conflict_status_errors = int(
            _scalar(
                connection,
                "SELECT count(*) FROM employer_metrics "
                "WHERE has_unresolved_h1b_candidate_evidence IS TRUE "
                "AND ((entity_resolution_valid IS TRUE "
                "AND weighted_relevant_lca_count > 0 AND ("
                "h1b_entity_coverage_state <> 'PARTIAL_ENTITY_COVERAGE' "
                "OR h1b_history_status <> 'RATED' OR position("
                "'Rating is based on confirmed records. Additional "
                "ambiguous records were excluded.' "
                "IN h1b_history_explanation) = 0)) OR (("
                "entity_resolution_valid IS NOT TRUE OR weighted_relevant_lca_count = 0) AND ("
                "h1b_entity_coverage_state <> 'UNRESOLVED_IDENTITY' "
                "OR h1b_history_status <> 'UNRATED')))",
            )
            or 0
        )
        perm_conflict_status_errors = int(
            _scalar(
                connection,
                "SELECT count(*) FROM employer_metrics "
                "WHERE has_unresolved_perm_candidate_evidence IS TRUE "
                "AND ((entity_resolution_valid IS TRUE "
                "AND weighted_relevant_perm_count > 0 AND ("
                "perm_entity_coverage_state <> 'PARTIAL_ENTITY_COVERAGE' "
                "OR green_card_history_status <> 'RATED' OR position("
                "'Rating is based on confirmed records. Additional "
                "ambiguous records were excluded.' "
                "IN green_card_history_explanation) = 0)) OR (("
                "entity_resolution_valid IS NOT TRUE OR weighted_relevant_perm_count = 0) AND ("
                "perm_entity_coverage_state <> 'UNRESOLVED_IDENTITY' "
                "OR green_card_history_status <> 'UNRATED')))",
            )
            or 0
        )
        coverage_state_errors = int(
            _scalar(
                connection,
                """
                SELECT count(*) FROM employer_metrics
                WHERE h1b_entity_coverage_state <> CASE
                        WHEN entity_resolution_valid IS NOT TRUE THEN 'UNRESOLVED_IDENTITY'
                        WHEN has_unresolved_h1b_candidate_evidence IS TRUE
                            AND weighted_relevant_lca_count > 0
                            THEN 'PARTIAL_ENTITY_COVERAGE'
                        WHEN has_unresolved_h1b_candidate_evidence IS TRUE
                            THEN 'UNRESOLVED_IDENTITY'
                        ELSE 'COMPLETE_ENTITY_COVERAGE' END
                   OR perm_entity_coverage_state <> CASE
                        WHEN entity_resolution_valid IS NOT TRUE THEN 'UNRESOLVED_IDENTITY'
                        WHEN has_unresolved_perm_candidate_evidence IS TRUE
                            AND weighted_relevant_perm_count > 0
                            THEN 'PARTIAL_ENTITY_COVERAGE'
                        WHEN has_unresolved_perm_candidate_evidence IS TRUE
                            THEN 'UNRESOLVED_IDENTITY'
                        ELSE 'COMPLETE_ENTITY_COVERAGE' END
                   OR h1b_entity_resolution_valid <>
                        (h1b_entity_coverage_state <> 'UNRESOLVED_IDENTITY')
                   OR perm_entity_resolution_valid <>
                        (perm_entity_coverage_state <> 'UNRESOLVED_IDENTITY')
                   OR entity_coverage_state <> CASE
                        WHEN h1b_entity_coverage_state = 'UNRESOLVED_IDENTITY'
                          OR perm_entity_coverage_state = 'UNRESOLVED_IDENTITY'
                            THEN 'UNRESOLVED_IDENTITY'
                        WHEN h1b_entity_coverage_state = 'PARTIAL_ENTITY_COVERAGE'
                          OR perm_entity_coverage_state = 'PARTIAL_ENTITY_COVERAGE'
                            THEN 'PARTIAL_ENTITY_COVERAGE'
                        ELSE 'COMPLETE_ENTITY_COVERAGE' END
                """,
            )
            or 0
        )
        checks.append(
            AcceptanceCheck(
                "entity_coverage_semantics",
                "Confirmed evidence remains scoreable with partial disclosure while unresolved "
                "identity remains Unrated and never becomes a false zero",
                expected_h1b_conflicts == actual_h1b_conflicts
                and expected_perm_conflicts == actual_perm_conflicts
                and h1b_conflict_status_errors == 0
                and perm_conflict_status_errors == 0
                and coverage_state_errors == 0,
                f"H-1B expected/actual={len(expected_h1b_conflicts)}/"
                f"{len(actual_h1b_conflicts)}; PERM expected/actual="
                f"{len(expected_perm_conflicts)}/{len(actual_perm_conflicts)}; "
                f"status errors={h1b_conflict_status_errors}/{perm_conflict_status_errors}; "
                f"coverage-state errors={coverage_state_errors}.",
            )
        )

        metric_versions = sorted(
            str(value) for value in employers["metric_version"].drop_nulls().unique().to_list()
        )
        score_versions = sorted(
            str(value) for value in employers["score_version"].drop_nulls().unique().to_list()
        )
        institution_metric_versions = sorted(
            str(value)
            for value in institution_metrics["metric_version"].drop_nulls().unique().to_list()
        )
        institution_score_versions = sorted(
            str(value)
            for value in institution_metrics["score_version"].drop_nulls().unique().to_list()
        )
        versions_ok = (
            metric_versions == [EXPECTED_METRIC_VERSION]
            and institution_metric_versions == [EXPECTED_METRIC_VERSION]
            and score_versions == [EXPECTED_SCORE_VERSION]
            and institution_score_versions == [EXPECTED_SCORE_VERSION]
        )
        checks.append(
            AcceptanceCheck(
                "active_product_versions",
                "Only Product A metric and score versions are active",
                versions_ok,
                f"employer={metric_versions}/{score_versions}; institution="
                f"{institution_metric_versions}/{institution_score_versions}.",
            )
        )

        ipeds_identity_columns = {
            "characteristics_source_artifact_id",
            "characteristics_year",
            "directory_year",
            "is_finalized",
            "release_status",
            "source_artifact_id",
        }
        missing_ipeds_identity_columns = sorted(
            ipeds_identity_columns - set(institution_identities.columns)
        )
        selected_ipeds = artifacts.filter(pl.col("source_id") == "ipeds")
        selected_ipeds_hd_ids = set(
            selected_ipeds.filter(pl.col("file_name").str.to_uppercase().str.starts_with("HD"))[
                "source_artifact_id"
            ].to_list()
        )
        selected_ipeds_ic_ids = set(
            selected_ipeds.filter(pl.col("file_name").str.to_uppercase().str.starts_with("IC"))[
                "source_artifact_id"
            ].to_list()
        )
        if missing_ipeds_identity_columns:
            ipeds_identity_errors = institution_identities.height
            ipeds_ic_matched_count = 0
            ipeds_hd_only_count = 0
            observed_ipeds_hd_ids: set[str] = set()
            observed_ipeds_ic_ids: set[str] = set()
        else:
            has_characteristics_id = pl.col("characteristics_source_artifact_id").is_not_null()
            has_characteristics_year = pl.col("characteristics_year").is_not_null()
            ipeds_identity_errors = institution_identities.filter(
                ~pl.col("is_finalized").fill_null(False)
                | (pl.col("release_status") != "FINAL")
                | pl.col("source_artifact_id").is_null()
                | pl.col("directory_year").is_null()
                | (has_characteristics_id != has_characteristics_year)
                | (
                    has_characteristics_id
                    & (pl.col("characteristics_year") != pl.col("directory_year")).fill_null(False)
                )
            ).height
            ipeds_ic_matched_count = institution_identities.filter(
                has_characteristics_id & has_characteristics_year
            ).height
            ipeds_hd_only_count = institution_identities.filter(
                ~has_characteristics_id & ~has_characteristics_year
            ).height
            observed_ipeds_hd_ids = set(
                institution_identities["source_artifact_id"].drop_nulls().to_list()
            )
            observed_ipeds_ic_ids = set(
                institution_identities["characteristics_source_artifact_id"].drop_nulls().to_list()
            )
        checks.append(
            AcceptanceCheck(
                "ipeds_finalized_identity_contract",
                "Finalized HD identities retain same-year IC provenance when available",
                not missing_ipeds_identity_columns
                and ipeds_identity_errors == 0
                and observed_ipeds_hd_ids == selected_ipeds_hd_ids
                and observed_ipeds_ic_ids == selected_ipeds_ic_ids,
                f"HD rows={institution_identities.height}; IC matched={ipeds_ic_matched_count}; "
                f"HD-only={ipeds_hd_only_count}; missing columns={missing_ipeds_identity_columns}; "
                f"row errors={ipeds_identity_errors}; selected HD IDs="
                f"{sorted(selected_ipeds_hd_ids)}; observed HD IDs="
                f"{sorted(observed_ipeds_hd_ids)}; selected IC IDs="
                f"{sorted(selected_ipeds_ic_ids)}; observed IC IDs="
                f"{sorted(observed_ipeds_ic_ids)}.",
            )
        )

        herd_key_columns = {"inst_id", "survey_year"}
        if herd_key_columns <= set(herd_observations.columns):
            duplicate_herd_years = (
                herd_observations.group_by("inst_id", "survey_year")
                .len()
                .filter(pl.col("len") > 1)
                .height
            )
        else:
            duplicate_herd_years = herd_observations.height
        checks.append(
            AcceptanceCheck(
                "herd_institution_year_deduplication",
                "HERD full and short observations never double-count an institution-year",
                herd_key_columns <= set(herd_observations.columns) and duplicate_herd_years == 0,
                f"duplicate institution-years={duplicate_herd_years}.",
            )
        )

        formula, recomputed_caps = _formula_contract(employers)
        formula_ok = all(value == 0 for value in formula.values())
        checks.append(
            AcceptanceCheck(
                "deterministic_score_formula",
                "Nearest-P95 caps, scores, whole stars, zero/Unrated states, and labels match "
                "Product A",
                formula_ok,
                f"independent formula audit={formula}; recomputed caps={recomputed_caps}.",
            )
        )
        checks.append(
            AcceptanceCheck(
                "supplemental_score_independence",
                "Sponsorship scores are reproducible from DOL/USCIS ingredients only",
                formula.get("formula_mismatches", 1) == 0,
                "Independent recomputation used no E-Verify, OPT, IPEDS, HERD, cap-exemption, "
                f"or policy fields; mismatches={formula.get('formula_mismatches')}.",
            )
        )

        lca_mismatches = _program_aggregate_mismatches(connection, program="lca")
        perm_mismatches = _program_aggregate_mismatches(connection, program="perm")
        uscis_mismatches = _uscis_aggregate_mismatches(connection)
        h1b1_e3_count = int(
            _scalar(
                connection,
                """
                SELECT count(*) FROM lca_cases_resolved
                WHERE upper(trim(coalesce(visa_class, ''))) IN ('H-1B1', 'E-3')
                   OR upper(trim(coalesce(visa_class, ''))) LIKE 'H-1B1 %'
                   OR upper(trim(coalesce(visa_class, ''))) LIKE 'E-3 %'
                """,
            )
            or 0
        )
        checks.append(
            AcceptanceCheck(
                "h1b_only_status_weighting",
                "Only technical H-1B certified/certified-withdrawn LCA rows affect H-1B ratings",
                lca_mismatches == 0 and h1b1_e3_count > 0,
                f"aggregate mismatches={lca_mismatches}; {h1b1_e3_count:,} H-1B1/E-3 rows "
                "remain queryable but excluded.",
            )
        )
        checks.append(
            AcceptanceCheck(
                "perm_status_weighting",
                "Only technical certified/certified-expired PERM rows receive Product A weight",
                perm_mismatches == 0,
                f"legal/parent aggregate and complete-year mismatches={perm_mismatches}.",
            )
        )
        checks.append(
            AcceptanceCheck(
                "uscis_employer_level_aggregation",
                "Employer-level H-1B initial approvals reconcile to USCIS evidence",
                uscis_mismatches == 0,
                f"legal/parent aggregate mismatches={uscis_mismatches}; label is employer-level.",
            )
        )

        scope_collisions = int(
            _scalar(
                connection,
                """
                SELECT count(*) FROM legal_entities
                WHERE parent_organization_id IS NOT NULL
                  AND legal_entity_id = parent_organization_id
                """,
            )
            or 0
        )
        evidence_identity_errors = 0
        for table in ("lca_cases_resolved", "perm_cases_resolved", "h1b_petitions_resolved"):
            evidence_identity_errors += int(
                _scalar(
                    connection,
                    f"""
                    SELECT count(*) FROM {table}
                    WHERE organization_id IS NOT NULL
                      AND legal_entity_id IS DISTINCT FROM organization_id
                    """,
                )
                or 0
            )
        employer_scopes = set(employers["identity_scope"].drop_nulls().to_list())
        checks.append(
            AcceptanceCheck(
                "legal_parent_separation",
                "Immigration evidence stays on legal entities and parent rollups remain separate",
                scope_collisions == 0
                and evidence_identity_errors == 0
                and employer_scopes <= {"LEGAL_ENTITY", "PARENT_ROLLUP"}
                and "LEGAL_ENTITY" in employer_scopes,
                f"ID collisions={scope_collisions}; evidence attachment errors="
                f"{evidence_identity_errors}; scopes={sorted(employer_scopes)}.",
            )
        )

        institution_comparison_columns = (
            "h1b_history_score",
            "h1b_history_status",
            "h1b_history_star_rating",
            "green_card_history_score",
            "green_card_history_status",
            "green_card_history_star_rating",
            "overall_sponsorship_score",
            "overall_sponsorship_status",
            "overall_sponsorship_star_rating",
        )
        comparisons = " OR ".join(
            f"i.{column} IS DISTINCT FROM e.{column}" for column in institution_comparison_columns
        )
        institution_score_mismatches = int(
            _scalar(
                connection,
                f"""
                SELECT count(*) FROM institution_metrics i
                JOIN employer_metrics e USING (organization_id)
                WHERE e.identity_scope = 'LEGAL_ENTITY' AND ({comparisons})
                """,
            )
            or 0
        )
        research_contract_errors = institution_metrics.filter(
            (
                pl.col("research_scale_star_rating").is_not_null()
                & ~pl.col("research_scale_star_rating").is_between(1, 5)
            )
            | (
                (pl.col("research_scale_status") == "RATED")
                & ~pl.col("research_scale_star_label").str.contains("out of 5 stars", literal=True)
            )
        ).height
        checks.append(
            AcceptanceCheck(
                "research_scale_independence",
                "HERD Research Scale is separate and cannot alter institution sponsorship ratings",
                institution_score_mismatches == 0 and research_contract_errors == 0,
                f"institution/employer sponsorship mismatches={institution_score_mismatches}; "
                f"Research Scale contract errors={research_contract_errors}.",
            )
        )

        health = pl.read_parquet(processed / "data_health.parquet")
        partial_sources = artifacts.filter(pl.col("is_partial_period")).height
        partial_warnings = (
            health.filter(
                pl.col("has_partial_period").fill_null(False)
                & pl.col("freshness_warning").is_not_null()
                & pl.col("freshness_warning").str.contains("Partial FY", literal=True)
            ).height
            if {"has_partial_period", "freshness_warning"} <= set(health.columns)
            else 0
        )
        checks.append(
            AcceptanceCheck(
                "partial_period_warning",
                "Every build with current partial evidence exposes a non-annualized warning",
                partial_sources == 0 or partial_warnings > 0,
                f"partial artifacts={partial_sources}; warning rows={partial_warnings}.",
            )
        )

        quality_failure_count = (
            int(
                _scalar(
                    connection,
                    "SELECT count(*) FROM quality_checks "
                    "WHERE critical IS TRUE AND status = 'FAIL'",
                )
                or 0
            )
            if {"critical", "status"} <= _table_columns(connection, "quality_checks")
            else 1
        )
        checks.append(
            AcceptanceCheck(
                "critical_quality_gates",
                "The Product A quality report has no critical failure",
                quality_failure_count == 0,
                f"critical quality failures={quality_failure_count}.",
            )
        )

        validation_rows, unresolved_targets = _representative_validation(
            connection, employers, institution_metrics
        )
        covered_targets = {str(row["target"]) for row in validation_rows}
        fixed_targets = {target.label for target in EMPLOYER_TARGETS} | set(INSTITUTION_TARGETS)
        smaller_count = sum(
            row["category"] == "smaller_company"
            and row["selection_status"] == "SELECTED_REAL_RESULT"
            for row in validation_rows
        )
        contrast_count = sum(
            row["category"] == "institution_contrast"
            and row["selection_status"] == "SELECTED_REAL_RESULT"
            for row in validation_rows
        )
        validation_complete = (
            fixed_targets <= covered_targets and smaller_count >= 2 and contrast_count >= 2
        )
        checks.append(
            AcceptanceCheck(
                "representative_validation",
                "Named, smaller-employer, and contrasting-institution validation is "
                "recorded honestly with two real selected results in each representative group",
                validation_complete,
                f"fixed targets={len(fixed_targets & covered_targets)}/{len(fixed_targets)}; "
                f"selected-real smaller rows={smaller_count}; selected-real contrast rows="
                f"{contrast_count}; unresolved "
                f"targets={len(unresolved_targets)}.",
            )
        )

        unresolved_rows = _entity_review_rows(connection, unresolved_targets)

        legal_entity_count = int(_scalar(connection, "SELECT count(*) FROM legal_entities") or 0)
        parent_count = int(_scalar(connection, "SELECT count(*) FROM parent_organizations") or 0)
        institution_count = institution_metrics.height
        relevant_lca = int(
            _scalar(
                connection,
                f"""
                SELECT count(*) FROM lca_cases_resolved
                WHERE technical_role IS TRUE
                  AND upper(trim(coalesce(visa_class, ''))) = 'H-1B'
                  AND {_CANONICAL_CASE_STATUS_SQL} IN
                      ('CERTIFIED', 'CERTIFIED-WITHDRAWN')
                """,
            )
            or 0
        )
        relevant_perm = int(
            _scalar(
                connection,
                f"""
                SELECT count(*) FROM perm_cases_resolved
                WHERE technical_role IS TRUE
                  AND {_CANONICAL_CASE_STATUS_SQL} IN
                      ('CERTIFIED', 'CERTIFIED-EXPIRED')
                """,
            )
            or 0
        )
        counts = {
            "employer_rows": employers.height,
            "legal_entities": legal_entity_count,
            "parent_organizations": parent_count,
            "institutions": institution_count,
            "relevant_h1b_lca_rows": relevant_lca,
            "relevant_perm_rows": relevant_perm,
            "h1b1_e3_queryable_rows": h1b1_e3_count,
            "unresolved_entity_rows": len(unresolved_rows),
        }
        checks.append(
            AcceptanceCheck(
                "nonzero_product_outputs",
                "Employer, legal-entity, institution, and qualifying evidence outputs are nonzero",
                all(
                    counts[key] > 0
                    for key in (
                        "employer_rows",
                        "legal_entities",
                        "institutions",
                        "relevant_h1b_lca_rows",
                        "relevant_perm_rows",
                    )
                ),
                json.dumps(counts, sort_keys=True),
            )
        )

    distribution_report = {
        "generated_at": generated_at,
        "metric_version": metric_versions[0] if len(metric_versions) == 1 else metric_versions,
        "score_version": score_versions[0] if len(score_versions) == 1 else score_versions,
        "counts": counts,
        "score_metadata": {
            column: sorted(
                _json_safe(value) for value in employers[column].drop_nulls().unique().to_list()
            )
            for column in (
                "score_count_percentile_cap",
                "h1b_volume_p95_cap",
                "uscis_initial_approvals_p95_cap",
                "green_card_volume_p95_cap",
            )
            if column in employers.columns
        },
        "independently_recomputed_caps": recomputed_caps,
        "employers": {
            prefix: _rating_distribution(employers, prefix)
            for prefix in (
                "h1b_history",
                "green_card_history",
                "overall_sponsorship",
            )
        },
        "institutions": {
            prefix: _rating_distribution(institution_metrics, prefix)
            for prefix in (
                "h1b_history",
                "green_card_history",
                "overall_sponsorship",
                "research_scale",
            )
        },
        "formula_audit": formula,
    }
    validation_report = {
        "generated_at": generated_at,
        "target_count": len({row["target"] for row in validation_rows}),
        "row_count": len(validation_rows),
        "status_counts": {
            str(row["selection_status"]): sum(
                item["selection_status"] == row["selection_status"] for item in validation_rows
            )
            for row in validation_rows
        },
        "rows": validation_rows,
    }

    passed = all(check.passed for check in checks)
    acceptance_report = {
        "generated_at": generated_at,
        "passed": passed,
        "passed_count": sum(check.passed for check in checks),
        "failed_count": sum(not check.passed for check in checks),
        "check_count": len(checks),
        "inputs": {
            "data_root": str(data_root),
            "database": str(database),
            "database_bytes": database.stat().st_size,
            "manifest": str(manifest_path),
        },
        "report_files": list(REPORT_FILES),
        "product_results": counts,
        "validation": {
            "target_count": validation_report["target_count"],
            "row_count": validation_report["row_count"],
            "status_counts": validation_report["status_counts"],
        },
        "warnings": warnings,
        "checks": [asdict(check) for check in checks],
    }

    report_root = _temporary_report_directory(output_root)
    try:
        source_json = dict(source_report)
        source_json["checks"] = [asdict(check) for check in source_checks]
        write_json_atomic(report_root / "source-selection.json", _json_safe(source_json))
        _write_text_atomic(
            report_root / "source-selection.md", _source_markdown(source_json, source_checks)
        )
        write_json_atomic(report_root / "score-distribution.json", _json_safe(distribution_report))
        _write_text_atomic(
            report_root / "score-distribution.md", _distribution_markdown(distribution_report)
        )
        _write_csv_atomic(report_root / "validation.csv", validation_rows, VALIDATION_FIELDS)
        _write_text_atomic(
            report_root / "validation.md", _validation_markdown(validation_rows, generated_at)
        )
        unresolved_fields = (
            "record_type",
            "target",
            "alias_id",
            "observation_id",
            "source_id",
            "raw_name",
            "city",
            "state",
            "postal_code",
            "candidate_legal_entity_id",
            "legal_entity_id",
            "parent_organization_id",
            "match_method",
            "match_score",
            "candidate_margin",
            "match_status",
            "review_status",
            "resolution_reason",
            "occurrence_count",
        )
        _write_csv_atomic(
            report_root / "unresolved-entities.csv", unresolved_rows, unresolved_fields
        )
        write_json_atomic(report_root / "acceptance.json", _json_safe(acceptance_report))
        _write_text_atomic(report_root / "acceptance.md", _acceptance_markdown(acceptance_report))
        _publish_report_directory(report_root, output_root)
    except Exception:
        _remove_flat_report_directory(report_root)
        raise
    return acceptance_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("db/immigration.duckdb"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs/reports/product-a"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("outputs/manifests/source_artifacts.jsonl"),
    )
    arguments = parser.parse_args(argv)
    try:
        report = run_acceptance(
            data_root=arguments.data_root,
            database=arguments.database,
            output_root=arguments.output_root,
            manifest_path=arguments.manifest,
        )
    except AcceptanceInputError as error:
        print(f"Product A acceptance could not run: {error}")
        return 2
    print(json.dumps(_json_safe(report), indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
