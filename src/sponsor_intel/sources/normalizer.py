"""DOL Excel normalization, schema drift, and data-quality gates."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import fastexcel
import polars as pl

from sponsor_intel.sources.errors import DataQualityError, SchemaDriftError
from sponsor_intel.sources.manifests import write_json_atomic
from sponsor_intel.sources.models import (
    DownloadedArtifact,
    IssueSeverity,
    NormalizedDataset,
    PersistedDataset,
    SourceConfig,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)

_DATE_COLUMNS = {
    "received_date",
    "decision_date",
    "employment_start_date",
    "employment_end_date",
    "priority_date",
}
_FLOAT_COLUMNS = {"wage_from", "wage_to"}


def normalize_column_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_") or "unnamed_column"


def _unique_column_names(columns: list[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    renames: dict[str, str] = {}
    normalized: list[str] = []
    counts: dict[str, int] = {}
    for original in columns:
        base = normalize_column_name(original)
        counts[base] = counts.get(base, 0) + 1
        target = base if counts[base] == 1 else f"{base}_{counts[base]}"
        renames[original] = target
        normalized.append(target)
    return renames, tuple(normalized)


def _canonical_expression(source: str, logical: str) -> pl.Expr:
    expression = pl.col(source)
    if logical in _FLOAT_COLUMNS:
        return (
            expression.cast(pl.String, strict=False)
            .str.replace_all(r"[,$]", "")
            .cast(pl.Float64, strict=False)
            .alias(logical)
        )
    if logical in _DATE_COLUMNS:
        as_string = expression.cast(pl.String, strict=False)
        return pl.coalesce(
            as_string.str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False).cast(pl.Date),
            as_string.str.strptime(pl.Date, "%Y-%m-%d", strict=False),
            as_string.str.strptime(pl.Datetime, "%m/%d/%Y %H:%M:%S", strict=False).cast(pl.Date),
            as_string.str.strptime(pl.Date, "%m/%d/%Y", strict=False),
        ).alias(logical)
    return expression.cast(pl.String, strict=False).alias(logical)


def _status_for(issues: list[ValidationIssue]) -> ValidationStatus:
    if any(issue.severity is IssueSeverity.ERROR for issue in issues):
        return ValidationStatus.FAILED
    if issues:
        return ValidationStatus.WARNING
    return ValidationStatus.PASSED


def _string_schema_overrides(path: Path) -> dict[str, pl.DataType]:
    reader = fastexcel.read_excel(path)
    sheet = reader.load_sheet(0, n_rows=0)
    return {column.name: pl.String() for column in sheet.available_columns()}


def _collapse_repeated_case_decisions(
    frame: pl.DataFrame,
    *,
    case_source: str | None,
    decision_source: str | None,
    source_columns: list[str],
) -> tuple[pl.DataFrame, int]:
    if case_source is None or decision_source is None:
        return frame, 0
    repeated = frame.filter(pl.col(case_source).is_duplicated())
    if repeated.is_empty():
        return frame, 0

    comparable_columns = [
        column for column in source_columns if column not in {case_source, decision_source}
    ]
    conflicts = (
        repeated.group_by(case_source)
        .agg([pl.col(column).n_unique().alias(column) for column in comparable_columns])
        .filter(pl.max_horizontal([pl.col(column) for column in comparable_columns]) > 1)
    )
    if not conflicts.is_empty():
        return frame, 0

    ranked = repeated.with_columns(
        _canonical_expression(decision_source, "decision_date").alias("_decision_order")
    ).sort(
        [case_source, "_decision_order", "source_row_number"],
        descending=[False, True, True],
        nulls_last=True,
    )
    retained_row_numbers = (
        ranked.group_by(case_source, maintain_order=True)
        .agg(pl.col("source_row_number").first())
        .get_column("source_row_number")
        .to_list()
    )
    repeated_row_numbers = repeated.get_column("source_row_number")
    removed_row_numbers = repeated_row_numbers.filter(
        ~repeated_row_numbers.is_in(retained_row_numbers)
    ).to_list()
    return (
        frame.filter(~pl.col("source_row_number").is_in(removed_row_numbers)),
        len(removed_row_numbers),
    )


class DolExcelNormalizer:
    """Normalize one DOL disclosure workbook without resolving entities."""

    def __init__(self, config: SourceConfig, staging_root: Path, report_root: Path) -> None:
        self.config = config
        self.staging_root = staging_root
        self.report_root = report_root

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        schema_overrides = _string_schema_overrides(artifact.raw_path)
        try:
            frame = pl.read_excel(
                artifact.raw_path,
                sheet_id=1,
                engine="calamine",
                schema_overrides=schema_overrides,
                infer_schema_length=10_000,
                raise_if_empty=True,
            )
        except fastexcel.CalamineCellError:
            frame = pl.read_excel(
                artifact.raw_path,
                sheet_id=1,
                engine="xlsx2csv",
                infer_schema_length=10_000,
                raise_if_empty=True,
            )
        if not isinstance(frame, pl.DataFrame):
            raise SchemaDriftError(f"Expected one worksheet in {artifact.raw_path.name}")
        original_columns = tuple(str(column) for column in frame.columns)
        renames, normalized_columns = _unique_column_names(list(original_columns))
        frame = frame.rename(renames)
        available = set(frame.columns)
        source_columns = list(frame.columns)
        source_row_count = frame.height
        frame = frame.with_row_index("source_row_number", offset=2).unique(
            subset=source_columns,
            maintain_order=True,
        )
        exact_duplicate_rows_removed = source_row_count - frame.height

        column_mapping: dict[str, str] = {}
        missing_required: list[str] = []
        absence_key = f"{artifact.candidate.variant}:fy{artifact.candidate.fiscal_year}"
        allowed_missing = set(self.config.allowed_missing_columns.get(absence_key, ()))
        known_absent: list[str] = []
        all_aliases = self.config.required_columns | self.config.optional_columns
        for logical_name, aliases in all_aliases.items():
            candidates = [normalize_column_name(alias) for alias in aliases]
            source_name = next(
                (candidate for candidate in candidates if candidate in available), None
            )
            if source_name is None:
                if logical_name in self.config.required_columns:
                    if logical_name in allowed_missing:
                        known_absent.append(logical_name)
                    else:
                        missing_required.append(logical_name)
                continue
            column_mapping[logical_name] = source_name

        frame, repeated_case_decisions_removed = _collapse_repeated_case_decisions(
            frame,
            case_source=column_mapping.get("case_id"),
            decision_source=column_mapping.get("decision_date"),
            source_columns=source_columns,
        )

        known_source_columns = {
            normalize_column_name(alias) for aliases in all_aliases.values() for alias in aliases
        }
        unexpected_columns = sorted(available - known_source_columns)
        schema_fingerprint = hashlib.sha256(
            ("\n".join(normalized_columns) + "\n").encode("utf-8")
        ).hexdigest()
        expected_schema_fingerprint = self.config.expected_schema_fingerprints.get(absence_key)
        schema_fingerprint_changed = schema_fingerprint != expected_schema_fingerprint
        schema_diff_path = (
            self.report_root / "schema" / self.config.id / f"{artifact.source_artifact_id}.json"
        )
        schema_report: dict[str, object] = {
            "source_artifact_id": artifact.source_artifact_id,
            "source_id": self.config.id,
            "fiscal_year": artifact.candidate.fiscal_year,
            "fiscal_quarter": artifact.candidate.fiscal_quarter,
            "schema_version": self.config.schema_version,
            "parser_version": self.config.parser_version,
            "schema_fingerprint": schema_fingerprint,
            "expected_schema_fingerprint": expected_schema_fingerprint,
            "schema_fingerprint_changed": schema_fingerprint_changed,
            "original_columns": original_columns,
            "normalized_columns": normalized_columns,
            "logical_column_mapping": column_mapping,
            "missing_required_columns": missing_required,
            "known_absent_columns": known_absent,
            "unexpected_optional_columns": unexpected_columns,
            "exact_duplicate_rows_removed": exact_duplicate_rows_removed,
            "repeated_case_decision_rows_removed": repeated_case_decisions_removed,
        }
        write_json_atomic(schema_diff_path, schema_report)
        if missing_required:
            raise SchemaDriftError(
                f"{self.config.id} required logical columns are missing: {missing_required}; "
                f"see {schema_diff_path}"
            )

        frame = frame.with_columns(
            [_canonical_expression(source, logical) for logical, source in column_mapping.items()]
        )
        if known_absent:
            frame = frame.with_columns(
                [pl.lit(None, dtype=pl.String).alias(logical) for logical in known_absent]
            )
        frame = frame.with_columns(
            pl.lit(artifact.source_artifact_id).alias("source_artifact_id"),
            pl.lit(self.config.id).alias("source_id"),
            pl.lit(artifact.candidate.fiscal_year, dtype=pl.Int32).alias("fiscal_year"),
            pl.lit(artifact.candidate.fiscal_quarter, dtype=pl.Int8).alias("fiscal_quarter"),
            pl.lit(artifact.candidate.is_partial_period).alias("is_partial_period"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
        )
        validation = self.validate(
            frame,
            unexpected_columns=unexpected_columns,
            exact_duplicate_rows_removed=exact_duplicate_rows_removed,
            known_absent_columns=known_absent,
            repeated_case_decision_rows_removed=repeated_case_decisions_removed,
            schema_fingerprint_changed=schema_fingerprint_changed,
            schema_fingerprint=schema_fingerprint,
            expected_schema_fingerprint=expected_schema_fingerprint,
        )
        return NormalizedDataset(
            artifact=artifact,
            frame=frame,
            original_columns=original_columns,
            normalized_columns=tuple(frame.columns),
            column_mapping=column_mapping,
            validation=validation,
            schema_diff_path=schema_diff_path,
        )

    def validate(
        self,
        frame: pl.DataFrame,
        *,
        unexpected_columns: list[str] | None = None,
        exact_duplicate_rows_removed: int = 0,
        known_absent_columns: list[str] | None = None,
        repeated_case_decision_rows_removed: int = 0,
        schema_fingerprint_changed: bool = False,
        schema_fingerprint: str | None = None,
        expected_schema_fingerprint: str | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if frame.height < self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="minimum_row_count",
                    message="Normalized row count is below the configured source sanity check",
                    details={
                        "actual": frame.height,
                        "minimum": self.config.minimum_row_count,
                    },
                )
            )

        blank_case_ids = frame.select(
            (
                pl.col("case_id").is_null()
                | (pl.col("case_id").str.strip_chars().fill_null("") == "")
            ).sum()
        ).item()
        if blank_case_ids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="missing_case_id",
                    message="Case identifiers must be present",
                    details={"count": int(blank_case_ids)},
                )
            )

        duplicate_case_ids = (
            frame.filter(pl.col("case_id").is_not_null())
            .select(pl.col("case_id").is_duplicated().sum())
            .item()
        )
        if duplicate_case_ids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="duplicate_case_id",
                    message="Case identifiers must be unique within one source artifact",
                    details={"count": int(duplicate_case_ids)},
                )
            )

        invalid_fiscal_years = frame.filter(pl.col("fiscal_year") < 2022).height
        if invalid_fiscal_years:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="invalid_fiscal_year",
                    message="Fiscal year must be at least 2022",
                    details={"count": invalid_fiscal_years},
                )
            )
        if exact_duplicate_rows_removed:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="exact_duplicate_source_rows",
                    message="Exact duplicate source rows were removed deterministically",
                    details={"count": exact_duplicate_rows_removed},
                )
            )
        if known_absent_columns:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="known_source_schema_absence",
                    message="Official source variant omits configured canonical columns",
                    details={"columns": known_absent_columns},
                )
            )
        if repeated_case_decision_rows_removed:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="repeated_case_decisions",
                    message="Repeated case decisions were collapsed to the latest decision",
                    details={"count": repeated_case_decision_rows_removed},
                )
            )
        if schema_fingerprint_changed:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="source_schema_drift",
                    message="Source schema differs from the committed official-layout baseline",
                    details={
                        "unexpected_optional_columns": unexpected_columns or [],
                        "schema_fingerprint": schema_fingerprint,
                        "expected_schema_fingerprint": expected_schema_fingerprint,
                    },
                )
            )
        return ValidationResult(status=_status_for(issues), issues=tuple(issues))

    def persist(self, dataset: NormalizedDataset) -> PersistedDataset:
        if dataset.validation.status is ValidationStatus.FAILED:
            errors = [issue.message for issue in dataset.validation.issues]
            raise DataQualityError(f"Normalized data failed quality gates: {errors}")
        target_directory = (
            self.staging_root / self.config.id / f"fy={dataset.artifact.candidate.fiscal_year}"
        )
        target_directory.mkdir(parents=True, exist_ok=True)
        target_path = target_directory / f"{dataset.artifact.source_artifact_id}.parquet"
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{target_path.name}-", suffix=".tmp", dir=target_directory
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            dataset.frame.write_parquet(
                temporary_path,
                compression="zstd",
                statistics=True,
            )
            os.replace(temporary_path, target_path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
        return PersistedDataset(
            artifact=dataset.artifact,
            parquet_path=target_path,
            row_count=dataset.frame.height,
            column_count=dataset.frame.width,
            schema_version=self.config.schema_version,
            parser_version=self.config.parser_version,
            validation=dataset.validation,
            schema_diff_path=dataset.schema_diff_path,
        )
