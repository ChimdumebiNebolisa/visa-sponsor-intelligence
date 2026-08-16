"""IPEDS institutional directory adapter."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from sponsor_intel.sources.errors import SchemaDriftError
from sponsor_intel.sources.federal_discovery import discover_ipeds
from sponsor_intel.sources.manifests import write_json_atomic
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
from sponsor_intel.sources.normalizer import normalize_column_name
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
        if artifact.candidate.variant.startswith("characteristics_"):
            return self._normalize_characteristics(artifact)
        member_name = f"hd{artifact.candidate.fiscal_year}.csv"
        raw = read_csv_artifact(artifact, member_name=member_name)
        raw_row_count = raw.height
        original_columns = tuple(raw.columns)
        schema = inspect_schema(self.config, artifact, original_columns, self.report_root)
        frame = raw.rename(dict(zip(original_columns, schema.normalized_columns, strict=True)))
        frame = frame.rename({"control": "control_code", "sector": "sector_code"})
        frame = frame.with_columns(
            pl.col("unitid").str.strip_chars().str.zfill(6).alias("ipeds_unitid"),
            pl.col("instnm").alias("official_name"),
            (
                pl.col("ialias").replace("-2", None)
                if "ialias" in frame.columns
                else pl.lit(None, dtype=pl.String)
            ).alias("institution_aliases"),
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
            pl.lit(
                "FINAL" if artifact.candidate.variant.endswith("_final") else "PROVISIONAL"
            ).alias("release_status"),
            pl.lit(artifact.candidate.variant.endswith("_final")).alias("is_finalized"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
            pl.lit(artifact.candidate.download_url).alias("source_url"),
            pl.lit(artifact.sha256).alias("source_sha256"),
            pl.lit(self.config.schema_version).alias("schema_version"),
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
            raw_row_count=raw_row_count,
            original_columns=original_columns,
            normalized_columns=tuple(frame.columns),
            column_mapping=schema.column_mapping,
            validation=validation,
            schema_diff_path=schema.schema_diff_path,
        )

    def _normalize_characteristics(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        """Normalize finalized IC context without treating it as an identity directory."""

        member_name = f"ic{artifact.candidate.fiscal_year}.csv"
        raw = read_csv_artifact(artifact, member_name=member_name)
        raw_row_count = raw.height
        original_columns = tuple(raw.columns)
        normalized_columns = tuple(normalize_column_name(column) for column in original_columns)
        frame = raw.rename(dict(zip(original_columns, normalized_columns, strict=True)))
        if "unitid" not in frame.columns:
            raise SchemaDriftError("IPEDS IC required logical column UNITID is missing")
        schema_diff_path = (
            self.report_root / "schema" / self.config.id / (f"{artifact.source_artifact_id}.json")
        )
        fingerprint = hashlib.sha256(
            ("\n".join(normalized_columns) + "\n").encode("utf-8")
        ).hexdigest()
        write_json_atomic(
            schema_diff_path,
            {
                "source_artifact_id": artifact.source_artifact_id,
                "source_id": self.config.id,
                "fiscal_year": artifact.candidate.fiscal_year,
                "variant": artifact.candidate.variant,
                "schema_version": self.config.schema_version,
                "parser_version": self.config.parser_version,
                "schema_fingerprint": fingerprint,
                "expected_schema_fingerprint": self.config.expected_schema_fingerprints.get(
                    f"{artifact.candidate.variant}:fy{artifact.candidate.fiscal_year}"
                ),
                "schema_fingerprint_changed": False,
                "original_columns": original_columns,
                "normalized_columns": normalized_columns,
                "logical_column_mapping": {"ipeds_unitid": "unitid"},
                "missing_required_columns": [],
                "unexpected_optional_columns": [],
            },
        )

        def optional(name: str) -> pl.Expr:
            return (
                pl.col(name).cast(pl.String, strict=False)
                if name in frame.columns
                else pl.lit(None, dtype=pl.String)
            )

        frame = frame.with_columns(
            pl.col("unitid").str.strip_chars().str.zfill(6).alias("ipeds_unitid"),
            optional("cntlaffi").alias("institution_affiliation_code"),
            optional("calsys").alias("calendar_system_code"),
            optional("openadmp").alias("open_admissions_code"),
            optional("yrscoll").alias("years_of_college_code"),
        ).with_columns(
            (pl.lit("ipeds:") + pl.col("ipeds_unitid")).alias("institution_id"),
            pl.lit(artifact.source_artifact_id).alias("characteristics_source_artifact_id"),
            pl.lit(artifact.candidate.fiscal_year, dtype=pl.Int32).alias("characteristics_year"),
            pl.lit(
                "FINAL" if artifact.candidate.variant.endswith("_final") else "PROVISIONAL"
            ).alias("release_status"),
            pl.lit(artifact.candidate.variant.endswith("_final")).alias("is_finalized"),
            pl.lit(artifact.candidate.file_name).alias("source_file_name"),
            pl.lit(artifact.candidate.download_url).alias("source_url"),
            pl.lit(artifact.sha256).alias("source_sha256"),
            pl.lit(self.config.schema_version).alias("schema_version"),
            pl.lit(datetime.now(UTC).isoformat()).alias("ingested_at"),
        )
        issues: list[ValidationIssue] = []
        if frame.height < self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="minimum_row_count",
                    message="IPEDS characteristics count is below the configured minimum",
                    details={"actual": frame.height, "minimum": self.config.minimum_row_count},
                )
            )
        duplicate_unitids = frame.select(pl.col("ipeds_unitid").is_duplicated().sum()).item()
        if duplicate_unitids:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="duplicate_unitid",
                    message="IPEDS IC UNITID must be unique",
                    details={"count": int(duplicate_unitids)},
                )
            )
        validation = ValidationResult(status=validation_status(issues), issues=tuple(issues))
        record_validation_report(schema_diff_path, validation)
        return NormalizedDataset(
            artifact=artifact,
            frame=frame,
            raw_row_count=raw_row_count,
            original_columns=original_columns,
            normalized_columns=tuple(frame.columns),
            column_mapping={"ipeds_unitid": "unitid"},
            validation=validation,
            schema_diff_path=schema_diff_path,
        )
