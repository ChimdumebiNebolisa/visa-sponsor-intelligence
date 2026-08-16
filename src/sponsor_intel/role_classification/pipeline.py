"""Materialize role classifications and classified DOL source mirrors."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import polars as pl

from sponsor_intel.role_classification.classifier import RoleClassifier
from sponsor_intel.role_classification.models import (
    RoleClassificationSummary,
    RoleTaxonomyConfig,
)
from sponsor_intel.sources.manifests import (
    ArtifactManifestStore,
    active_artifact_records,
    active_layer_paths,
    write_json_atomic,
)
from sponsor_intel.sources.models import ArtifactManifestRecord
from sponsor_intel.sources.registry import DEFAULT_SOURCE_REGISTRY_PATH, SourceRegistry


def _write_parquet_atomic(frame: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        frame.write_parquet(temporary_path, compression="zstd", statistics=True)
        os.replace(temporary_path, target)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _classification_id(source_id: str, title: str, soc_code: str, version: str) -> str:
    payload = "\x1f".join((source_id, title, soc_code, version))
    return f"role_{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


def _source_files(data_root: Path, records: tuple[ArtifactManifestRecord, ...]) -> list[Path]:
    files: list[Path] = []
    for source_id in ("dol_lca", "dol_perm"):
        if any(record.source_id == source_id for record in records):
            files.extend(
                active_layer_paths(
                    data_root,
                    layer="resolved",
                    records=records,
                    source_id=source_id,
                )
            )
    if not files:
        raise ValueError("No Phase 3 DOL resolved-source Parquet files are available")
    return sorted(files)


def _keyed(frame: pl.LazyFrame) -> pl.LazyFrame:
    names = set(frame.collect_schema().names())
    title = (
        pl.col("job_title_raw").cast(pl.String, strict=False).fill_null("")
        if "job_title_raw" in names
        else pl.lit("")
    )
    soc = (
        pl.col("soc_code").cast(pl.String, strict=False).fill_null("")
        if "soc_code" in names
        else pl.lit("")
    )
    return frame.with_columns(title.alias("_role_title_key"), soc.alias("_role_soc_key"))


def _build_lookup(files: list[Path], classifier: RoleClassifier) -> pl.DataFrame:
    combinations = (
        pl.concat(
            [
                _keyed(pl.scan_parquet(path)).select(
                    "source_id", "_role_title_key", "_role_soc_key"
                )
                for path in files
            ],
            how="diagonal_relaxed",
        )
        .group_by(["source_id", "_role_title_key", "_role_soc_key"])
        .agg(pl.len().alias("occurrence_count"))
        .collect()
        .sort(["source_id", "_role_title_key", "_role_soc_key"])
    )
    rows: list[dict[str, object]] = []
    for row in combinations.iter_rows(named=True):
        result = classifier.classify(str(row["_role_title_key"]), str(row["_role_soc_key"]))
        rows.append(
            {
                "classification_id": _classification_id(
                    str(row["source_id"]),
                    str(row["_role_title_key"]),
                    str(row["_role_soc_key"]),
                    result.classification_version,
                ),
                "source_id": row["source_id"],
                "job_title_raw": row["_role_title_key"],
                "soc_code_raw": row["_role_soc_key"],
                "occurrence_count": row["occurrence_count"],
                **result.model_dump(),
            }
        )
    return pl.DataFrame(rows).sort("classification_id")


def _persist_classified_sources(files: list[Path], lookup: pl.DataFrame, data_root: Path) -> int:
    join_lookup = lookup.select(
        "source_id",
        pl.col("job_title_raw").alias("_role_title_key"),
        pl.col("soc_code_raw").alias("_role_soc_key"),
        "technical_role",
        "role_family",
        "role_confidence",
        "classification_method",
        "classification_rule",
        "classification_version",
        "review_status",
    )
    record_count = 0
    for path in files:
        lazy = _keyed(pl.scan_parquet(path))
        names = set(lazy.collect_schema().names())
        classified = (
            lazy.drop(
                [
                    item
                    for item in (
                        "technical_role",
                        "role_family",
                        "role_confidence",
                        "classification_method",
                        "classification_rule",
                        "classification_version",
                        "review_status",
                        "role_review_status",
                    )
                    if item in names
                ]
            )
            .join(
                join_lookup.lazy(),
                on=["source_id", "_role_title_key", "_role_soc_key"],
                how="left",
                validate="m:1",
            )
            .drop("_role_title_key", "_role_soc_key")
            .collect()
        )
        source_id = str(classified["source_id"][0])
        fiscal_year = int(classified["fiscal_year"][0])
        target = data_root / "classified" / "sources" / source_id / f"fy={fiscal_year}" / path.name
        _write_parquet_atomic(classified, target)
        record_count += classified.height
    return record_count


class RoleClassificationPipeline:
    """Classify every resolved DOL row with versioned deterministic evidence."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        taxonomy_path: Path = Path("configs/role_taxonomy.yaml"),
        source_registry_path: Path = DEFAULT_SOURCE_REGISTRY_PATH,
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.config = RoleTaxonomyConfig.from_yaml(taxonomy_path)
        self.classifier = RoleClassifier(self.config)
        self.registry = SourceRegistry.from_yaml(source_registry_path)
        self.manifest_store = ArtifactManifestStore(
            output_root / "manifests" / "source_artifacts.jsonl"
        )

    def build(self) -> RoleClassificationSummary:
        source_ids = {
            record.source_id
            for record in self.manifest_store.records()
            if record.source_id in {"dol_lca", "dol_perm"}
        }
        records = active_artifact_records(
            self.manifest_store,
            self.registry,
            discovery_root=self.output_root / "manifests" / "discovery",
            source_ids=source_ids,
        )
        files = _source_files(self.data_root, records)
        lookup = _build_lookup(files, self.classifier)
        classifications_path = self.data_root / "processed" / "role_classifications.parquet"
        review_path = self.output_root / "review" / "role_classification_review.parquet"
        summary_path = self.output_root / "reports" / "roles" / "summary.json"
        _write_parquet_atomic(lookup, classifications_path)
        review = lookup.filter(pl.col("review_status") == "NEEDS_REVIEW").sort(
            ["occurrence_count", "source_id", "job_title_raw", "soc_code_raw"],
            descending=[True, False, False, False],
        )
        _write_parquet_atomic(review, review_path)
        record_count = _persist_classified_sources(files, lookup, self.data_root)
        family_counts = {
            str(row["role_family"]): int(row["occurrence_count"])
            for row in lookup.group_by("role_family")
            .agg(pl.col("occurrence_count").sum())
            .iter_rows(named=True)
        }
        method_counts = {
            str(row["classification_method"]): int(row["occurrence_count"])
            for row in lookup.group_by("classification_method")
            .agg(pl.col("occurrence_count").sum())
            .iter_rows(named=True)
        }
        technical_count = int(
            lookup.filter(pl.col("technical_role") == True)["occurrence_count"].sum()  # noqa: E712
        )
        ambiguous_count = int(
            lookup.filter(pl.col("technical_role").is_null())["occurrence_count"].sum()
        )
        summary = RoleClassificationSummary(
            record_count=record_count,
            unique_classification_count=lookup.height,
            technical_record_count=technical_count,
            ambiguous_record_count=ambiguous_count,
            review_queue_count=review.height,
            family_counts=family_counts,
            method_counts=method_counts,
            classifications_path=classifications_path,
            review_queue_path=review_path,
            summary_path=summary_path,
        )
        write_json_atomic(summary_path, summary.model_dump(mode="json"))
        return summary
