"""USCIS H-1B Employer Data Hub adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.sources.errors import SchemaDriftError
from sponsor_intel.sources.federal_discovery import discover_uscis_h1b
from sponsor_intel.sources.models import (
    DownloadedArtifact,
    IssueSeverity,
    NormalizedDataset,
    SourceArtifactCandidate,
    SourceConfig,
    SourceContext,
    ValidationIssue,
    ValidationResult,
)
from sponsor_intel.sources.tabular import (
    TabularSourceAdapter,
    inspect_schema,
    read_csv_artifact,
    record_validation_report,
    validation_status,
)

_MEASURES = {
    "New Employment Approval": "initial_approvals",
    "New Employment Denial": "initial_denials",
    "Continuation Approval": "continuing_approvals",
    "Continuation Denial": "continuing_denials",
    "Change with Same Employer Approval": "same_employer_change_approvals",
    "Change with Same Employer Denial": "same_employer_change_denials",
    "New Concurrent Approval": "concurrent_approvals",
    "New Concurrent Denial": "concurrent_denials",
    "Change of Employer Approval": "employer_change_approvals",
    "Change of Employer Denial": "employer_change_denials",
    "Amended Approval": "amended_approvals",
    "Amended Denial": "amended_denials",
}


class UscisH1bAdapter(TabularSourceAdapter):
    """Normalize USCIS petition decisions as evidence distinct from DOL records."""

    def __init__(
        self,
        config: SourceConfig,
        client,
        data_root: Path,
        output_root: Path,
    ) -> None:
        if config.id != "uscis_h1b":
            raise ValueError(f"UscisH1bAdapter requires uscis_h1b, received {config.id}")
        super().__init__(config, client, data_root, output_root)

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]:
        report = discover_uscis_h1b(
            self.config,
            self.client,
            from_fiscal_year=context.from_fiscal_year,
        )
        self.last_discovery_report = report
        return list(report.selected)

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        raw = read_csv_artifact(artifact)
        original_columns = tuple(raw.columns)
        schema = inspect_schema(self.config, artifact, original_columns, self.report_root)
        raw = raw.rename(dict(zip(original_columns, schema.normalized_columns, strict=True)))
        required_measures = set(_MEASURES)
        available_measures = set(raw.get_column("measure_names").drop_nulls().unique().to_list())
        missing_measures = sorted(required_measures - available_measures)
        if missing_measures:
            raise SchemaDriftError(f"USCIS H-1B measures are missing: {missing_measures}")

        raw = raw.with_columns(
            pl.col("fiscal_year").cast(pl.Int32, strict=False),
            pl.col("measure_values")
            .str.replace_all(",", "")
            .cast(pl.Int64, strict=False)
            .alias("_measure_value"),
        ).filter(pl.col("fiscal_year") >= self.config.minimum_fiscal_year)
        dimensions = [
            "line_by_line",
            "fiscal_year",
            "employer_petitioner_name",
            "tax_id",
            "industry_naics_code",
            "petitioner_city",
            "petitioner_state",
            "petitioner_zip_code",
        ]
        metric_expressions = [
            pl.col("_measure_value")
            .filter(pl.col("measure_names") == source_name)
            .first()
            .alias(target_name)
            for source_name, target_name in _MEASURES.items()
        ]
        frame = (
            raw.group_by(dimensions, maintain_order=True)
            .agg(metric_expressions)
            .rename(
                {
                    "line_by_line": "source_line_id_raw",
                    "employer_petitioner_name": "employer_name_raw",
                    "tax_id": "tax_id_last_four",
                    "industry_naics_code": "naics",
                    "petitioner_city": "city",
                    "petitioner_state": "state",
                    "petitioner_zip_code": "zip_code",
                }
            )
            .with_row_index("source_row_number", offset=1)
        )
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("legal_entity_id"),
            pl.lit(None, dtype=pl.String).alias("parent_organization_id"),
            pl.lit("USCIS_H1B_PETITION_DECISIONS").alias("evidence_type"),
            pl.lit(artifact.source_artifact_id).alias("source_artifact_id"),
            pl.lit(self.config.id).alias("source_id"),
            pl.lit(artifact.candidate.is_partial_period).alias("is_partial_period"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
        )

        issues = list(schema.issues)
        if frame.height < self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="minimum_row_count",
                    message="USCIS normalized row count is below the configured minimum",
                    details={"actual": frame.height, "minimum": self.config.minimum_row_count},
                )
            )
        wrong_fiscal_year = frame.filter(
            pl.col("fiscal_year") != artifact.candidate.fiscal_year
        ).height
        if wrong_fiscal_year:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="fiscal_year_filter_mismatch",
                    message="USCIS Tableau fiscal-year filter returned records from another year",
                    details={"count": wrong_fiscal_year},
                )
            )
        missing_line_ids = frame.get_column("source_line_id_raw").null_count()
        if missing_line_ids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="unavailable_source_line_id",
                    message="USCIS rows with unavailable Tableau line identifiers were preserved",
                    details={"count": missing_line_ids},
                )
            )
        blank_employers = frame.filter(
            pl.col("employer_name_raw").is_null()
            | (pl.col("employer_name_raw").str.strip_chars().fill_null("") == "")
        ).height
        if blank_employers:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="redacted_or_blank_employer_name",
                    message="USCIS rows with unavailable employer names were preserved",
                    details={"count": blank_employers},
                )
            )
        metric_columns = list(_MEASURES.values())
        negative_values = frame.select(
            pl.sum_horizontal([(pl.col(column) < 0).fill_null(False) for column in metric_columns])
            .gt(0)
            .sum()
        ).item()
        if negative_values:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="negative_petition_count",
                    message="USCIS petition decision counts cannot be negative",
                    details={"count": int(negative_values)},
                )
            )
        validation = ValidationResult(status=validation_status(issues), issues=tuple(issues))
        record_validation_report(schema.schema_diff_path, validation)
        return NormalizedDataset(
            artifact=artifact,
            frame=frame,
            original_columns=original_columns,
            normalized_columns=tuple(frame.columns),
            column_mapping=schema.column_mapping,
            validation=validation,
            schema_diff_path=schema.schema_diff_path,
        )
