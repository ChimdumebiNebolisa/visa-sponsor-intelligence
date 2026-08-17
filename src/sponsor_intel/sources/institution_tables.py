"""Build canonical institution and explicitly reviewed HERD join tables."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import polars as pl

from sponsor_intel.sources.errors import DataQualityError
from sponsor_intel.sources.manifests import ArtifactManifestStore, write_json_atomic
from sponsor_intel.sources.models import ArtifactManifestRecord


def _latest_records(
    records: tuple[ArtifactManifestRecord, ...], source_id: str
) -> list[ArtifactManifestRecord]:
    latest: dict[tuple[int, str], ArtifactManifestRecord] = {}
    for record in records:
        if source_id != record.source_id or not record.parquet_path.is_file():
            continue
        key = (record.fiscal_year, record.download_url)
        current = latest.get(key)
        if current is None or record.retrieved_at > current.retrieved_at:
            latest[key] = record
    return sorted(latest.values(), key=lambda item: (item.fiscal_year, item.file_name))


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


def build_institution_tables(
    manifest_store: ArtifactManifestStore,
    *,
    data_root: Path,
    output_root: Path,
) -> None:
    """Materialize IPEDS identities and UNITID-only HERD reconciliation evidence."""

    records = manifest_store.records()
    ipeds_records = _latest_records(records, "ipeds")
    if not ipeds_records:
        return
    latest_schema_record = max(ipeds_records, key=lambda item: item.retrieved_at)
    current_ipeds_records = [
        record
        for record in ipeds_records
        if record.schema_version == latest_schema_record.schema_version
        and record.parser_version == latest_schema_record.parser_version
    ]
    directories = [
        record for record in current_ipeds_records if record.file_name.upper().startswith("HD")
    ]
    characteristics = [
        record for record in current_ipeds_records if record.file_name.upper().startswith("IC")
    ]
    if not directories or not characteristics:
        raise DataQualityError("Finalized IPEDS requires both HD and IC artifacts")
    latest_ipeds = max(directories, key=lambda item: (item.fiscal_year, item.retrieved_at))
    matching_characteristics = [
        record for record in characteristics if record.fiscal_year == latest_ipeds.fiscal_year
    ]
    if not matching_characteristics:
        raise DataQualityError("Finalized IPEDS HD and IC artifacts must use the same year")
    latest_characteristics = max(matching_characteristics, key=lambda item: item.retrieved_at)
    ipeds = pl.read_parquet(latest_ipeds.parquet_path)
    ic = pl.read_parquet(latest_characteristics.parquet_path).select(
        "institution_id",
        "characteristics_source_artifact_id",
        "characteristics_year",
        "institution_affiliation_code",
        "calendar_system_code",
        "open_admissions_code",
        "years_of_college_code",
    )
    ipeds = ipeds.join(ic, on="institution_id", how="left", validate="1:1").with_columns(
        pl.col("sector").alias("institution_category")
    )
    institution_columns = [
        "institution_id",
        "ipeds_unitid",
        "official_name",
        "institution_aliases",
        "system_name",
        "control",
        "sector",
        "city",
        "stabbr",
        "official_domain",
        "highest_degree",
        "active_status",
        "institution_category",
        "release_status",
        "is_finalized",
        "characteristics_source_artifact_id",
        "characteristics_year",
        "institution_affiliation_code",
        "calendar_system_code",
        "open_admissions_code",
        "years_of_college_code",
        "legal_entity_id",
        "parent_organization_id",
        "match_confidence",
        "review_status",
        "source_artifact_id",
        "directory_year",
        "source_url",
        "source_sha256",
        "schema_version",
    ]
    institutions = ipeds.select(institution_columns).rename({"stabbr": "state"})
    _write_parquet_atomic(institutions, data_root / "processed" / "institutions.parquet")

    herd_records = _latest_records(records, "herd")
    if not herd_records:
        return
    herd = pl.concat(
        [pl.read_parquet(record.parquet_path) for record in herd_records],
        how="diagonal_relaxed",
    )
    duplicate_observations = herd.select(
        pl.struct(["inst_id", "survey_year"]).is_duplicated().sum()
    ).item()
    if duplicate_observations:
        raise DataQualityError(
            "HERD standard and short-form observations overlap for the same institution/year"
        )

    source_join_columns = {
        "institution_id",
        "institution_join_method",
        "institution_match_confidence",
        "institution_review_status",
    }
    herd = herd.drop([column for column in source_join_columns if column in herd.columns])
    crosswalk = institutions.select(
        "ipeds_unitid",
        pl.col("institution_id").alias("_matched_institution_id"),
        pl.col("official_name").alias("ipeds_official_name"),
    )
    herd = (
        herd.join(crosswalk, on="ipeds_unitid", how="left", validate="m:1")
        .with_columns(
            pl.col("_matched_institution_id").alias("institution_id"),
            pl.when(pl.col("_matched_institution_id").is_not_null())
            .then(pl.lit("IPEDS_UNITID_EXACT"))
            .otherwise(pl.lit("UNMATCHED"))
            .alias("institution_join_method"),
            pl.when(pl.col("_matched_institution_id").is_not_null())
            .then(pl.lit(1.0))
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("institution_match_confidence"),
            pl.when(pl.col("_matched_institution_id").is_not_null())
            .then(pl.lit("IDENTIFIER_MATCHED"))
            .otherwise(pl.lit("NEEDS_REVIEW"))
            .alias("institution_review_status"),
        )
        .drop("_matched_institution_id")
    )
    _write_parquet_atomic(herd, data_root / "processed" / "herd_observations.parquet")

    review = herd.select(
        "inst_id",
        "survey_year",
        "survey_form",
        "ncses_inst_id",
        "ipeds_unitid",
        "institution_name_raw",
        "ipeds_official_name",
        "institution_id",
        "institution_join_method",
        "institution_match_confidence",
        "institution_review_status",
        "source_artifact_id",
    ).sort(["institution_review_status", "survey_year", "institution_name_raw"])
    review_path = output_root / "reports" / "institutions" / "herd_ipeds_join_review.parquet"
    _write_parquet_atomic(review, review_path)
    matched = review.filter(pl.col("institution_review_status") == "IDENTIFIER_MATCHED").height
    total = review.height
    write_json_atomic(
        review_path.with_suffix(".json"),
        {
            "join_policy": "Exact six-digit IPEDS UNITID only; no name-based fallback",
            "institution_directory_year": latest_ipeds.fiscal_year,
            "institution_characteristics_year": latest_characteristics.fiscal_year,
            "ipeds_release_status": "FINAL",
            "herd_observation_count": total,
            "identifier_matched_count": matched,
            "needs_review_count": total - matched,
            "identifier_match_rate": matched / total if total else None,
            "review_table": str(review_path),
        },
    )
