"""Atomic discovery and source-artifact manifest persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import polars as pl
from pydantic import BaseModel

from sponsor_intel.case_status import canonical_case_status
from sponsor_intel.sources.discovery import REVIEWED_LCA_COMPLETED_SEGMENTS
from sponsor_intel.sources.errors import DataQualityError
from sponsor_intel.sources.models import (
    ArtifactManifestRecord,
    DiscoveryReport,
    RawArtifactManifestRecord,
    SourceArtifactCandidate,
    ValidationStatus,
)
from sponsor_intel.sources.registry import SourceRegistry


def write_json_atomic(path: Path, model: BaseModel | dict[str, object]) -> None:
    """Write one JSON document atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
            json.dump(payload, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


class ArtifactManifestStore:
    """Deduplicated JSONL provenance manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> tuple[ArtifactManifestRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[ArtifactManifestRecord] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(ArtifactManifestRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(
                    f"Invalid source manifest record at {self.path}:{line_number}"
                ) from error
        return tuple(records)

    def latest_for_candidate(
        self, candidate: SourceArtifactCandidate
    ) -> ArtifactManifestRecord | None:
        matching = [
            record
            for record in self.records()
            if record.source_id == candidate.source_id
            and record.download_url == candidate.download_url
            and record.fiscal_year == candidate.fiscal_year
            and record.fiscal_quarter == candidate.fiscal_quarter
        ]
        if not matching:
            return None
        return max(matching, key=lambda record: record.retrieved_at)

    def upsert(self, record: ArtifactManifestRecord) -> None:
        by_id = {item.source_artifact_id: item for item in self.records()}
        by_id[record.source_artifact_id] = record
        ordered = sorted(
            by_id.values(),
            key=lambda item: (item.source_id, item.fiscal_year, item.file_name, item.sha256),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}-", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
                for item in ordered:
                    destination.write(item.model_dump_json())
                    destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise


_LCA_SUPERSEDED_ROW_SCHEMA = {
    "source_artifact_id": pl.String,
    "source_row_number": pl.Int64,
    "case_id": pl.String,
    "fiscal_year": pl.Int64,
    "superseding_fiscal_year": pl.Int64,
    "superseding_source_artifact_id": pl.String,
    "superseding_source_row_number": pl.Int64,
    "earlier_decision_date": pl.Date,
    "later_decision_date": pl.Date,
}
_LCA_STABLE_IDENTITY_COLUMNS = (
    "employer_name_raw",
    "visa_class",
    "employer_address_1",
    "employer_address_2",
    "employer_city",
    "employer_state",
    "employer_postal_code",
)
_LCA_STABLE_COMPARISON_TRANSLATION = str.maketrans("", "", "\ufffd\u2013\u201c\u2019\u00bf")


def _stable_employer_identity(value: object, *, postal_code: bool = False) -> str:
    repaired = str(value or "").replace("\u00c3\u00b6", "\u00f6")
    normalized = " ".join(repaired.translate(_LCA_STABLE_COMPARISON_TRANSLATION).split()).casefold()
    if postal_code and len(normalized) == 4 and normalized.isascii() and normalized.isdigit():
        return f"0{normalized}"
    return normalized


def lca_superseded_row_keys(
    records: tuple[ArtifactManifestRecord, ...],
) -> pl.DataFrame:
    """Validate global LCA state supersessions and return earlier source-row keys."""

    lca_records = [record for record in records if record.source_id == "dol_lca"]
    if len(lca_records) < 2:
        return pl.DataFrame(schema=_LCA_SUPERSEDED_ROW_SCHEMA)
    superseded: list[dict[str, object]] = []
    required_columns = {
        "case_id",
        "case_status",
        "decision_date",
        "employer_name_raw",
        "source_row_number",
    }
    scans: list[pl.LazyFrame] = []
    for record in lca_records:
        source = pl.scan_parquet(record.parquet_path)
        available = set(source.collect_schema().names())
        missing = required_columns - available
        if missing:
            raise DataQualityError(
                f"FY{record.fiscal_year} LCA supersession validation is missing columns: "
                f"{sorted(missing)}"
            )
        scans.append(
            source.select(
                pl.lit(record.source_artifact_id).alias("source_artifact_id"),
                pl.lit(record.fiscal_year).alias("fiscal_year"),
                pl.col("source_row_number").cast(pl.Int64, strict=False),
                pl.col("case_id").cast(pl.String, strict=False),
                pl.col("case_status").cast(pl.String, strict=False),
                pl.col("decision_date").cast(pl.Date, strict=False),
                *[
                    (
                        pl.col(column).cast(pl.String, strict=False)
                        if column in available
                        else pl.lit(None, dtype=pl.String).alias(column)
                    )
                    for column in _LCA_STABLE_IDENTITY_COLUMNS
                ],
            )
        )

    combined = pl.concat(scans)
    duplicate_ids = combined.group_by("case_id").len().filter(pl.col("len") > 1).select("case_id")
    overlaps = (
        combined.join(duplicate_ids, on="case_id", how="inner")
        .with_columns(canonical_case_status().alias("_canonical_status"))
        .collect()
    )
    for case_rows in overlaps.partition_by("case_id", maintain_order=True):
        case_id = str(case_rows.get_column("case_id")[0] or "").strip()
        ordered = case_rows.sort(["decision_date", "source_artifact_id", "source_row_number"])
        rows = list(ordered.iter_rows(named=True))
        fiscal_years = sorted({int(row["fiscal_year"]) for row in rows})
        if len(rows) != 2:
            raise DataQualityError(
                f"LCA case {case_id} has an unsupported selected-artifact overlap across "
                f"fiscal years {fiscal_years}; only exactly one earlier CERTIFIED row "
                "followed by one later CERTIFIED-WITHDRAWN row for the same stable employer "
                "identity is permitted"
            )
        earlier, later = rows
        earlier_date = earlier["decision_date"]
        later_date = later["decision_date"]
        earlier_identity = tuple(
            _stable_employer_identity(earlier[column], postal_code=column == "employer_postal_code")
            for column in _LCA_STABLE_IDENTITY_COLUMNS
        )
        later_identity = tuple(
            _stable_employer_identity(later[column], postal_code=column == "employer_postal_code")
            for column in _LCA_STABLE_IDENTITY_COLUMNS
        )
        valid_pair = bool(
            earlier["source_artifact_id"] != later["source_artifact_id"]
            and case_id
            and earlier["source_row_number"] is not None
            and later["source_row_number"] is not None
            and earlier_date is not None
            and later_date is not None
            and earlier_date < later_date
            and earlier_identity[0]
            and earlier_identity == later_identity
            and earlier["_canonical_status"] == "CERTIFIED"
            and later["_canonical_status"] == "CERTIFIED-WITHDRAWN"
        )
        if not valid_pair:
            raise DataQualityError(
                f"LCA case {case_id} has an unsupported selected-artifact overlap across "
                f"fiscal years {fiscal_years}; only exactly one earlier CERTIFIED row "
                "followed by one later CERTIFIED-WITHDRAWN row for the same stable employer "
                "identity is permitted"
            )
        superseded.append(
            {
                "source_artifact_id": earlier["source_artifact_id"],
                "source_row_number": earlier["source_row_number"],
                "case_id": case_id,
                "fiscal_year": earlier["fiscal_year"],
                "superseding_fiscal_year": later["fiscal_year"],
                "superseding_source_artifact_id": later["source_artifact_id"],
                "superseding_source_row_number": later["source_row_number"],
                "earlier_decision_date": earlier["decision_date"],
                "later_decision_date": later["decision_date"],
            }
        )
    if not superseded:
        return pl.DataFrame(schema=_LCA_SUPERSEDED_ROW_SCHEMA)
    return pl.DataFrame(superseded, schema=_LCA_SUPERSEDED_ROW_SCHEMA).sort(
        [
            "fiscal_year",
            "superseding_fiscal_year",
            "case_id",
            "source_artifact_id",
            "source_row_number",
        ]
    )


def validate_lca_coverage_segments(
    pairs: tuple[tuple[SourceArtifactCandidate, ArtifactManifestRecord], ...],
) -> pl.DataFrame:
    """Fail closed when completed LCA coverage segments are incomplete or overlapping."""

    by_year: dict[int, list[tuple[SourceArtifactCandidate, ArtifactManifestRecord]]] = {}
    for candidate, record in pairs:
        if candidate.source_id == "dol_lca":
            by_year.setdefault(candidate.fiscal_year, []).append((candidate, record))
    latest_fiscal_year = max(by_year, default=None)
    for fiscal_year, yearly_pairs in sorted(by_year.items()):
        candidates = [candidate for candidate, _record in yearly_pairs]
        if any(candidate.is_partial_period for candidate in candidates):
            valid_partial = bool(
                fiscal_year == latest_fiscal_year
                and len(candidates) == 1
                and candidates[0].is_partial_period
                and not candidates[0].is_quarter_partition
            )
            if not valid_partial:
                raise DataQualityError(
                    f"Partial FY{fiscal_year} LCA selection must contain exactly one latest "
                    "nonpartition cumulative artifact"
                )
            continue
        explicit_annual = bool(
            len(candidates) == 1
            and candidates[0].fiscal_quarter is None
            and not candidates[0].is_quarter_partition
        )
        if explicit_annual:
            continue
        if not all(candidate.is_quarter_partition for candidate in candidates):
            raise DataQualityError(
                f"Completed FY{fiscal_year} LCA selection must use one explicit annual "
                "artifact or a reviewed partition set covering Q1-Q4 exactly once"
            )
        invalid_segments = [
            (candidate.coverage_start_quarter, candidate.fiscal_quarter)
            for candidate in candidates
            if candidate.coverage_start_quarter is None
            or candidate.fiscal_quarter is None
            or candidate.coverage_start_quarter > candidate.fiscal_quarter
        ]
        if invalid_segments:
            raise DataQualityError(
                f"Completed FY{fiscal_year} LCA coverage segments have invalid "
                f"quarter bounds: {invalid_segments}"
            )
        observed_segments = tuple(
            sorted(
                (candidate.coverage_start_quarter or 1, candidate.fiscal_quarter or 4)
                for candidate in candidates
            )
        )
        reviewed_segments = REVIEWED_LCA_COMPLETED_SEGMENTS.get(fiscal_year)
        covered_quarters = [
            quarter
            for start_quarter, end_quarter in observed_segments
            for quarter in range(start_quarter, end_quarter + 1)
        ]
        if sorted(covered_quarters) != [1, 2, 3, 4] or len(covered_quarters) != 4:
            raise DataQualityError(
                f"Completed FY{fiscal_year} LCA coverage segments must cover "
                f"Q1-Q4 exactly once; found {sorted(covered_quarters)}"
            )
        if reviewed_segments is None or observed_segments != tuple(sorted(reviewed_segments)):
            raise DataQualityError(
                f"Completed FY{fiscal_year} LCA partition set is not reviewed; "
                f"observed={observed_segments}, expected={reviewed_segments}"
            )
    return lca_superseded_row_keys(tuple(record for _candidate, record in pairs))


def active_artifact_selection(
    store: ArtifactManifestStore,
    registry: SourceRegistry,
    *,
    discovery_root: Path,
    source_ids: set[str],
) -> tuple[tuple[ArtifactManifestRecord, ...], pl.DataFrame]:
    """Resolve active records and validated LCA superseded source-row keys."""

    manifest_records = store.records()
    selected_records: list[ArtifactManifestRecord] = []
    superseded_frames: list[pl.DataFrame] = []
    for source_id in sorted(source_ids):
        report_path = discovery_root / f"{source_id}-latest.json"
        if not report_path.is_file():
            raise ValueError(f"Latest discovery report is unavailable: {report_path}")
        try:
            report = DiscoveryReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError(f"Latest discovery report is invalid: {report_path}") from error
        if report.source_id != source_id:
            raise ValueError(
                f"Discovery report source mismatch for {source_id}: {report.source_id}"
            )
        selected_candidates = report.selected
        if len(selected_candidates) != len(set(report.selected_candidate_ids)):
            raise ValueError(
                f"Discovery report has missing or duplicate selections for {source_id}: "
                f"{report_path}"
            )
        if not selected_candidates:
            raise ValueError(
                f"Discovery report selected no artifacts for {source_id}: {report_path}"
            )

        source = registry.get(source_id)
        source_pairs: list[tuple[SourceArtifactCandidate, ArtifactManifestRecord]] = []
        for candidate in selected_candidates:
            matches = [
                record
                for record in manifest_records
                if record.source_id == source_id
                and record.download_url == candidate.download_url
                and record.fiscal_year == candidate.fiscal_year
                and record.fiscal_quarter == candidate.fiscal_quarter
                and record.is_partial_period == candidate.is_partial_period
                and record.is_quarter_partition == candidate.is_quarter_partition
                and record.coverage_start_quarter == candidate.coverage_start_quarter
                and record.file_name == candidate.file_name
                and record.parser_version == source.parser_version
                and record.schema_version == source.schema_version
            ]
            if not matches:
                raise ValueError(
                    "Selected source artifact has no validated current manifest record: "
                    f"{source_id} {candidate.file_name}"
                )
            selected_record = max(matches, key=lambda record: record.retrieved_at)
            if selected_record.validation_status == ValidationStatus.FAILED:
                raise ValueError(
                    "Newest selected source artifact failed validation: "
                    f"{source_id} {candidate.file_name} "
                    f"({selected_record.source_artifact_id})"
                )
            if not selected_record.parquet_path.is_file():
                raise ValueError(
                    "Newest selected source artifact normalized Parquet is unavailable: "
                    f"{source_id} {candidate.file_name} "
                    f"({selected_record.source_artifact_id})"
                )
            selected_records.append(selected_record)
            source_pairs.append((candidate, selected_record))
        superseded = validate_lca_coverage_segments(tuple(source_pairs))
        if not superseded.is_empty():
            superseded_frames.append(superseded)

    records = tuple(
        sorted(
            selected_records,
            key=lambda record: (
                record.source_id,
                record.fiscal_year,
                record.fiscal_quarter or 0,
                record.file_name,
            ),
        )
    )
    superseded_rows = (
        pl.concat(superseded_frames).sort(
            ["fiscal_year", "case_id", "source_artifact_id", "source_row_number"]
        )
        if superseded_frames
        else pl.DataFrame(schema=_LCA_SUPERSEDED_ROW_SCHEMA)
    )
    return records, superseded_rows


def active_artifact_records(
    store: ArtifactManifestStore,
    registry: SourceRegistry,
    *,
    discovery_root: Path,
    source_ids: set[str],
) -> tuple[ArtifactManifestRecord, ...]:
    """Resolve the latest discovery selections to validated current manifest records."""

    records, _superseded_rows = active_artifact_selection(
        store,
        registry,
        discovery_root=discovery_root,
        source_ids=source_ids,
    )
    return records


def active_layer_paths(
    data_root: Path,
    *,
    layer: str,
    records: tuple[ArtifactManifestRecord, ...],
    source_id: str,
) -> list[Path]:
    """Return exact materialized paths for active artifacts and fail closed when one is absent."""

    paths = [
        data_root
        / layer
        / "sources"
        / source_id
        / f"fy={record.fiscal_year}"
        / f"{record.source_artifact_id}.parquet"
        for record in records
        if record.source_id == source_id
    ]
    if not paths:
        raise ValueError(f"No active {source_id} source artifacts are selected")
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise ValueError(
            f"Active {layer} {source_id} artifacts are unavailable: "
            f"{[str(path) for path in missing]}"
        )
    return paths


class RawArtifactManifestStore:
    """Deduplicated raw-download provenance written before normalization."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> tuple[RawArtifactManifestRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[RawArtifactManifestRecord] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(RawArtifactManifestRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(
                    f"Invalid raw manifest record at {self.path}:{line_number}"
                ) from error
        return tuple(records)

    def latest_for_candidate(
        self, candidate: SourceArtifactCandidate
    ) -> RawArtifactManifestRecord | None:
        matching = [
            record
            for record in self.records()
            if record.source_id == candidate.source_id
            and record.download_url == candidate.download_url
            and record.fiscal_year == candidate.fiscal_year
            and record.fiscal_quarter == candidate.fiscal_quarter
        ]
        if not matching:
            return None
        return max(matching, key=lambda record: record.retrieved_at)

    def upsert(self, record: RawArtifactManifestRecord) -> None:
        by_id = {item.source_artifact_id: item for item in self.records()}
        by_id[record.source_artifact_id] = record
        ordered = sorted(
            by_id.values(),
            key=lambda item: (item.source_id, item.fiscal_year, item.file_name, item.sha256),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}-", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
                for item in ordered:
                    destination.write(item.model_dump_json())
                    destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
