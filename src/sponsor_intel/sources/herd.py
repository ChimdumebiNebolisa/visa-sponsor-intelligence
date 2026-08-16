"""NCSES HERD institution-level microdata adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from sponsor_intel.sources.federal_discovery import discover_herd
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


def _metric(questionnaire_no: str, *, column: str | None = None) -> pl.Expr:
    predicate = pl.col("questionnaire_no") == questionnaire_no
    if column is not None:
        predicate &= pl.col("column") == column
    return pl.col("_metric_value").filter(predicate).first()


class HerdAdapter(TabularSourceAdapter):
    """Normalize HERD microdata without guessing institution matches."""

    def __init__(
        self,
        config: SourceConfig,
        client,
        data_root: Path,
        output_root: Path,
    ) -> None:
        if config.id != "herd":
            raise ValueError(f"HerdAdapter requires herd, received {config.id}")
        super().__init__(config, client, data_root, output_root)

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]:
        report = discover_herd(
            self.config,
            self.client,
            from_fiscal_year=context.from_fiscal_year,
        )
        self.last_discovery_report = report
        return list(report.selected)

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        prefix = "short" if artifact.candidate.variant == "short" else "herd"
        member_name = f"{prefix}{artifact.candidate.fiscal_year}.csv"
        raw = read_csv_artifact(artifact, member_name=member_name)
        raw_row_count = raw.height
        original_columns = tuple(raw.columns)
        schema = inspect_schema(self.config, artifact, original_columns, self.report_root)
        raw = raw.rename(dict(zip(original_columns, schema.normalized_columns, strict=True)))
        raw = raw.with_columns(
            pl.col("data")
            .str.replace_all(",", "")
            .cast(pl.Int64, strict=False)
            .alias("_metric_value"),
            pl.col("year").cast(pl.Int32, strict=False),
            pl.col("ipeds_unitid").str.strip_chars().str.zfill(6),
        )
        dimensions = [
            "inst_id",
            "year",
            "ncses_inst_id",
            "ipeds_unitid",
            "inst_name_long",
            "inst_city",
            "inst_state_code",
            "inst_zip",
            "hbcu_flag",
            "med_sch_flag",
        ]
        metrics = [
            _metric("01.g").alias("_total_rd"),
            _metric("01.a").alias("_federal_rd"),
            _metric("01.c").alias("_business_funded_rd"),
            _metric("01.e").alias("_institution_funded_rd"),
        ]
        if artifact.candidate.variant == "short":
            metrics.extend(
                [
                    _metric("02.a", column="Total").alias("_computing_rd"),
                    _metric("02.b", column="Total").alias("_engineering_rd"),
                    pl.lit(None, dtype=pl.Int64).alias("rd_personnel"),
                ]
            )
        else:
            metrics.extend(
                [
                    _metric("09A", column="Total").alias("_computing_federal_rd"),
                    _metric("11A", column="Total").alias("_computing_nonfederal_rd"),
                    _metric("09B10", column="Total").alias("_engineering_federal_rd"),
                    _metric("11B10", column="Total").alias("_engineering_nonfederal_rd"),
                    _metric("15", column="Total").alias("rd_personnel"),
                ]
            )
        frame = raw.group_by(dimensions, maintain_order=True).agg(metrics)
        if artifact.candidate.variant == "standard":
            frame = frame.with_columns(
                pl.when(
                    pl.col("_computing_federal_rd").is_not_null()
                    & pl.col("_computing_nonfederal_rd").is_not_null()
                )
                .then(pl.col("_computing_federal_rd") + pl.col("_computing_nonfederal_rd"))
                .alias("_computing_rd"),
                pl.when(
                    pl.col("_engineering_federal_rd").is_not_null()
                    & pl.col("_engineering_nonfederal_rd").is_not_null()
                )
                .then(pl.col("_engineering_federal_rd") + pl.col("_engineering_nonfederal_rd"))
                .alias("_engineering_rd"),
            )
        expenditure_columns = {
            "_total_rd": "total_rd",
            "_federal_rd": "federal_rd",
            "_business_funded_rd": "business_funded_rd",
            "_institution_funded_rd": "institution_funded_rd",
            "_computing_rd": "computing_rd",
            "_engineering_rd": "engineering_rd",
        }
        expenditure_expressions = [
            (pl.col(source) * 1000).alias(target) for source, target in expenditure_columns.items()
        ]
        frame = frame.with_columns(expenditure_expressions).select(
            *dimensions,
            *expenditure_columns.values(),
            "rd_personnel",
        )
        frame = frame.rename(
            {
                "year": "survey_year",
                "inst_name_long": "institution_name_raw",
                "inst_city": "city",
                "inst_state_code": "state",
                "inst_zip": "zip_code",
            }
        ).with_columns(
            pl.lit(artifact.candidate.variant).alias("survey_form"),
            pl.lit("USD").alias("expenditure_unit"),
            pl.lit(None, dtype=pl.String).alias("institution_id"),
            pl.lit("UNRECONCILED").alias("institution_join_method"),
            pl.lit(None, dtype=pl.Float64).alias("institution_match_confidence"),
            pl.lit("NEEDS_REVIEW").alias("institution_review_status"),
            pl.lit(artifact.source_artifact_id).alias("source_artifact_id"),
            pl.lit(self.config.id).alias("source_id"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
        )

        issues = list(schema.issues)
        if frame.height < self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="minimum_row_count",
                    message="HERD institution count is below the configured minimum",
                    details={"actual": frame.height, "minimum": self.config.minimum_row_count},
                )
            )
        wrong_year = frame.filter(pl.col("survey_year") != artifact.candidate.fiscal_year).height
        duplicate_institutions = frame.select(
            pl.struct(["inst_id", "survey_year"]).is_duplicated().sum()
        ).item()
        if wrong_year:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="survey_year_mismatch",
                    message="HERD record year differs from its source artifact",
                    details={"count": wrong_year},
                )
            )
        if duplicate_institutions:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="duplicate_herd_institution",
                    message="HERD institution identifiers must be unique within one form/year",
                    details={"count": int(duplicate_institutions)},
                )
            )
        missing_total = frame.get_column("total_rd").null_count()
        if missing_total:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.WARNING,
                    category="unknown_total_rd",
                    message="HERD records with unavailable total R&D were preserved as unknown",
                    details={"count": missing_total},
                )
            )
        validation = ValidationResult(status=validation_status(issues), issues=tuple(issues))
        record_validation_report(schema.schema_diff_path, validation)
        return NormalizedDataset(
            artifact=artifact,
            frame=frame,
            raw_row_count=raw_row_count,
            original_columns=original_columns,
            normalized_columns=tuple(frame.columns),
            column_mapping=schema.column_mapping,
            validation=validation,
            schema_diff_path=schema.schema_diff_path,
        )
