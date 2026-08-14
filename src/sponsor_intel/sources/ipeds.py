"""IPEDS institutional directory adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from sponsor_intel.sources.federal_discovery import discover_ipeds
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

_CONTROL = {"1": "PUBLIC", "2": "PRIVATE_NONPROFIT", "3": "PRIVATE_FOR_PROFIT"}
_SECTOR = {
    "0": "ADMINISTRATIVE_UNIT",
    "1": "PUBLIC_FOUR_YEAR",
    "2": "PRIVATE_NONPROFIT_FOUR_YEAR",
    "3": "PRIVATE_FOR_PROFIT_FOUR_YEAR",
    "4": "PUBLIC_TWO_YEAR",
    "5": "PRIVATE_NONPROFIT_TWO_YEAR",
    "6": "PRIVATE_FOR_PROFIT_TWO_YEAR",
    "7": "PUBLIC_LESS_THAN_TWO_YEAR",
    "8": "PRIVATE_NONPROFIT_LESS_THAN_TWO_YEAR",
    "9": "PRIVATE_FOR_PROFIT_LESS_THAN_TWO_YEAR",
}
_HIGHEST_DEGREE = {
    "0": "NONDEGREE",
    "1": "CERTIFICATE_LESS_THAN_ONE_YEAR",
    "2": "CERTIFICATE_ONE_TO_TWO_YEARS",
    "3": "ASSOCIATE",
    "4": "CERTIFICATE_TWO_TO_FOUR_YEARS",
    "5": "BACHELOR",
    "6": "POST_BACCALAUREATE_CERTIFICATE",
    "7": "MASTER",
    "8": "POST_MASTER_CERTIFICATE",
    "9": "DOCTOR_RESEARCH_SCHOLARSHIP",
    "10": "DOCTOR_PROFESSIONAL_PRACTICE",
    "11": "DOCTOR_OTHER",
    "12": "OTHER",
}


def _domain(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    if not parsed.hostname:
        return None
    return parsed.hostname.casefold().removeprefix("www.").rstrip(".")


class IpedsAdapter(TabularSourceAdapter):
    """Preserve authoritative UNITID-based institution identities."""

    def __init__(
        self,
        config: SourceConfig,
        client,
        data_root: Path,
        output_root: Path,
    ) -> None:
        if config.id != "ipeds":
            raise ValueError(f"IpedsAdapter requires ipeds, received {config.id}")
        super().__init__(config, client, data_root, output_root)

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]:
        report = discover_ipeds(
            self.config,
            self.client,
            from_fiscal_year=context.from_fiscal_year,
        )
        self.last_discovery_report = report
        return list(report.selected)

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        member_name = f"hd{artifact.candidate.fiscal_year}.csv"
        raw = read_csv_artifact(artifact, member_name=member_name)
        original_columns = tuple(raw.columns)
        schema = inspect_schema(self.config, artifact, original_columns, self.report_root)
        frame = raw.rename(dict(zip(original_columns, schema.normalized_columns, strict=True)))
        frame = frame.rename({"control": "control_code", "sector": "sector_code"})
        frame = frame.with_columns(
            pl.col("unitid").str.strip_chars().str.zfill(6).alias("ipeds_unitid"),
            pl.col("instnm").alias("official_name"),
            pl.col("f1sysnam").replace("-2", None).alias("system_name"),
            pl.col("control_code").replace_strict(_CONTROL, default="UNKNOWN").alias("control"),
            pl.col("sector_code").replace_strict(_SECTOR, default="UNKNOWN").alias("sector"),
            pl.col("hloffer")
            .replace_strict(_HIGHEST_DEGREE, default="UNKNOWN")
            .alias("highest_degree"),
            pl.col("webaddr")
            .map_elements(_domain, return_dtype=pl.String)
            .alias("official_domain"),
            pl.when(pl.col("cyactive") == "1")
            .then(pl.lit("ACTIVE"))
            .otherwise(pl.lit("INACTIVE_OR_UNKNOWN"))
            .alias("active_status"),
        ).with_columns(
            (pl.lit("ipeds:") + pl.col("ipeds_unitid")).alias("institution_id"),
            pl.lit(None, dtype=pl.String).alias("legal_entity_id"),
            pl.lit(None, dtype=pl.String).alias("parent_organization_id"),
            pl.lit(1.0).alias("match_confidence"),
            pl.lit("AUTHORITATIVE_SOURCE_ID").alias("review_status"),
            pl.lit(artifact.source_artifact_id).alias("source_artifact_id"),
            pl.lit(self.config.id).alias("source_id"),
            pl.lit(artifact.candidate.fiscal_year, dtype=pl.Int32).alias("directory_year"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
        )

        issues = list(schema.issues)
        if frame.height < self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="minimum_row_count",
                    message="IPEDS institution count is below the configured minimum",
                    details={"actual": frame.height, "minimum": self.config.minimum_row_count},
                )
            )
        invalid_unitids = frame.filter(~pl.col("ipeds_unitid").str.contains(r"^\d{6}$")).height
        duplicate_unitids = frame.select(pl.col("ipeds_unitid").is_duplicated().sum()).item()
        if invalid_unitids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="invalid_unitid",
                    message="IPEDS UNITID must be a six-digit identifier",
                    details={"count": invalid_unitids},
                )
            )
        if duplicate_unitids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="duplicate_unitid",
                    message="IPEDS UNITID must be unique in the directory",
                    details={"count": int(duplicate_unitids)},
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
