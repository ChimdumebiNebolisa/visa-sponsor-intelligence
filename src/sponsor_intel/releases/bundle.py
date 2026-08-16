"""Package a quality-approved build as private GitHub Release assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from sponsor_intel.database.builder import REQUIRED_VIEWS
from sponsor_intel.quality.report import (
    EXPECTED_METRIC_VERSION,
    EXPECTED_SCORE_VERSION,
    REQUIRED_TABLE_COLUMNS,
)

_REQUIRED_PRODUCT_A_TABLES = {
    "data_health",
    "employer_metrics",
    "entity_aliases",
    "everify_observations",
    "h1b_petitions_resolved",
    "herd_observations",
    "institution_metrics",
    "institutions",
    "lca_cases_resolved",
    "legal_entities",
    "opt_employer_observations",
    "parent_organizations",
    "perm_cases_resolved",
    "quality_checks",
    "source_artifacts",
}
_REQUIRED_PRODUCT_A_VIEWS = set(REQUIRED_VIEWS) - {
    "vw_policy_evidence",
    "vw_policy_review_queue",
}
_METRIC_RELATIONS = {
    "employer_metrics": (
        "vw_employer_explorer",
        "employer_metrics.parquet",
        "employer_count",
    ),
    "institution_metrics": (
        "vw_institution_explorer",
        "institution_metrics.parquet",
        "institution_count",
    ),
}
_INACTIVE_SCORE_SIDECARS = {
    "employer_scores_v1.parquet",
    "employer_scores_v2.parquet",
    "institution_scores_v1.parquet",
}


@dataclass(frozen=True, slots=True)
class ReleaseBundle:
    """Paths produced for a private data release."""

    release_root: Path
    assets: tuple[Path, ...]
    checksums_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _replace_text(value: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _archive(
    target: Path,
    files: Iterable[tuple[Path, Path]],
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for path, archive_path in sorted(files, key=lambda item: item[1].as_posix()):
                archive.write(path, archive_path.as_posix())
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _tree_files(root: Path, repository_root: Path) -> list[tuple[Path, Path]]:
    if not root.is_dir():
        return []
    return [(path, path.relative_to(repository_root)) for path in root.rglob("*") if path.is_file()]


def _processed_release_files(
    processed_root: Path, repository_root: Path
) -> list[tuple[Path, Path]]:
    """Exclude only superseded score sidecars from Product A processed assets."""

    return [
        (path, archive_path)
        for path, archive_path in _tree_files(processed_root, repository_root)
        if path.parent != processed_root or path.name not in _INACTIVE_SCORE_SIDECARS
    ]


def _active_source_artifact_ids(processed_root: Path) -> set[str]:
    artifacts_path = processed_root / "source_artifacts.parquet"
    if not artifacts_path.is_file():
        raise ValueError("Processed active source-artifact provenance is required for packaging")
    artifacts = pl.read_parquet(artifacts_path, columns=["source_artifact_id"])
    artifact_ids = {
        str(value).strip()
        for value in artifacts.get_column("source_artifact_id").drop_nulls().to_list()
        if str(value).strip()
    }
    if not artifact_ids or len(artifact_ids) != artifacts.height:
        raise ValueError("Processed active source-artifact IDs must be non-empty and unique")
    return artifact_ids


def _active_state_files(
    root: Path,
    repository_root: Path,
    active_artifact_ids: set[str],
) -> list[tuple[Path, Path]]:
    """Keep core state plus only source Parquets selected for the approved build."""

    source_root = root / "sources"
    selected: list[tuple[Path, Path]] = []
    for path, archive_path in _tree_files(root, repository_root):
        try:
            path.relative_to(source_root)
        except ValueError:
            selected.append((path, archive_path))
            continue
        if path.suffix.lower() == ".parquet" and path.stem in active_artifact_ids:
            selected.append((path, archive_path))
    return selected


def _positive_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _validate_quality_metadata(
    report: dict[str, object], metadata: dict[str, object]
) -> tuple[str, dict[str, int]]:
    """Require one internally consistent Product A quality/build identity."""

    build_id = report.get("build_id")
    generated_at = report.get("generated_at")
    manifest_sha256 = report.get("manifest_sha256")
    if (
        report.get("passed") is not True
        or report.get("critical_failure_count") != 0
        or metadata.get("quality_passed") is not True
        or not isinstance(build_id, str)
        or re.fullmatch(r"product-a-[0-9a-f]{16}", build_id) is None
        or metadata.get("build_id") != build_id
        or not isinstance(generated_at, str)
        or metadata.get("generated_at") != generated_at
        or not isinstance(manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
        or metadata.get("manifest_sha256") != manifest_sha256
        or report.get("metric_version") != EXPECTED_METRIC_VERSION
        or metadata.get("metric_version") != EXPECTED_METRIC_VERSION
        or report.get("score_version") != EXPECTED_SCORE_VERSION
        or metadata.get("score_version") != EXPECTED_SCORE_VERSION
    ):
        raise ValueError("Quality report and Product A build metadata are inconsistent")

    counts: dict[str, int] = {}
    for _, (_, _, metadata_key) in _METRIC_RELATIONS.items():
        count = _positive_integer(metadata.get(metadata_key))
        if count is None:
            raise ValueError("Product A build metadata requires positive explorer row counts")
        counts[metadata_key] = count
    return build_id, counts


def _relation_columns(connection: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    }


def _relation_count(connection: duckdb.DuckDBPyConnection, relation: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {relation}").fetchone()
    if row is None:
        raise ValueError(f"Product A relation count is unavailable: {relation}")
    return int(row[0])


def _validate_product_a_database(
    database_path: Path,
    processed_root: Path,
    build_id: str,
    metadata_counts: dict[str, int],
) -> None:
    """Reject corrupt, stale, or non-Product-A presentation databases before copying."""

    try:
        with duckdb.connect(str(database_path), read_only=True) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
                ).fetchall()
            }
            views = {
                str(row[0])
                for row in connection.execute(
                    "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
                ).fetchall()
            }
            if missing_tables := _REQUIRED_PRODUCT_A_TABLES - tables:
                raise ValueError(
                    f"DuckDB is missing required Product A tables: {sorted(missing_tables)}"
                )
            if missing_views := _REQUIRED_PRODUCT_A_VIEWS - views:
                raise ValueError(
                    f"DuckDB is missing required Product A views: {sorted(missing_views)}"
                )

            quality_columns = _relation_columns(connection, "quality_checks")
            if missing_quality_columns := {"build_id", "critical", "status"} - quality_columns:
                raise ValueError(
                    "quality_checks is missing Product A columns: "
                    f"{sorted(missing_quality_columns)}"
                )
            invalid_quality_rows = connection.execute(
                """
                SELECT count(*)
                FROM quality_checks
                WHERE build_id IS DISTINCT FROM ?
                   OR (critical IS TRUE AND status = 'FAIL')
                """,
                [build_id],
            ).fetchone()
            if (
                _relation_count(connection, "quality_checks") <= 0
                or invalid_quality_rows is None
                or int(invalid_quality_rows[0]) != 0
            ):
                raise ValueError("DuckDB quality checks do not match the approved Product A build")

            for table_name, (view_name, parquet_name, metadata_key) in _METRIC_RELATIONS.items():
                required_columns = REQUIRED_TABLE_COLUMNS[parquet_name]
                for relation in (table_name, view_name):
                    if missing_columns := required_columns - _relation_columns(
                        connection, relation
                    ):
                        raise ValueError(
                            f"{relation} is missing Product A columns: {sorted(missing_columns)}"
                        )
                    invalid_versions = connection.execute(
                        f"""
                        SELECT count(*)
                        FROM {relation}
                        WHERE metric_version IS DISTINCT FROM ?
                           OR score_version IS DISTINCT FROM ?
                        """,
                        [EXPECTED_METRIC_VERSION, EXPECTED_SCORE_VERSION],
                    ).fetchone()
                    if invalid_versions is None or int(invalid_versions[0]) != 0:
                        raise ValueError(
                            f"{relation} contains rows outside the Product A version contract"
                        )

                table_count = _relation_count(connection, table_name)
                view_count = _relation_count(connection, view_name)
                parquet_path = processed_root / parquet_name
                if not parquet_path.is_file():
                    raise ValueError(f"Required Product A Parquet is unavailable: {parquet_path}")
                parquet_row = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [parquet_path.as_posix()]
                ).fetchone()
                if parquet_row is None:
                    raise ValueError(f"Product A Parquet count is unavailable: {parquet_name}")
                parquet_count = int(parquet_row[0])
                if (
                    table_count <= 0
                    or view_count <= 0
                    or table_count != view_count
                    or table_count != parquet_count
                    or table_count != metadata_counts[metadata_key]
                ):
                    raise ValueError(
                        f"Product A row counts disagree for {table_name}: "
                        f"DuckDB={table_count}, view={view_count}, Parquet={parquet_count}, "
                        f"metadata={metadata_counts[metadata_key]}"
                    )

            for table_name in sorted(_REQUIRED_PRODUCT_A_TABLES):
                parquet_path = processed_root / f"{table_name}.parquet"
                if not parquet_path.is_file():
                    continue
                parquet_row = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [parquet_path.as_posix()]
                ).fetchone()
                if parquet_row is None or _relation_count(connection, table_name) != int(
                    parquet_row[0]
                ):
                    raise ValueError(
                        f"DuckDB row count does not match processed Parquet: {table_name}"
                    )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("DuckDB failed read-only Product A validation") from error


def build_release_bundle(
    *,
    repository_root: Path = Path("."),
    data_root: Path = Path("data"),
    database_path: Path = Path("db/immigration.duckdb"),
    output_root: Path = Path("outputs"),
) -> ReleaseBundle:
    """Create immutable release inputs only when critical quality gates pass."""

    repository_root = repository_root.resolve()
    selected_data_root = (repository_root / data_root).resolve()
    selected_database = (repository_root / database_path).resolve()
    selected_output_root = (repository_root / output_root).resolve()
    quality_root = selected_output_root / "reports" / "quality"
    report_path = quality_root / "data_quality.json"
    metadata_path = quality_root / "build_metadata.json"
    if not report_path.is_file() or not metadata_path.is_file():
        raise ValueError("Quality report and build metadata are required before packaging")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Quality report and build metadata must be valid JSON") from error
    if not isinstance(report, dict) or not isinstance(metadata, dict):
        raise ValueError("Quality report and build metadata must be JSON objects")
    if report.get("passed") is not True or report.get("critical_failure_count") != 0:
        raise ValueError("Critical quality gates failed; publication is blocked")
    build_id, metadata_counts = _validate_quality_metadata(report, metadata)
    if not selected_database.is_file():
        raise ValueError(f"DuckDB release artifact is unavailable: {selected_database}")

    processed_root = selected_data_root / "processed"
    processed_files = _processed_release_files(processed_root, repository_root)
    if not processed_files:
        raise ValueError("No processed Parquet files are available for release")
    active_artifact_ids = _active_source_artifact_ids(processed_root)
    state_files: list[tuple[Path, Path]] = []
    for directory in (
        selected_data_root / "resolved",
        selected_data_root / "classified",
    ):
        state_files.extend(_active_state_files(directory, repository_root, active_artifact_ids))
    manifest_files = _tree_files(selected_output_root / "manifests", repository_root)
    manifest_files.extend(_tree_files(selected_output_root / "reports" / "schema", repository_root))
    if not state_files or not manifest_files:
        raise ValueError("Resolved/classified build state and source manifests are required")

    _validate_product_a_database(
        selected_database,
        processed_root,
        build_id,
        metadata_counts,
    )

    release_root = selected_output_root / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    database_asset = release_root / "immigration.duckdb"
    processed_asset = release_root / "processed-parquet.zip"
    state_asset = release_root / "build-state.zip"
    manifests_asset = release_root / "source-manifests.zip"
    quality_asset = release_root / "data-quality.json"
    metadata_asset = release_root / "build-metadata.json"

    # Historical policy caches are optional supplemental state. They may be retained when
    # available, but can neither satisfy nor block the Product A release requirements above.
    for directory in (
        selected_data_root / "cache" / "policy_discovery",
        selected_data_root / "cache" / "policy_extraction",
    ):
        state_files.extend(_tree_files(directory, repository_root))

    _replace_copy(selected_database, database_asset)
    _archive(processed_asset, processed_files)
    _archive(state_asset, state_files)
    _archive(manifests_asset, manifest_files)
    _replace_copy(report_path, quality_asset)
    _replace_copy(metadata_path, metadata_asset)

    assets = (
        database_asset,
        processed_asset,
        state_asset,
        manifests_asset,
        quality_asset,
        metadata_asset,
    )
    checksums_path = release_root / "checksums.sha256"
    _replace_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets),
        checksums_path,
    )
    return ReleaseBundle(
        release_root=release_root,
        assets=assets,
        checksums_path=checksums_path,
    )
