"""Manifest-backed legal-entity resolution and resolved-source persistence."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from sponsor_intel.entity_resolution.models import (
    EntityOverrides,
    EntityResolutionConfig,
    EntityResolutionSummary,
)
from sponsor_intel.entity_resolution.normalization import (
    core_name,
    name_acronym,
    normalize_city,
    normalize_name,
    normalize_postal_code,
    normalize_state,
    stable_id,
)
from sponsor_intel.entity_resolution.resolver import ResolutionTables, resolve_observations
from sponsor_intel.sources.manifests import ArtifactManifestStore, write_json_atomic
from sponsor_intel.sources.models import ArtifactManifestRecord
from sponsor_intel.sources.registry import SourceRegistry

_EMPLOYER_SOURCES = {"dol_lca", "dol_perm", "uscis_h1b"}


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


def _current_records(
    store: ArtifactManifestStore, registry: SourceRegistry
) -> tuple[ArtifactManifestRecord, ...]:
    latest: dict[tuple[str, int, int | None, str], ArtifactManifestRecord] = {}
    for record in store.records():
        try:
            source = registry.get(record.source_id)
        except ValueError:
            continue
        if (
            record.parser_version != source.parser_version
            or record.schema_version != source.schema_version
            or not record.parquet_path.is_file()
        ):
            continue
        key = (
            record.source_id,
            record.fiscal_year,
            record.fiscal_quarter,
            record.download_url,
        )
        current = latest.get(key)
        if current is None or record.retrieved_at > current.retrieved_at:
            latest[key] = record
    return tuple(
        sorted(
            latest.values(),
            key=lambda item: (
                item.source_id,
                item.fiscal_year,
                item.fiscal_quarter or 0,
                item.file_name,
            ),
        )
    )


def _column(frame: pl.LazyFrame, *names: str) -> pl.Expr:
    available = set(frame.collect_schema().names())
    for name in names:
        if name in available:
            return pl.col(name).cast(pl.String, strict=False).fill_null("")
    return pl.lit("")


def _source_projection(record: ArtifactManifestRecord) -> pl.LazyFrame | None:
    frame = pl.scan_parquet(record.parquet_path)
    if record.source_id in _EMPLOYER_SOURCES:
        if record.source_id == "dol_lca":
            city = _column(frame, "employer_city")
            state = _column(frame, "employer_state")
            postal = _column(frame, "employer_postal_code")
        elif record.source_id == "dol_perm":
            city = _column(frame, "employer_city", "emp_city")
            state = _column(frame, "employer_state_province", "emp_state")
            postal = _column(frame, "employer_postal_code", "emp_postcode")
        else:
            city = _column(frame, "city")
            state = _column(frame, "state")
            postal = _column(frame, "zip_code")
        return frame.select(
            _column(frame, "employer_name_raw").alias("alias_raw"),
            city.alias("city_raw"),
            state.alias("state_raw"),
            postal.alias("postal_raw"),
            pl.lit(record.source_id).alias("source_id"),
        )
    if record.source_id == "ipeds":
        return frame.select(
            _column(frame, "instnm", "official_name").alias("alias_raw"),
            _column(frame, "city").alias("city_raw"),
            _column(frame, "stabbr").alias("state_raw"),
            _column(frame, "zip").alias("postal_raw"),
            pl.lit(record.source_id).alias("source_id"),
        )
    return None


def _normalize_observations(
    records: tuple[ArtifactManifestRecord, ...], config: EntityResolutionConfig
) -> pl.DataFrame:
    projections = [
        projection for record in records if (projection := _source_projection(record)) is not None
    ]
    if not projections:
        raise ValueError("No current DOL, USCIS, or IPEDS artifacts are available")
    grouped = (
        pl.concat(projections, how="diagonal_relaxed")
        .group_by(["source_id", "alias_raw", "city_raw", "state_raw", "postal_raw"])
        .agg(pl.len().alias("occurrence_count"))
        .collect()
    )
    normalized = grouped.with_columns(
        pl.col("alias_raw")
        .map_elements(lambda value: normalize_name(value, config), return_dtype=pl.String)
        .alias("normalized_name"),
        pl.col("city_raw").map_elements(normalize_city, return_dtype=pl.String).alias("city"),
        pl.col("state_raw").map_elements(normalize_state, return_dtype=pl.String).alias("state"),
        pl.col("postal_raw")
        .map_elements(normalize_postal_code, return_dtype=pl.String)
        .alias("postal_code"),
    )
    collapsed = normalized.group_by(
        ["source_id", "alias_raw", "normalized_name", "city", "state", "postal_code"]
    ).agg(pl.col("occurrence_count").sum())
    return (
        collapsed.with_columns(
            pl.col("normalized_name")
            .map_elements(lambda value: core_name(value, config), return_dtype=pl.String)
            .alias("core_name"),
            pl.col("normalized_name")
            .map_elements(name_acronym, return_dtype=pl.String)
            .alias("acronym"),
        )
        .with_columns(
            pl.struct(["source_id", "alias_raw", "city", "state", "postal_code"])
            .map_elements(
                lambda row: stable_id(
                    "observation",
                    str(row["source_id"]),
                    str(row["alias_raw"]),
                    str(row["city"]),
                    str(row["state"]),
                    str(row["postal_code"]),
                ),
                return_dtype=pl.String,
            )
            .alias("observation_id")
        )
        .select(
            "observation_id",
            "alias_raw",
            "normalized_name",
            "core_name",
            "acronym",
            "source_id",
            "city",
            "state",
            "postal_code",
            "occurrence_count",
        )
    )


def _latest_ipeds(records: tuple[ArtifactManifestRecord, ...]) -> pl.DataFrame:
    candidates = [record for record in records if record.source_id == "ipeds"]
    if not candidates:
        raise ValueError("A current IPEDS directory artifact is required")
    record = max(candidates, key=lambda item: (item.fiscal_year, item.retrieved_at))
    frame = pl.read_parquet(record.parquet_path)
    return frame.select(
        "unitid",
        "instnm",
        "city",
        "stabbr",
        "zip",
        "f1sysnam",
        "f1syscod",
    )


def _location_expressions(
    source_id: str, schema_names: set[str], config: EntityResolutionConfig
) -> tuple[pl.Expr, pl.Expr, pl.Expr, pl.Expr, pl.Expr]:
    def available(*names: str) -> pl.Expr:
        for name in names:
            if name in schema_names:
                return pl.col(name).cast(pl.String, strict=False).fill_null("")
        return pl.lit("")

    if source_id == "dol_lca":
        name = available("employer_name_raw")
        city = available("employer_city")
        state = available("employer_state")
        postal = available("employer_postal_code")
    elif source_id == "dol_perm":
        name = available("employer_name_raw")
        city = available("employer_city", "emp_city")
        state = available("employer_state_province", "emp_state")
        postal = available("employer_postal_code", "emp_postcode")
    elif source_id == "uscis_h1b":
        name = available("employer_name_raw")
        city = available("city")
        state = available("state")
        postal = available("zip_code")
    else:
        name = available("instnm", "official_name")
        city = available("city")
        state = available("stabbr")
        postal = available("zip")
    return (
        name,
        name.map_elements(lambda value: normalize_name(value, config), return_dtype=pl.String),
        city.map_elements(normalize_city, return_dtype=pl.String),
        state.map_elements(normalize_state, return_dtype=pl.String),
        postal.map_elements(normalize_postal_code, return_dtype=pl.String),
    )


def _persist_resolved_sources(
    records: tuple[ArtifactManifestRecord, ...],
    tables: ResolutionTables,
    config: EntityResolutionConfig,
    data_root: Path,
) -> int:
    lookup = tables.aliases.select(
        "source_id",
        pl.col("alias_raw").alias("_entity_raw"),
        pl.col("alias_normalized").alias("_entity_name"),
        pl.col("city").alias("_entity_city"),
        pl.col("state").alias("_entity_state"),
        pl.col("postal_code").alias("_entity_postal"),
        "legal_entity_id",
        "parent_organization_id",
        pl.col("match_status").alias("entity_match_status"),
        pl.col("match_method").alias("entity_match_method"),
        pl.col("match_score").alias("entity_match_score"),
    )
    resolved_count = 0
    for record in records:
        if record.source_id not in _EMPLOYER_SOURCES | {"ipeds"}:
            continue
        lazy = pl.scan_parquet(record.parquet_path)
        names = set(lazy.collect_schema().names())
        (
            entity_raw,
            entity_name,
            entity_city,
            entity_state,
            entity_postal,
        ) = _location_expressions(record.source_id, names, config)
        frame = (
            lazy.drop(
                [
                    name
                    for name in (
                        "legal_entity_id",
                        "parent_organization_id",
                        "entity_match_status",
                        "entity_match_method",
                        "entity_match_score",
                    )
                    if name in names
                ]
            )
            .with_columns(
                entity_raw.alias("_entity_raw"),
                entity_name.alias("_entity_name"),
                entity_city.alias("_entity_city"),
                entity_state.alias("_entity_state"),
                entity_postal.alias("_entity_postal"),
            )
            .join(
                lookup.lazy(),
                on=[
                    "source_id",
                    "_entity_raw",
                    "_entity_name",
                    "_entity_city",
                    "_entity_state",
                    "_entity_postal",
                ],
                how="left",
                validate="m:1",
            )
            .drop(
                "_entity_raw",
                "_entity_name",
                "_entity_city",
                "_entity_state",
                "_entity_postal",
            )
            .collect()
        )
        target = (
            data_root
            / "resolved"
            / "sources"
            / record.source_id
            / f"fy={record.fiscal_year}"
            / f"{record.source_artifact_id}.parquet"
        )
        _write_parquet_atomic(frame, target)
        resolved_count += frame.height
    return resolved_count


def _top_inspection(
    observations: pl.DataFrame, tables: ResolutionTables, output_root: Path
) -> Path:
    joined = observations.join(
        tables.aliases.select("observation_id", "legal_entity_id", "match_status"),
        on="observation_id",
        how="left",
        validate="1:1",
    )
    report = joined.sort(
        [
            "occurrence_count",
            "source_id",
            "normalized_name",
            "state",
            "city",
            "observation_id",
        ],
        descending=[True, False, False, False, False, False],
    ).head(100)
    path = output_root / "reports" / "entities" / "top_entity_inspection.parquet"
    _write_parquet_atomic(report, path)
    return path


class EntityResolutionPipeline:
    """Build auditable legal and parent registries from current source artifacts."""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        config_path: Path = Path("configs/entity_resolution.yaml"),
        overrides_path: Path = Path("configs/entity_overrides.yaml"),
    ) -> None:
        self.registry = registry
        self.data_root = data_root
        self.output_root = output_root
        self.config = EntityResolutionConfig.from_yaml(config_path)
        self.overrides = EntityOverrides.from_yaml(overrides_path)
        self.manifest_store = ArtifactManifestStore(
            output_root / "manifests" / "source_artifacts.jsonl"
        )

    def build(self) -> EntityResolutionSummary:
        records = _current_records(self.manifest_store, self.registry)
        observations = _normalize_observations(records, self.config)
        tables = resolve_observations(
            observations, _latest_ipeds(records), self.config, self.overrides
        )
        resolved_root = self.data_root / "resolved"
        legal_path = resolved_root / "legal_entities.parquet"
        parent_path = resolved_root / "parent_organizations.parquet"
        alias_path = resolved_root / "entity_aliases.parquet"
        review_path = self.output_root / "review" / "entity_match_review.parquet"
        _write_parquet_atomic(tables.legal_entities, legal_path)
        _write_parquet_atomic(tables.parent_organizations, parent_path)
        _write_parquet_atomic(tables.aliases, alias_path)
        _write_parquet_atomic(tables.review_queue, review_path)
        resolved_count = _persist_resolved_sources(records, tables, self.config, self.data_root)
        inspection_path = _top_inspection(observations, tables, self.output_root)
        status_counts = {
            str(row["match_status"]): int(row["len"])
            for row in tables.aliases.group_by("match_status").len().iter_rows(named=True)
        }
        summary_path = self.output_root / "reports" / "entities" / "summary.json"
        summary = EntityResolutionSummary(
            observation_count=observations.height,
            legal_entity_count=tables.legal_entities.height,
            parent_organization_count=tables.parent_organizations.height,
            resolved_record_count=resolved_count,
            status_counts=status_counts,
            review_queue_count=tables.review_queue.height,
            legal_entities_path=legal_path,
            parent_organizations_path=parent_path,
            aliases_path=alias_path,
            review_queue_path=review_path,
            summary_path=summary_path,
        )
        payload: dict[str, Any] = summary.model_dump(mode="json")
        payload["normalization_version"] = self.config.normalization_version
        payload["schema_version"] = self.config.schema_version
        payload["top_entity_inspection_path"] = str(inspection_path)
        payload["source_artifact_count"] = len(records)
        write_json_atomic(summary_path, payload)
        return summary
