"""Package a quality-approved build as private GitHub Release assets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


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
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("passed") is not True or report.get("critical_failure_count") != 0:
        raise ValueError("Critical quality gates failed; publication is blocked")
    if not selected_database.is_file():
        raise ValueError(f"DuckDB release artifact is unavailable: {selected_database}")

    release_root = selected_output_root / "release"
    release_root.mkdir(parents=True, exist_ok=True)
    database_asset = release_root / "immigration.duckdb"
    processed_asset = release_root / "processed-parquet.zip"
    state_asset = release_root / "build-state.zip"
    manifests_asset = release_root / "source-manifests.zip"
    quality_asset = release_root / "data-quality.json"
    metadata_asset = release_root / "build-metadata.json"

    processed_files = _tree_files(selected_data_root / "processed", repository_root)
    if not processed_files:
        raise ValueError("No processed Parquet files are available for release")
    state_files: list[tuple[Path, Path]] = []
    for directory in (
        selected_data_root / "resolved",
        selected_data_root / "classified",
        selected_data_root / "cache" / "policy_discovery",
        selected_data_root / "cache" / "policy_extraction",
    ):
        state_files.extend(_tree_files(directory, repository_root))
    manifest_files = _tree_files(selected_output_root / "manifests", repository_root)
    manifest_files.extend(_tree_files(selected_output_root / "reports" / "schema", repository_root))
    if not state_files or not manifest_files:
        raise ValueError("Resolved/classified build state and source manifests are required")

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
