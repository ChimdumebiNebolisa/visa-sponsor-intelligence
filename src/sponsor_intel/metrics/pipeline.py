"""Build processed case, employer, institution, and health tables."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import cast

import polars as pl
from polars._typing import PolarsDataType

from sponsor_intel.case_status import canonical_case_status
from sponsor_intel.metrics.models import MetricsBuildSummary
from sponsor_intel.scoring import (
    DEFAULT_PRODUCT_A_SCORING_CONFIG_PATH,
    DEFAULT_SCORING_CONFIG_PATH,
    DEFAULT_SCORING_V2_CONFIG_PATH,
    ProductAScoringConfig,
    ScoringConfig,
    ScoringV2Config,
    score_employers,
    score_employers_product_a,
    score_employers_v2,
    score_institutions,
    score_institutions_product_a,
)
from sponsor_intel.sources.manifests import (
    ArtifactManifestStore,
    active_artifact_records,
    active_layer_paths,
    write_json_atomic,
)
from sponsor_intel.sources.models import ArtifactManifestRecord
from sponsor_intel.sources.registry import DEFAULT_SOURCE_REGISTRY_PATH, SourceRegistry

V1_METRIC_VERSION = "scored_metrics_v1"
V2_METRIC_VERSION = "scored_metrics_v2"
METRIC_VERSION = "product_a_metrics_v1"

LOGGER = logging.getLogger(__name__)

_POLICY_DOCUMENT_REQUIRED_COLUMNS = frozenset({"retrieved_at"})
_POLICY_FACT_REQUIRED_COLUMNS = frozenset(
    {
        "institution_id",
        "human_review_status",
        "exact_excerpt_verified",
        "is_current",
        "valid_to",
        "source_url",
        "fact_value",
        "fact_type",
        "valid_from",
    }
)


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


def _files(
    root: Path,
    source_id: str,
    records: tuple[ArtifactManifestRecord, ...],
    *,
    classified: bool = False,
) -> list[Path]:
    layer = "classified" if classified else "resolved"
    return active_layer_paths(root, layer=layer, records=records, source_id=source_id)


def _organization_id() -> pl.Expr:
    """Keep observed immigration evidence attached to the petitioning legal entity."""

    return pl.col("legal_entity_id").alias("organization_id")


def _source_column(
    source: pl.DataFrame,
    *names: str,
    alias: str,
    dtype: PolarsDataType = pl.String,
) -> pl.Expr:
    """Select the first available canonical/raw source field across form versions."""

    for name in names:
        if name in source.columns:
            return pl.col(name).cast(dtype, strict=False).alias(alias)
    return pl.lit(None, dtype=dtype).alias(alias)


def _read_lca(data_root: Path, records: tuple[ArtifactManifestRecord, ...]) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "dol_lca", records, classified=True):
        source = pl.read_parquet(path)
        frames.append(
            source.select(
                _source_column(
                    source,
                    "source_row_number",
                    alias="source_row_number",
                    dtype=pl.Int64,
                ),
                "case_id",
                "source_artifact_id",
                "source_file_name",
                "ingested_at",
                "fiscal_year",
                "fiscal_quarter",
                "is_partial_period",
                _source_column(source, "visa_class", alias="visa_class"),
                pl.col("case_status").cast(pl.String),
                _source_column(source, "received_date", alias="received_date", dtype=pl.Date),
                pl.col("decision_date").cast(pl.Date, strict=False),
                "employer_name_raw",
                _source_column(
                    source,
                    "employer_address_1",
                    "employer_address1",
                    alias="employer_address_1",
                ),
                _source_column(source, "employer_city", alias="employer_city"),
                _source_column(source, "employer_state", alias="employer_state"),
                _source_column(
                    source,
                    "employer_postal_code",
                    alias="employer_postal_code",
                ),
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                _organization_id(),
                _source_column(source, "entity_match_status", alias="entity_match_status"),
                "job_title_raw",
                pl.col("soc_code").cast(pl.String, strict=False),
                pl.col("soc_title").cast(pl.String, strict=False),
                _source_column(source, "naics_code", alias="naics_code"),
                _source_column(source, "full_time", "full_time_position", alias="full_time"),
                _source_column(
                    source,
                    "worker_positions",
                    "total_worker_positions",
                    alias="worker_positions",
                    dtype=pl.Float64,
                ),
                "role_family",
                "technical_role",
                "role_confidence",
                "classification_method",
                "classification_version",
                "review_status",
                pl.col("worksite_state").cast(pl.String, strict=False),
                _source_column(source, "worksite_city", alias="worksite_city"),
                pl.col("wage_from").cast(pl.Float64, strict=False),
                pl.col("wage_to").cast(pl.Float64, strict=False),
                pl.col("wage_unit").cast(pl.String, strict=False),
                _source_column(
                    source,
                    "prevailing_wage",
                    alias="prevailing_wage",
                    dtype=pl.Float64,
                ),
                _source_column(source, "schema_version", alias="schema_version"),
                _source_column(source, "source_url", alias="source_url"),
                _source_column(source, "source_sha256", alias="source_sha256"),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "case_id"]
    )


def _read_perm(data_root: Path, records: tuple[ArtifactManifestRecord, ...]) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "dol_perm", records, classified=True):
        source = pl.read_parquet(path)
        names = set(source.columns)
        wage_from = next(
            (name for name in ("wage_offer_from", "job_opp_wage_from") if name in names),
            None,
        )
        wage_to = next(
            (name for name in ("wage_offer_to", "job_opp_wage_to") if name in names),
            None,
        )
        wage_unit = next(
            (name for name in ("wage_offer_unit_of_pay", "job_opp_wage_per") if name in names),
            None,
        )
        frames.append(
            source.select(
                _source_column(
                    source,
                    "source_row_number",
                    alias="source_row_number",
                    dtype=pl.Int64,
                ),
                "case_id",
                "source_artifact_id",
                "source_file_name",
                "ingested_at",
                "fiscal_year",
                "fiscal_quarter",
                "is_partial_period",
                pl.col("case_status").cast(pl.String),
                _source_column(source, "received_date", alias="received_date", dtype=pl.Date),
                pl.col("decision_date").cast(pl.Date, strict=False),
                "employer_name_raw",
                _source_column(
                    source,
                    "employer_address_1",
                    alias="employer_address_1",
                ),
                _source_column(source, "employer_city", alias="employer_city"),
                _source_column(
                    source,
                    "employer_state",
                    "employer_state_province",
                    alias="employer_state",
                ),
                _source_column(
                    source,
                    "employer_postal_code",
                    alias="employer_postal_code",
                ),
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                _organization_id(),
                _source_column(source, "entity_match_status", alias="entity_match_status"),
                "job_title_raw",
                pl.col("soc_code").cast(pl.String, strict=False),
                pl.col("soc_title").cast(pl.String, strict=False),
                _source_column(source, "naics_code", alias="naics_code"),
                "role_family",
                "technical_role",
                "role_confidence",
                "classification_method",
                "classification_version",
                "review_status",
                pl.col("worksite_state").cast(pl.String, strict=False),
                _source_column(source, "worksite_city", alias="worksite_city"),
                (
                    pl.col(wage_from).cast(pl.Float64, strict=False)
                    if wage_from is not None
                    else pl.lit(None, dtype=pl.Float64)
                ).alias("wage_from"),
                (
                    pl.col(wage_to).cast(pl.Float64, strict=False)
                    if wage_to is not None
                    else pl.lit(None, dtype=pl.Float64)
                ).alias("wage_to"),
                (
                    pl.col(wage_unit).cast(pl.String, strict=False)
                    if wage_unit is not None
                    else pl.lit(None, dtype=pl.String)
                ).alias("wage_unit"),
                _source_column(
                    source,
                    "prevailing_wage",
                    "pw_wage",
                    "pwd_wage",
                    alias="prevailing_wage",
                    dtype=pl.Float64,
                ),
                _source_column(source, "minimum_education", alias="minimum_education"),
                _source_column(
                    source,
                    "major_field",
                    "major_field_of_study",
                    alias="major_field",
                ),
                _source_column(
                    source,
                    "required_experience_months",
                    "job_opp_experience",
                    alias="required_experience_months",
                    dtype=pl.Float64,
                ),
                _source_column(source, "form_version", alias="form_version"),
                _source_column(source, "schema_version", alias="schema_version"),
                _source_column(source, "source_url", alias="source_url"),
                _source_column(source, "source_sha256", alias="source_sha256"),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "case_id"]
    )


def _read_uscis(data_root: Path, records: tuple[ArtifactManifestRecord, ...]) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "uscis_h1b", records):
        source = pl.read_parquet(path)
        frames.append(
            source.select(
                "source_row_number",
                "source_artifact_id",
                "source_file_name",
                "ingested_at",
                "fiscal_year",
                "is_partial_period",
                "employer_name_raw",
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                _organization_id(),
                _source_column(source, "entity_match_status", alias="entity_match_status"),
                pl.col("initial_approvals").cast(pl.Int64),
                pl.col("initial_denials").cast(pl.Int64),
                pl.col("continuing_approvals").cast(pl.Int64),
                pl.col("continuing_denials").cast(pl.Int64),
                pl.col("state").cast(pl.String, strict=False),
                pl.col("city").cast(pl.String, strict=False),
                pl.col("zip_code").cast(pl.String, strict=False),
                _source_column(source, "source_url", alias="source_url"),
                _source_column(source, "source_sha256", alias="source_sha256"),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "source_row_number"]
    )


def _read_institutions(
    data_root: Path, records: tuple[ArtifactManifestRecord, ...]
) -> pl.DataFrame:
    frames = []
    directory_records = tuple(
        record
        for record in records
        if record.source_id == "ipeds" and record.file_name.upper().startswith("HD")
    )
    for path in _files(data_root, "ipeds", directory_records):
        source = pl.read_parquet(path)
        frames.append(
            source.select(
                "institution_id",
                "ipeds_unitid",
                "official_name",
                _source_column(source, "institution_aliases", alias="institution_aliases"),
                "system_name",
                "control",
                "sector",
                "city",
                pl.col("stabbr").alias("state"),
                "official_domain",
                "highest_degree",
                "active_status",
                _source_column(
                    source,
                    "institution_category",
                    "sector",
                    alias="institution_category",
                ),
                _source_column(source, "release_status", alias="release_status"),
                _source_column(
                    source,
                    "is_finalized",
                    alias="is_finalized",
                    dtype=pl.Boolean,
                ),
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                "match_confidence",
                "review_status",
                "source_artifact_id",
                "directory_year",
                _source_column(source, "source_url", alias="source_url"),
                _source_column(source, "source_sha256", alias="source_sha256"),
                _source_column(source, "schema_version", alias="schema_version"),
            )
        )
    institutions = (
        pl.concat(frames, how="vertical_relaxed")
        .unique("institution_id", keep="last")
        .sort("institution_id")
    )
    supplemental_path = data_root / "processed" / "institutions.parquet"
    if supplemental_path.is_file():
        supplemental_source = pl.read_parquet(supplemental_path)
        supplemental_names = [
            "characteristics_source_artifact_id",
            "characteristics_year",
            "institution_affiliation_code",
            "calendar_system_code",
            "open_admissions_code",
            "years_of_college_code",
        ]
        available = [name for name in supplemental_names if name in supplemental_source.columns]
        if available:
            institutions = institutions.join(
                supplemental_source.select("institution_id", *available).unique(
                    "institution_id", keep="last"
                ),
                on="institution_id",
                how="left",
                validate="1:1",
            )
    return institutions


def _organization_dimension(
    legal_entities: pl.DataFrame,
    parents: pl.DataFrame,
    institutions: pl.DataFrame,
) -> pl.DataFrame:
    parent_lookup = parents.select(
        "parent_organization_id",
        pl.col("canonical_name").alias("parent_organization_name"),
        pl.col("organization_type").alias("parent_organization_type"),
        pl.col("headquarters_state").alias("parent_state"),
        pl.col("is_staffing_or_consulting").alias("parent_is_staffing_or_consulting"),
        pl.col("review_status").alias("parent_relationship_review_status"),
    )
    institution_links = pl.concat(
        [
            institutions.select(pl.col("legal_entity_id").alias("organization_id"), "control"),
            institutions.filter(pl.col("parent_organization_id").is_not_null()).select(
                pl.col("parent_organization_id").alias("organization_id"), "control"
            ),
        ],
        how="vertical_relaxed",
    )
    institution_control = (
        institution_links.filter(pl.col("organization_id").is_not_null())
        .group_by("organization_id")
        .agg(pl.col("control").drop_nulls().first().alias("institution_control"))
    )
    legal_dimension = (
        legal_entities.join(parent_lookup, on="parent_organization_id", how="left")
        .with_columns(
            pl.col("legal_entity_id").alias("organization_id"),
            pl.col("legal_name").alias("organization_name"),
            pl.col("legal_name").alias("legal_entity_name"),
            pl.col("organization_type").alias("organization_type_raw"),
            pl.col("review_status").alias("entity_resolution_status"),
            pl.lit("LEGAL_ENTITY").alias("identity_scope"),
            pl.lit(1, dtype=pl.UInt32).alias("legal_entity_count"),
            pl.coalesce("parent_is_staffing_or_consulting", pl.lit(False)).alias(
                "is_staffing_or_consulting"
            ),
        )
        .select(
            "organization_id",
            "legal_entity_id",
            "parent_organization_id",
            "organization_name",
            "legal_entity_name",
            "parent_organization_name",
            "organization_type_raw",
            pl.col("state"),
            "legal_entity_count",
            "is_staffing_or_consulting",
            "identity_scope",
            "entity_resolution_status",
            "parent_relationship_review_status",
        )
    )
    parent_counts = legal_entities.group_by("parent_organization_id").agg(
        pl.col("legal_entity_id").n_unique().cast(pl.UInt32).alias("legal_entity_count")
    )
    parent_dimension = (
        parents.join(parent_counts, on="parent_organization_id", how="left")
        .with_columns(
            pl.col("parent_organization_id").alias("organization_id"),
            pl.lit(None, dtype=pl.String).alias("legal_entity_id"),
            pl.col("canonical_name").alias("organization_name"),
            pl.lit(None, dtype=pl.String).alias("legal_entity_name"),
            pl.col("canonical_name").alias("parent_organization_name"),
            pl.col("organization_type").alias("organization_type_raw"),
            pl.col("headquarters_state").alias("state"),
            pl.col("legal_entity_count").fill_null(0),
            pl.lit("PARENT_ROLLUP").alias("identity_scope"),
            pl.col("review_status").alias("entity_resolution_status"),
            pl.col("review_status").alias("parent_relationship_review_status"),
        )
        .select(legal_dimension.columns)
    )
    return (
        pl.concat([legal_dimension, parent_dimension], how="vertical_relaxed")
        .join(institution_control, on="organization_id", how="left")
        .with_columns(
            pl.when(pl.col("institution_control") == "PUBLIC")
            .then(pl.lit("university_public"))
            .when(pl.col("institution_control") == "PRIVATE_NONPROFIT")
            .then(pl.lit("university_private_nonprofit"))
            .when(pl.col("institution_control") == "PRIVATE_FOR_PROFIT")
            .then(pl.lit("college_other"))
            .when(pl.col("organization_type_raw").is_in(["TECHNOLOGY", "RETAIL"]))
            .then(pl.lit("for_profit"))
            .when(pl.col("organization_type_raw") == "HIGHER_EDUCATION_SYSTEM")
            .then(pl.lit("university_system"))
            .otherwise(pl.lit("unknown"))
            .alias("organization_type"),
            pl.col("organization_name").alias("display_name"),
            pl.col("organization_name").alias("canonical_name"),
        )
        .sort(["organization_name", "identity_scope", "organization_id"])
    )


def _empty_unresolved_candidate_flags() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "organization_id": pl.String,
            "has_unresolved_h1b_candidate_evidence": pl.Boolean,
            "has_unresolved_perm_candidate_evidence": pl.Boolean,
        }
    )


def _unresolved_candidate_evidence_flags(
    aliases: pl.DataFrame,
    legal_entities: pl.DataFrame,
    parents: pl.DataFrame,
    lca: pl.DataFrame,
    perm: pl.DataFrame,
) -> pl.DataFrame:
    """Flag candidates whose qualifying DOL evidence remains on a separate unresolved entity."""

    required_alias_columns = {
        "legal_entity_id",
        "candidate_legal_entity_id",
        "source_id",
        "match_status",
        "review_status",
    }
    missing_alias_columns = required_alias_columns.difference(aliases.columns)
    if missing_alias_columns:
        missing = ", ".join(sorted(missing_alias_columns))
        raise ValueError(
            f"Entity aliases are missing candidate-resolution columns: {missing}; "
            "refusing to validate zero sponsorship history"
        )

    review_required = aliases.filter(
        pl.col("candidate_legal_entity_id").is_not_null()
        & pl.col("legal_entity_id").is_not_null()
        & (pl.col("candidate_legal_entity_id") != pl.col("legal_entity_id"))
        & (
            pl.col("match_status").cast(pl.String).str.to_uppercase().str.strip_chars()
            == "REVIEW_REQUIRED"
        )
        & (
            pl.col("review_status").cast(pl.String).str.to_uppercase().str.strip_chars()
            == "REVIEW_REQUIRED"
        )
    ).select(
        pl.col("legal_entity_id").alias("unresolved_legal_entity_id"),
        "candidate_legal_entity_id",
        "source_id",
    )
    if review_required.is_empty():
        return _empty_unresolved_candidate_flags()

    def candidate_targets(
        frame: pl.DataFrame,
        *,
        source_id: str,
        program: str,
    ) -> pl.DataFrame:
        status = canonical_case_status()
        qualifying = pl.col("technical_role").fill_null(False)
        if program == "h1b":
            qualifying &= (
                pl.col("visa_class").fill_null("").str.to_uppercase().str.strip_chars() == "H-1B"
            ) & status.is_in(["CERTIFIED", "CERTIFIED-WITHDRAWN"])
        else:
            qualifying &= status.is_in(["CERTIFIED", "CERTIFIED-EXPIRED"])
        qualifying_entities = (
            frame.filter(pl.col("legal_entity_id").is_not_null() & qualifying)
            .select(pl.col("legal_entity_id").alias("unresolved_legal_entity_id"))
            .unique()
        )
        return (
            review_required.filter(pl.col("source_id") == source_id)
            .join(qualifying_entities, on="unresolved_legal_entity_id", how="inner")
            .select("candidate_legal_entity_id")
            .unique()
            .with_columns(pl.lit(True).alias(f"has_unresolved_{program}_candidate_evidence"))
        )

    h1b_candidates = candidate_targets(lca, source_id="dol_lca", program="h1b")
    perm_candidates = candidate_targets(perm, source_id="dol_perm", program="perm")
    candidates = (
        h1b_candidates.join(perm_candidates, on="candidate_legal_entity_id", how="full")
        .with_columns(
            pl.coalesce("candidate_legal_entity_id", "candidate_legal_entity_id_right").alias(
                "candidate_legal_entity_id"
            ),
            pl.col("has_unresolved_h1b_candidate_evidence").fill_null(False),
            pl.col("has_unresolved_perm_candidate_evidence").fill_null(False),
        )
        .drop("candidate_legal_entity_id_right")
    )
    if candidates.is_empty():
        return _empty_unresolved_candidate_flags()

    accepted_resolution_statuses = [
        "DETERMINISTIC",
        "HIGH_CONFIDENCE_AUTO",
        "MANUAL_OVERRIDE",
    ]
    candidate_entities = candidates.join(
        legal_entities.select(
            pl.col("legal_entity_id").alias("candidate_legal_entity_id"),
            "parent_organization_id",
            pl.col("review_status").alias("candidate_review_status"),
        ),
        on="candidate_legal_entity_id",
        how="inner",
    )
    legal_flags = candidate_entities.select(
        pl.col("candidate_legal_entity_id").alias("organization_id"),
        "has_unresolved_h1b_candidate_evidence",
        "has_unresolved_perm_candidate_evidence",
    )
    reviewed_parents = parents.filter(
        pl.col("review_status").is_in(accepted_resolution_statuses)
    ).select("parent_organization_id")
    parent_flags = (
        candidate_entities.filter(
            pl.col("parent_organization_id").is_not_null()
            & pl.col("candidate_review_status").is_in(accepted_resolution_statuses)
        )
        .join(reviewed_parents, on="parent_organization_id", how="inner")
        .select(
            pl.col("parent_organization_id").alias("organization_id"),
            "has_unresolved_h1b_candidate_evidence",
            "has_unresolved_perm_candidate_evidence",
        )
    )
    return (
        pl.concat([legal_flags, parent_flags], how="vertical_relaxed")
        .group_by("organization_id")
        .agg(
            pl.col("has_unresolved_h1b_candidate_evidence").any(),
            pl.col("has_unresolved_perm_candidate_evidence").any(),
        )
        .sort("organization_id")
    )


def _program_metrics(
    frame: pl.DataFrame,
    *,
    prefix: str,
    relevant: pl.Expr,
) -> pl.DataFrame:
    keyed = frame.filter(pl.col("organization_id").is_not_null())
    aggregate = keyed.group_by("organization_id").agg(
        pl.len().alias(f"{prefix}_case_count"),
        relevant.cast(pl.Int64).sum().alias(f"relevant_{prefix}_count"),
        pl.col("fiscal_year").n_unique().alias(f"{prefix}_active_years"),
        pl.col("fiscal_year").max().alias(f"last_{prefix}_activity_year"),
        pl.col("fiscal_year")
        .filter(~pl.col("is_partial_period"))
        .max()
        .alias(f"{prefix}_latest_complete_fiscal_year"),
        pl.col("fiscal_year")
        .filter(pl.col("is_partial_period"))
        .max()
        .alias(f"{prefix}_partial_fiscal_year"),
        pl.col("fiscal_quarter")
        .filter(pl.col("is_partial_period"))
        .max()
        .alias(f"{prefix}_partial_quarter"),
        pl.col("is_partial_period").any().alias(f"{prefix}_has_partial_period"),
        pl.col("role_family")
        .filter(pl.col("technical_role") == True)  # noqa: E712
        .drop_nulls()
        .unique()
        .sort()
        .alias(f"{prefix}_role_families"),
        pl.col("worksite_state").drop_nulls().unique().sort().alias(f"{prefix}_worksite_states"),
    )
    family = (
        keyed.filter(pl.col("technical_role") == True)  # noqa: E712
        .group_by("organization_id", "role_family")
        .len(name="family_count")
        .sort(
            ["organization_id", "family_count", "role_family"],
            descending=[False, True, False],
        )
        .group_by("organization_id", maintain_order=True)
        .first()
        .select(
            "organization_id",
            pl.col("role_family").alias(f"top_{prefix}_role_family"),
            pl.col("family_count").alias(f"top_{prefix}_role_family_count"),
        )
    )
    title = (
        keyed.filter(pl.col("technical_role") == True)  # noqa: E712
        .group_by("organization_id", "job_title_raw")
        .len(name="title_count")
        .sort(
            ["organization_id", "title_count", "job_title_raw"],
            descending=[False, True, False],
        )
        .group_by("organization_id", maintain_order=True)
        .first()
        .select(
            "organization_id",
            pl.col("job_title_raw").alias(f"top_{prefix}_technical_title"),
            pl.col("title_count").alias(f"top_{prefix}_technical_title_count"),
        )
    )
    yearly = (
        keyed.group_by("organization_id", "fiscal_year")
        .agg(
            pl.len().alias("case_count"),
            relevant.cast(pl.Int64).sum().alias("relevant_count"),
            pl.col("is_partial_period").any().alias("is_partial_period"),
        )
        .sort(["organization_id", "fiscal_year"])
        .group_by("organization_id", maintain_order=True)
        .agg(
            pl.struct("fiscal_year", "case_count", "relevant_count", "is_partial_period").alias(
                f"{prefix}_counts_by_fy"
            )
        )
    )
    return (
        aggregate.join(family, on="organization_id", how="left")
        .join(title, on="organization_id", how="left")
        .join(yearly, on="organization_id", how="left")
    )


def _with_parent_rollups(frame: pl.DataFrame) -> pl.DataFrame:
    """Add reviewed parent-scope copies while retaining every legal-entity observation."""

    legal = frame.with_columns(
        pl.col("legal_entity_id").alias("organization_id"),
        pl.lit("LEGAL_ENTITY").alias("evidence_identity_scope"),
    )
    parent = frame.filter(pl.col("parent_organization_id").is_not_null()).with_columns(
        pl.col("parent_organization_id").alias("organization_id"),
        pl.lit("PARENT_ROLLUP").alias("evidence_identity_scope"),
    )
    return pl.concat([legal, parent], how="vertical_relaxed")


def _product_a_program_metrics(frame: pl.DataFrame, *, program: str) -> pl.DataFrame:
    """Aggregate qualifying technical history with explicit status weights."""

    if program not in {"lca", "perm"}:
        raise ValueError(f"Unsupported Product A program: {program}")
    keyed = _with_parent_rollups(frame).filter(pl.col("organization_id").is_not_null())
    status = canonical_case_status()
    technical = pl.col("technical_role").fill_null(False)
    if program == "lca":
        relevant_base = technical & (
            pl.col("visa_class").fill_null("").str.to_uppercase().str.strip_chars() == "H-1B"
        )
        full = relevant_base & (status == "CERTIFIED")
        half = relevant_base & (status == "CERTIFIED-WITHDRAWN")
        full_name = "relevant_certified_lca_count"
        half_name = "relevant_certified_withdrawn_lca_count"
    else:
        relevant_base = technical
        full = relevant_base & (status == "CERTIFIED")
        half = relevant_base & (status == "CERTIFIED-EXPIRED")
        full_name = "relevant_certified_perm_count"
        half_name = "relevant_certified_expired_perm_count"
    positive = full | half
    weight = pl.when(full).then(pl.lit(1.0)).when(half).then(pl.lit(0.5)).otherwise(0.0)

    aggregate = keyed.group_by("organization_id").agg(
        pl.len().alias(f"{program}_case_count"),
        full.cast(pl.Int64).sum().alias(full_name),
        half.cast(pl.Int64).sum().alias(half_name),
        weight.sum().alias(f"weighted_relevant_{program}_count"),
        pl.col("fiscal_year").filter(positive).n_unique().alias(f"{program}_active_years"),
        pl.col("fiscal_year")
        .filter(positive & ~pl.col("is_partial_period"))
        .n_unique()
        .alias(f"{program}_complete_active_years"),
        pl.col("fiscal_year")
        .filter(positive)
        .max()
        .alias(f"last_relevant_{program}_activity_year"),
        pl.col("fiscal_year").max().alias(f"last_{program}_activity_year"),
        pl.col("fiscal_year")
        .filter(~pl.col("is_partial_period"))
        .max()
        .alias(f"{program}_latest_complete_fiscal_year"),
        pl.col("fiscal_year")
        .filter(pl.col("is_partial_period"))
        .max()
        .alias(f"{program}_partial_fiscal_year"),
        pl.col("fiscal_quarter")
        .filter(pl.col("is_partial_period"))
        .max()
        .alias(f"{program}_partial_quarter"),
        pl.col("is_partial_period").any().alias(f"{program}_has_partial_period"),
        pl.col("role_family")
        .filter(positive)
        .drop_nulls()
        .unique()
        .sort()
        .alias(f"{program}_relevant_job_families"),
        pl.col("role_family")
        .filter(positive)
        .drop_nulls()
        .n_unique()
        .alias(f"{program}_relevant_job_family_count"),
        pl.col("worksite_state")
        .filter(positive)
        .drop_nulls()
        .unique()
        .sort()
        .alias(f"{program}_worksite_states"),
    )
    family = (
        keyed.filter(positive & pl.col("role_family").is_not_null())
        .group_by("organization_id", "role_family")
        .agg(weight.sum().alias("family_count"))
        .sort(
            ["organization_id", "family_count", "role_family"],
            descending=[False, True, False],
        )
        .group_by("organization_id", maintain_order=True)
        .first()
        .select(
            "organization_id",
            pl.col("role_family").alias(f"top_{program}_role_family"),
            pl.col("family_count").alias(f"top_{program}_role_family_count"),
        )
    )
    title = (
        keyed.filter(positive & pl.col("job_title_raw").is_not_null())
        .group_by("organization_id", "job_title_raw")
        .agg(weight.sum().alias("title_count"))
        .sort(
            ["organization_id", "title_count", "job_title_raw"],
            descending=[False, True, False],
        )
        .group_by("organization_id", maintain_order=True)
        .first()
        .select(
            "organization_id",
            pl.col("job_title_raw").alias(f"top_{program}_technical_title"),
            pl.col("title_count").alias(f"top_{program}_technical_title_count"),
        )
    )
    yearly = (
        keyed.group_by("organization_id", "fiscal_year")
        .agg(
            pl.len().alias("case_count"),
            full.cast(pl.Int64).sum().alias("certified_count"),
            half.cast(pl.Int64).sum().alias("qualified_secondary_status_count"),
            weight.sum().round(2).alias("weighted_relevant_count"),
            pl.col("is_partial_period").any().alias("is_partial_period"),
        )
        .sort(["organization_id", "fiscal_year"])
        .group_by("organization_id", maintain_order=True)
        .agg(
            pl.struct(
                "fiscal_year",
                "case_count",
                "certified_count",
                "qualified_secondary_status_count",
                "weighted_relevant_count",
                "is_partial_period",
            ).alias(f"{program}_counts_by_fy")
        )
    )
    return (
        aggregate.join(family, on="organization_id", how="left")
        .join(title, on="organization_id", how="left")
        .join(yearly, on="organization_id", how="left")
        .with_columns(pl.col(f"{program}_relevant_job_families").alias(f"{program}_role_families"))
    )


def _uscis_metrics(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        _with_parent_rollups(frame)
        .filter(pl.col("organization_id").is_not_null())
        .group_by("organization_id")
        .agg(
            pl.len().alias("uscis_employer_year_rows"),
            pl.col("initial_approvals").sum(),
            pl.col("initial_denials").sum(),
            pl.col("continuing_approvals").sum(),
            pl.col("continuing_denials").sum(),
            pl.col("fiscal_year").n_unique().alias("uscis_active_years"),
            pl.col("fiscal_year")
            .filter(~pl.col("is_partial_period") & (pl.col("initial_approvals") > 0))
            .n_unique()
            .alias("uscis_complete_active_years"),
            pl.col("fiscal_year").max().alias("last_uscis_activity_year"),
            pl.col("fiscal_year")
            .filter(~pl.col("is_partial_period"))
            .max()
            .alias("uscis_latest_complete_fiscal_year"),
            pl.col("fiscal_year")
            .filter(pl.col("is_partial_period"))
            .max()
            .alias("uscis_partial_fiscal_year"),
            pl.col("is_partial_period").any().alias("uscis_has_partial_period"),
        )
    )


def _employer_metrics(
    dimension: pl.DataFrame,
    institutions: pl.DataFrame,
    lca: pl.DataFrame,
    perm: pl.DataFrame,
    uscis: pl.DataFrame,
    unresolved_candidate_flags: pl.DataFrame,
) -> pl.DataFrame:
    result = (
        dimension.join(
            _product_a_program_metrics(lca, program="lca"),
            on="organization_id",
            how="left",
        )
        .join(
            _product_a_program_metrics(perm, program="perm"),
            on="organization_id",
            how="left",
        )
        .join(_uscis_metrics(uscis), on="organization_id", how="left")
        .join(unresolved_candidate_flags, on="organization_id", how="left")
    )
    institution_orgs = pl.concat(
        [
            institutions.select(pl.col("legal_entity_id").alias("organization_id")),
            institutions.filter(pl.col("parent_organization_id").is_not_null()).select(
                pl.col("parent_organization_id").alias("organization_id")
            ),
        ],
        how="vertical_relaxed",
    )
    institution_orgs = (
        institution_orgs.select("organization_id")
        .unique()
        .with_columns(pl.lit(True).alias("is_higher_education"))
    )
    count_columns = [
        "lca_case_count",
        "relevant_certified_lca_count",
        "relevant_certified_withdrawn_lca_count",
        "weighted_relevant_lca_count",
        "lca_active_years",
        "lca_complete_active_years",
        "lca_relevant_job_family_count",
        "perm_case_count",
        "relevant_certified_perm_count",
        "relevant_certified_expired_perm_count",
        "weighted_relevant_perm_count",
        "perm_active_years",
        "perm_complete_active_years",
        "perm_relevant_job_family_count",
        "uscis_employer_year_rows",
        "initial_approvals",
        "initial_denials",
        "continuing_approvals",
        "continuing_denials",
        "uscis_active_years",
        "uscis_complete_active_years",
    ]
    lca_complete_year_count = lca.filter(~pl.col("is_partial_period"))["fiscal_year"].n_unique()
    perm_complete_year_count = perm.filter(~pl.col("is_partial_period"))["fiscal_year"].n_unique()
    lca_latest_complete = cast(
        int | None,
        lca.filter(~pl.col("is_partial_period"))["fiscal_year"].max(),
    )
    perm_latest_complete = cast(
        int | None,
        perm.filter(~pl.col("is_partial_period"))["fiscal_year"].max(),
    )
    latest_complete = max(lca_latest_complete or 0, perm_latest_complete or 0)
    partial_years = pl.concat(
        [
            lca.filter(pl.col("is_partial_period")).select("fiscal_year"),
            perm.filter(pl.col("is_partial_period")).select("fiscal_year"),
        ]
    )
    current_partial = cast(
        int | None,
        partial_years["fiscal_year"].max() if not partial_years.is_empty() else None,
    )
    result = result.join(institution_orgs, on="organization_id", how="left").with_columns(
        [pl.col(column).fill_null(0) for column in count_columns]
        + [
            pl.col("is_higher_education").fill_null(False),
            pl.col("lca_has_partial_period").fill_null(False),
            pl.col("perm_has_partial_period").fill_null(False),
            pl.col("uscis_has_partial_period").fill_null(False),
            pl.col("has_unresolved_h1b_candidate_evidence").fill_null(False),
            pl.col("has_unresolved_perm_candidate_evidence").fill_null(False),
        ]
    )
    result = (
        result.with_columns(
            pl.col("relevant_certified_lca_count").alias("relevant_lca_count"),
            (
                (pl.col("lca_case_count") > 0).cast(pl.Int8)
                + (pl.col("perm_case_count") > 0).cast(pl.Int8)
                + (pl.col("uscis_employer_year_rows") > 0).cast(pl.Int8)
            ).alias("source_coverage_count"),
            pl.max_horizontal(
                "last_relevant_lca_activity_year",
                "last_relevant_perm_activity_year",
                "last_uscis_activity_year",
            ).alias("last_observed_activity_year"),
            pl.max_horizontal(
                "lca_latest_complete_fiscal_year",
                "perm_latest_complete_fiscal_year",
                "uscis_latest_complete_fiscal_year",
            ).alias("latest_complete_fiscal_year"),
            pl.max_horizontal(
                "last_relevant_lca_activity_year",
                "last_relevant_perm_activity_year",
                "last_uscis_activity_year",
            ).alias("latest_observed_year"),
            pl.max_horizontal("lca_complete_active_years", "perm_complete_active_years").alias(
                "complete_active_years"
            ),
            pl.max_horizontal(
                "lca_partial_fiscal_year", "perm_partial_fiscal_year", "uscis_partial_fiscal_year"
            ).alias("current_partial_fiscal_year"),
            pl.max_horizontal("lca_partial_quarter", "perm_partial_quarter").alias(
                "current_partial_quarter"
            ),
            (
                pl.col("lca_has_partial_period")
                | pl.col("perm_has_partial_period")
                | pl.col("uscis_has_partial_period")
            ).alias("has_partial_period"),
            pl.lit("UNKNOWN").alias("everify_status"),
            pl.lit("UNKNOWN").alias("known_opt_observation"),
            pl.when(pl.col("is_higher_education"))
            .then(pl.lit("HIGHER_EDUCATION_CONTEXT_VERIFY_CAP_EXEMPTION"))
            .otherwise(pl.lit("UNKNOWN"))
            .alias("cap_exemption_status"),
            pl.col("entity_resolution_status")
            .is_in(["DETERMINISTIC", "HIGH_CONFIDENCE_AUTO", "MANUAL_OVERRIDE"])
            .alias("entity_resolution_valid"),
            pl.lit(True).alias("lca_source_valid"),
            pl.lit(True).alias("perm_source_valid"),
            pl.lit(True).alias("uscis_source_valid"),
            pl.lit(lca_complete_year_count).alias("lca_complete_fiscal_year_count"),
            pl.lit(perm_complete_year_count).alias("perm_complete_fiscal_year_count"),
            pl.lit(latest_complete).alias("latest_complete_immigration_fiscal_year"),
            pl.lit(current_partial, dtype=pl.Int64).alias(
                "current_partial_immigration_fiscal_year"
            ),
            pl.lit("NOT_SCORED").alias("evidence_confidence"),
            pl.lit(None, dtype=pl.Float64).alias("h1b_activity_score"),
            pl.lit(None, dtype=pl.Float64).alias("immigration_evidence_score"),
            pl.lit("OBSERVED_GOVERNMENT_RECORD|DERIVED_METRIC").alias("evidence_classes"),
            pl.lit(METRIC_VERSION).alias("metric_version"),
        )
        .with_columns(
            pl.when(~pl.col("entity_resolution_valid"))
            .then(pl.lit("UNRESOLVED_IDENTITY"))
            .when(
                pl.col("has_unresolved_h1b_candidate_evidence")
                & (pl.col("weighted_relevant_lca_count") > 0)
            )
            .then(pl.lit("PARTIAL_ENTITY_COVERAGE"))
            .when(pl.col("has_unresolved_h1b_candidate_evidence"))
            .then(pl.lit("UNRESOLVED_IDENTITY"))
            .otherwise(pl.lit("COMPLETE_ENTITY_COVERAGE"))
            .alias("h1b_entity_coverage_state"),
            pl.when(~pl.col("entity_resolution_valid"))
            .then(pl.lit("UNRESOLVED_IDENTITY"))
            .when(
                pl.col("has_unresolved_perm_candidate_evidence")
                & (pl.col("weighted_relevant_perm_count") > 0)
            )
            .then(pl.lit("PARTIAL_ENTITY_COVERAGE"))
            .when(pl.col("has_unresolved_perm_candidate_evidence"))
            .then(pl.lit("UNRESOLVED_IDENTITY"))
            .otherwise(pl.lit("COMPLETE_ENTITY_COVERAGE"))
            .alias("perm_entity_coverage_state"),
            (pl.col("source_coverage_count") / 3).alias("source_coverage_ratio"),
        )
        .with_columns(
            (pl.col("h1b_entity_coverage_state") != "UNRESOLVED_IDENTITY").alias(
                "h1b_entity_resolution_valid"
            ),
            (pl.col("perm_entity_coverage_state") != "UNRESOLVED_IDENTITY").alias(
                "perm_entity_resolution_valid"
            ),
            pl.when(
                (pl.col("h1b_entity_coverage_state") == "UNRESOLVED_IDENTITY")
                | (pl.col("perm_entity_coverage_state") == "UNRESOLVED_IDENTITY")
            )
            .then(pl.lit("UNRESOLVED_IDENTITY"))
            .when(
                (pl.col("h1b_entity_coverage_state") == "PARTIAL_ENTITY_COVERAGE")
                | (pl.col("perm_entity_coverage_state") == "PARTIAL_ENTITY_COVERAGE")
            )
            .then(pl.lit("PARTIAL_ENTITY_COVERAGE"))
            .otherwise(pl.lit("COMPLETE_ENTITY_COVERAGE"))
            .alias("entity_coverage_state"),
        )
    )
    return result.sort(
        ["weighted_relevant_lca_count", "organization_name"], descending=[True, False]
    )


def _legal_program_metrics(
    frame: pl.DataFrame,
    *,
    prefix: str,
    relevant: pl.Expr,
) -> pl.DataFrame:
    aggregate = (
        frame.filter(pl.col("legal_entity_id").is_not_null())
        .group_by("legal_entity_id")
        .agg(
            pl.len().alias(f"{prefix}_case_count"),
            relevant.cast(pl.Int64).sum().alias(f"relevant_{prefix}_count"),
            pl.col("fiscal_year").n_unique().alias(f"{prefix}_active_years"),
            pl.col("fiscal_year").max().alias(f"last_{prefix}_activity_year"),
        )
    )
    title = (
        frame.filter(
            pl.col("legal_entity_id").is_not_null()
            & relevant
            & pl.col("job_title_raw").is_not_null()
        )
        .group_by("legal_entity_id", "job_title_raw")
        .len(name="title_count")
        .sort(
            ["legal_entity_id", "title_count", "job_title_raw"],
            descending=[False, True, False],
        )
        .group_by("legal_entity_id", maintain_order=True)
        .first()
        .select(
            "legal_entity_id",
            pl.col("job_title_raw").alias(f"top_{prefix}_technical_title"),
            pl.col("title_count").alias(f"top_{prefix}_technical_title_count"),
        )
    )
    return aggregate.join(title, on="legal_entity_id", how="left")


def _latest_herd_context(herd: pl.DataFrame) -> pl.DataFrame:
    """Use one shared latest HERD cohort for comparable Research Scale percentiles."""

    latest_survey_year = herd["survey_year"].max()
    return (
        herd.filter(pl.col("survey_year") == latest_survey_year)
        .sort(["institution_id", "survey_form"])
        .select(
            "institution_id",
            "survey_year",
            "total_rd",
            "federal_rd",
            "computing_rd",
            "engineering_rd",
            "rd_personnel",
            "survey_form",
        )
    )


def _validate_herd_institution_year_grain(herd: pl.DataFrame) -> None:
    """Reject duplicate matched institutions without conflating distinct unmatched rows."""

    duplicate_institution_year = (
        herd.filter(pl.col("institution_id").is_not_null())
        .group_by("institution_id", "survey_year")
        .len()
        .filter(pl.col("len") > 1)
    )
    if not duplicate_institution_year.is_empty():
        raise ValueError(
            "HERD full/short reconciliation produced duplicate institution-year rows; "
            "refusing to double-count research context"
        )


def _institution_metrics(
    institutions: pl.DataFrame,
    parents: pl.DataFrame,
    herd: pl.DataFrame,
    employer_metrics: pl.DataFrame,
) -> pl.DataFrame:
    _validate_herd_institution_year_grain(herd)
    herd_latest = _latest_herd_context(herd)
    parent_lookup = parents.select(
        "parent_organization_id",
        pl.col("canonical_name").alias("parent_organization_name"),
    )
    legal_history = employer_metrics.filter(pl.col("identity_scope") == "LEGAL_ENTITY").drop(
        "parent_organization_id",
        "parent_organization_name",
        "state",
        "organization_type",
        strict=False,
    )
    result = (
        institutions.join(parent_lookup, on="parent_organization_id", how="left")
        .with_columns(
            pl.coalesce("parent_organization_name", "system_name").alias("parent_system"),
            pl.col("legal_entity_id").alias("organization_id"),
        )
        .join(herd_latest, on="institution_id", how="left")
        .join(legal_history, on=["organization_id", "legal_entity_id"], how="left")
    )
    return (
        result.with_columns(
            pl.col("survey_year").is_not_null().alias("has_herd_data"),
            (pl.col("survey_year").is_not_null() & pl.col("total_rd").is_not_null()).alias(
                "has_total_rd_data"
            ),
            (pl.col("survey_year").is_not_null() & pl.col("federal_rd").is_not_null()).alias(
                "has_federal_rd_data"
            ),
            (pl.col("survey_year").is_not_null() & pl.col("computing_rd").is_not_null()).alias(
                "has_computing_rd_data"
            ),
            (pl.col("survey_year").is_not_null() & pl.col("engineering_rd").is_not_null()).alias(
                "has_engineering_rd_data"
            ),
        )
        .with_columns(
            pl.col("official_name").alias("institution_name"),
            pl.col("organization_name").alias("legal_employer_name"),
            pl.lit(
                "Higher-education institution; exact cap-exempt status requires verification."
            ).alias("higher_education_context"),
            pl.lit("UNKNOWN").alias("research_staff_h1b_policy"),
            pl.lit("UNKNOWN").alias("research_staff_permanent_residence_policy"),
            pl.lit("UNKNOWN").alias("general_staff_permanent_residence_policy"),
            pl.lit("UNKNOWN").alias("perm_support"),
            pl.lit("UNKNOWN").alias("eb1b_support"),
            pl.lit("NOT_STARTED").alias("policy_review_status"),
            pl.lit("Supplemental; incomplete; not used in sponsorship ratings").alias(
                "policy_evidence_role"
            ),
            pl.lit(None, dtype=pl.Float64).alias("research_pathway_score"),
            pl.lit("OBSERVED_GOVERNMENT_RECORD|DERIVED_METRIC").alias("evidence_classes"),
            pl.lit(METRIC_VERSION).alias("metric_version"),
        )
        .sort(
            ["weighted_relevant_perm_count", "weighted_relevant_lca_count", "official_name"],
            descending=[True, True, False],
        )
    )


def _source_health(
    lca: pl.DataFrame,
    perm: pl.DataFrame,
    uscis: pl.DataFrame,
    institutions: pl.DataFrame,
    herd: pl.DataFrame,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for source_id, frame, year_column, quarter_column in (
        ("dol_lca", lca, "fiscal_year", "fiscal_quarter"),
        ("dol_perm", perm, "fiscal_year", "fiscal_quarter"),
        ("uscis_h1b", uscis, "fiscal_year", None),
    ):
        complete = frame.filter(~pl.col("is_partial_period"))
        partial = frame.filter(pl.col("is_partial_period"))
        rows.append(
            {
                "source_id": source_id,
                "row_count": frame.height,
                "earliest_year": frame[year_column].min(),
                "latest_year": frame[year_column].max(),
                "latest_complete_fiscal_year": complete[year_column].max(),
                "current_partial_fiscal_year": partial[year_column].max(),
                "current_partial_quarter": (
                    partial[quarter_column].max() if quarter_column and partial.height else None
                ),
                "has_partial_period": partial.height > 0,
                "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
            }
        )
    rows.extend(
        [
            {
                "source_id": "ipeds",
                "row_count": institutions.height,
                "earliest_year": institutions["directory_year"].min(),
                "latest_year": institutions["directory_year"].max(),
                "latest_complete_fiscal_year": institutions["directory_year"].max(),
                "current_partial_fiscal_year": None,
                "current_partial_quarter": None,
                "has_partial_period": False,
                "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
            },
            {
                "source_id": "herd",
                "row_count": herd.height,
                "earliest_year": herd["survey_year"].min(),
                "latest_year": herd["survey_year"].max(),
                "latest_complete_fiscal_year": herd["survey_year"].max(),
                "current_partial_fiscal_year": None,
                "current_partial_quarter": None,
                "has_partial_period": False,
                "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
            },
        ]
    )
    return pl.DataFrame(rows).with_columns(
        pl.when(pl.col("has_partial_period"))
        .then(
            pl.concat_str(
                pl.lit("Partial FY"),
                pl.col("current_partial_fiscal_year"),
                pl.lit(" data must not be compared with complete years without a warning."),
            )
        )
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("freshness_warning")
    )


def _selected_source_artifacts(
    output_root: Path,
    frames: tuple[pl.DataFrame, ...],
) -> pl.DataFrame:
    """Materialize selected artifact provenance for Data Health and acceptance."""

    schema = {
        "source_artifact_id": pl.String,
        "source_id": pl.String,
        "authority": pl.String,
        "landing_page_url": pl.String,
        "download_url": pl.String,
        "retrieved_at": pl.String,
        "fiscal_year": pl.Int64,
        "fiscal_quarter": pl.Int64,
        "is_partial_period": pl.Boolean,
        "is_quarter_partition": pl.Boolean,
        "coverage_start_quarter": pl.Int64,
        "file_name": pl.String,
        "mime_type": pl.String,
        "byte_size": pl.Int64,
        "sha256": pl.String,
        "record_layout_url": pl.String,
        "parser_version": pl.String,
        "schema_version": pl.String,
        "raw_row_count": pl.Int64,
        "normalized_row_count": pl.Int64,
        "validation_status": pl.String,
    }
    manifest_path = output_root / "manifests" / "source_artifacts.jsonl"
    records = ArtifactManifestStore(manifest_path).records()
    if not records:
        return pl.DataFrame(schema=schema)
    observed_counts: dict[str, int] = {}
    for frame in frames:
        artifact_columns = [
            column
            for column in frame.columns
            if column == "source_artifact_id" or column.endswith("_source_artifact_id")
        ]
        for column in artifact_columns:
            for row in (
                frame.filter(pl.col(column).is_not_null())
                .group_by(column)
                .len()
                .iter_rows(named=True)
            ):
                artifact_id = str(row[column])
                observed_counts[artifact_id] = observed_counts.get(artifact_id, 0) + int(row["len"])
    rows: list[dict[str, object]] = []
    for record in records:
        if record.source_artifact_id not in observed_counts:
            continue
        rows.append(
            {
                "source_artifact_id": record.source_artifact_id,
                "source_id": record.source_id,
                "authority": record.authority,
                "landing_page_url": str(record.landing_page_url),
                "download_url": str(record.download_url),
                "retrieved_at": record.retrieved_at.isoformat(),
                "fiscal_year": record.fiscal_year,
                "fiscal_quarter": record.fiscal_quarter,
                "is_partial_period": record.is_partial_period,
                "is_quarter_partition": record.is_quarter_partition,
                "coverage_start_quarter": record.coverage_start_quarter,
                "file_name": record.file_name,
                "mime_type": record.mime_type,
                "byte_size": record.byte_size,
                "sha256": record.sha256,
                "record_layout_url": (
                    str(record.record_layout_url) if record.record_layout_url else None
                ),
                "parser_version": record.parser_version,
                "schema_version": record.schema_version,
                "raw_row_count": record.raw_row_count,
                "normalized_row_count": record.row_count,
                "validation_status": record.validation_status,
            }
        )
    return (
        pl.DataFrame(rows, schema=schema).sort(
            ["source_id", "fiscal_year", "fiscal_quarter", "file_name"]
        )
        if rows
        else pl.DataFrame(schema=schema)
    )


def _phase6_frames(data_root: Path) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    processed = data_root / "processed"
    everify_path = processed / "everify_observations.parquet"
    opt_path = processed / "opt_employer_observations.parquet"
    everify = pl.read_parquet(everify_path) if everify_path.is_file() else None
    opt = pl.read_parquet(opt_path) if opt_path.is_file() else None
    return everify, opt


def _enrich_phase6_metrics(
    frame: pl.DataFrame,
    *,
    everify: pl.DataFrame | None,
    opt: pl.DataFrame | None,
) -> pl.DataFrame:
    result = frame
    if everify is not None and not everify.is_empty():
        latest_everify = (
            everify.filter(pl.col("organization_id").is_not_null())
            .sort(["organization_id", "retrieved_at"])
            .group_by("organization_id", maintain_order=True)
            .last()
            .select(
                "organization_id",
                pl.when(
                    pl.col("enrollment_status").is_in(["CONFIRMED_ACTIVE", "CONFIRMED_INACTIVE"])
                )
                .then(pl.col("enrollment_status"))
                .otherwise(pl.lit("UNKNOWN"))
                .alias("_everify_status"),
                pl.col("enrollment_status").alias("everify_lookup_status"),
                pl.col("retrieved_at").alias("everify_retrieved_at"),
                pl.col("source_url").alias("everify_source_url"),
                pl.col("match_confidence").alias("everify_match_confidence"),
                pl.col("review_status").alias("everify_review_status"),
            )
        )
        result = (
            result.join(latest_everify, on="organization_id", how="left")
            .with_columns(
                pl.coalesce("_everify_status", "everify_status").alias("everify_status"),
                pl.col("everify_lookup_status").fill_null("NOT_CHECKED"),
            )
            .drop("_everify_status")
        )
    else:
        result = result.with_columns(
            pl.lit("NOT_CHECKED").alias("everify_lookup_status"),
            pl.lit(None, dtype=pl.String).alias("everify_retrieved_at"),
            pl.lit(None, dtype=pl.String).alias("everify_source_url"),
            pl.lit(None, dtype=pl.Float64).alias("everify_match_confidence"),
            pl.lit(None, dtype=pl.String).alias("everify_review_status"),
        )
    if opt is not None and not opt.is_empty():
        latest_opt = (
            opt.filter(
                pl.col("organization_id").is_not_null()
                & pl.col("is_positive")
                & (pl.col("program_type") == "OPT_OR_STEM_OPT")
            )
            .sort(
                ["organization_id", "report_year", "rank"],
                descending=[False, True, False],
            )
            .group_by("organization_id", maintain_order=True)
            .first()
            .select(
                "organization_id",
                pl.lit("OBSERVED_POSITIVE").alias("_known_opt_observation"),
                pl.col("report_year").alias("opt_report_year"),
                pl.col("reported_count").alias("opt_reported_count"),
                pl.col("rank").alias("opt_report_rank"),
                pl.col("source_url").alias("opt_source_url"),
            )
        )
        result = (
            result.join(latest_opt, on="organization_id", how="left")
            .with_columns(
                pl.coalesce("_known_opt_observation", "known_opt_observation").alias(
                    "known_opt_observation"
                )
            )
            .drop("_known_opt_observation")
        )
    else:
        result = result.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("opt_report_year"),
            pl.lit(None, dtype=pl.Int64).alias("opt_reported_count"),
            pl.lit(None, dtype=pl.Int64).alias("opt_report_rank"),
            pl.lit(None, dtype=pl.String).alias("opt_source_url"),
        )
    return result.with_columns(pl.lit(METRIC_VERSION).alias("metric_version"))


def _append_phase6_health(
    health: pl.DataFrame,
    *,
    everify: pl.DataFrame | None,
    opt: pl.DataFrame | None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    if opt is not None and not opt.is_empty():
        rows.append(
            {
                "source_id": "sevp_opt",
                "row_count": opt.height,
                "earliest_year": opt["report_year"].min(),
                "latest_year": opt["report_year"].max(),
                "latest_complete_fiscal_year": opt["report_year"].max(),
                "current_partial_fiscal_year": None,
                "current_partial_quarter": None,
                "has_partial_period": False,
                "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
                "freshness_warning": (
                    "Positive-only Top 200 coverage; absence from the report means UNKNOWN."
                ),
            }
        )
    if everify is not None and not everify.is_empty():
        years = everify["retrieved_at"].str.slice(0, 4).cast(pl.Int32, strict=False)
        rows.append(
            {
                "source_id": "everify",
                "row_count": everify.height,
                "earliest_year": years.min(),
                "latest_year": years.max(),
                "latest_complete_fiscal_year": None,
                "current_partial_fiscal_year": None,
                "current_partial_quarter": None,
                "has_partial_period": False,
                "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
                "freshness_warning": "Lookup coverage is prioritized, cached, and incomplete.",
            }
        )
    if not rows:
        return health
    return pl.concat([health, pl.DataFrame(rows)], how="vertical_relaxed").sort("source_id")


def _read_optional_policy_frame(
    path: Path,
    *,
    required_columns: frozenset[str],
) -> pl.DataFrame | None:
    if not path.is_file():
        return None
    try:
        frame = pl.read_parquet(path)
    except Exception as error:
        LOGGER.warning(
            "Ignoring unreadable optional policy table %s (%s)",
            path.name,
            type(error).__name__,
        )
        return None
    missing = required_columns.difference(frame.columns)
    if missing:
        LOGGER.warning(
            "Ignoring optional policy table %s with incompatible schema; missing %s",
            path.name,
            ", ".join(sorted(missing)),
        )
        return None
    return frame


def _phase7_frames(data_root: Path) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    processed = data_root / "processed"
    documents_path = processed / "policy_documents.parquet"
    facts_path = processed / "policy_facts.parquet"
    documents = _read_optional_policy_frame(
        documents_path,
        required_columns=_POLICY_DOCUMENT_REQUIRED_COLUMNS,
    )
    facts = _read_optional_policy_frame(
        facts_path,
        required_columns=_POLICY_FACT_REQUIRED_COLUMNS,
    )
    return documents, facts


def _enrich_phase7_institution_metrics(
    frame: pl.DataFrame,
    *,
    facts: pl.DataFrame | None,
) -> pl.DataFrame:
    if facts is None or facts.is_empty():
        return frame
    accepted = facts.filter(
        (pl.col("human_review_status") == "REVIEWED_ACCEPTED")
        & pl.col("exact_excerpt_verified")
        & pl.col("is_current")
        & pl.col("valid_to").is_null()
        & pl.col("source_url").str.starts_with("https://")
    )
    reviewed_not_stated = facts.filter(
        (pl.col("human_review_status") == "REVIEWED_NOT_STATED")
        & (pl.col("fact_value") == "NOT_STATED")
        & pl.col("is_current")
        & pl.col("valid_to").is_null()
        & pl.col("source_url").str.starts_with("https://")
    )
    statuses = facts.group_by("institution_id").agg(
        pl.col("human_review_status").eq("REVIEWED_ACCEPTED").any().alias("_has_accepted_policy"),
        pl.col("human_review_status")
        .eq("REVIEWED_NOT_STATED")
        .any()
        .alias("_has_reviewed_not_stated"),
        pl.col("human_review_status").eq("NEEDS_REVIEW").any().alias("_has_pending_policy"),
    )
    result = frame.join(statuses, on="institution_id", how="left")
    reviewed = pl.col("_has_accepted_policy").fill_null(False) | pl.col(
        "_has_reviewed_not_stated"
    ).fill_null(False)
    if accepted.is_empty() and reviewed_not_stated.is_empty():
        return result.with_columns(
            pl.when(reviewed & pl.col("_has_pending_policy").fill_null(False))
            .then(pl.lit("PARTIALLY_REVIEWED"))
            .when(reviewed)
            .then(pl.lit("REVIEWED"))
            .when(pl.col("_has_pending_policy").fill_null(False))
            .then(pl.lit("NEEDS_REVIEW"))
            .otherwise(pl.col("policy_review_status"))
            .alias("policy_review_status"),
        ).drop("_has_accepted_policy", "_has_reviewed_not_stated", "_has_pending_policy")

    latest = (
        pl.concat([accepted, reviewed_not_stated], how="diagonal_relaxed")
        .sort(["institution_id", "fact_type", "valid_from"])
        .group_by(["institution_id", "fact_type"], maintain_order=True)
        .last()
        .select("institution_id", "fact_type", "fact_value")
    )
    wide = latest.pivot(on="fact_type", index="institution_id", values="fact_value")
    mappings = {
        "h1b_research_staff_eligible": "research_staff_h1b_policy",
        "pr_research_staff_eligible": "research_staff_permanent_residence_policy",
        "pr_general_staff_eligible": "general_staff_permanent_residence_policy",
        "perm_supported": "perm_support",
        "eb1b_supported": "eb1b_support",
    }
    selected = ["institution_id"]
    expressions: list[pl.Expr] = []
    for fact_type, target in mappings.items():
        if fact_type in wide.columns:
            expressions.append(pl.col(fact_type).alias(f"_{target}"))
            selected.append(fact_type)
        else:
            expressions.append(pl.lit(None, dtype=pl.String).alias(f"_{target}"))
    policy_values = (
        wide.select(selected)
        .with_columns(expressions)
        .select("institution_id", *[f"_{target}" for target in mappings.values()])
    )
    result = result.join(policy_values, on="institution_id", how="left")
    return (
        result.with_columns(
            *[pl.coalesce(f"_{target}", target).alias(target) for target in mappings.values()],
            pl.when(reviewed & pl.col("_has_pending_policy").fill_null(False))
            .then(pl.lit("PARTIALLY_REVIEWED"))
            .when(reviewed)
            .then(pl.lit("REVIEWED"))
            .when(pl.col("_has_pending_policy").fill_null(False))
            .then(pl.lit("NEEDS_REVIEW"))
            .otherwise(pl.col("policy_review_status"))
            .alias("policy_review_status"),
            pl.when(pl.col("_has_accepted_policy").fill_null(False))
            .then(
                pl.concat_str(
                    "evidence_classes",
                    pl.lit("REVIEWED_OFFICIAL_POLICY"),
                    separator="|",
                )
            )
            .when(pl.col("_has_reviewed_not_stated").fill_null(False))
            .then(
                pl.concat_str(
                    "evidence_classes",
                    pl.lit("REVIEWED_POLICY_PROFILE"),
                    separator="|",
                )
            )
            .otherwise(pl.col("evidence_classes"))
            .alias("evidence_classes"),
        )
        .drop(
            "_has_accepted_policy",
            "_has_reviewed_not_stated",
            "_has_pending_policy",
            *[f"_{target}" for target in mappings.values()],
        )
        .with_columns(pl.lit(METRIC_VERSION).alias("metric_version"))
    )


def _append_phase7_health(
    health: pl.DataFrame,
    *,
    documents: pl.DataFrame | None,
    facts: pl.DataFrame | None,
) -> pl.DataFrame:
    if documents is None or documents.is_empty():
        return health
    retrieved_years = documents["retrieved_at"].str.slice(0, 4).cast(pl.Int32, strict=False)
    accepted_count = (
        facts.filter(pl.col("human_review_status") == "REVIEWED_ACCEPTED").height
        if facts is not None and not facts.is_empty()
        else 0
    )
    row = pl.DataFrame(
        [
            {
                "source_id": "institution_policy",
                "row_count": documents.height,
                "earliest_year": retrieved_years.min(),
                "latest_year": retrieved_years.max(),
                "latest_complete_fiscal_year": None,
                "current_partial_fiscal_year": None,
                "current_partial_quarter": None,
                "has_partial_period": False,
                "evidence_class": "REVIEWED_OFFICIAL_POLICY",
                "freshness_warning": (
                    f"{accepted_count} facts are human-reviewed and accepted; all other "
                    "extractions remain outside product signals."
                ),
            }
        ]
    )
    return pl.concat([health, row], how="vertical_relaxed").sort("source_id")


class MetricsPipeline:
    """Create processed tables, reviewed evidence, and versioned Phase 8 scores."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        scoring_config_path: Path = DEFAULT_SCORING_CONFIG_PATH,
        scoring_v2_config_path: Path = DEFAULT_SCORING_V2_CONFIG_PATH,
        product_a_scoring_config_path: Path = DEFAULT_PRODUCT_A_SCORING_CONFIG_PATH,
        source_registry_path: Path = DEFAULT_SOURCE_REGISTRY_PATH,
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.scoring_config_path = scoring_config_path
        self.scoring_v2_config_path = scoring_v2_config_path
        self.product_a_scoring_config_path = product_a_scoring_config_path
        self.registry = SourceRegistry.from_yaml(source_registry_path)
        self.manifest_store = ArtifactManifestStore(
            output_root / "manifests" / "source_artifacts.jsonl"
        )

    def build(self) -> MetricsBuildSummary:
        resolved_root = self.data_root / "resolved"
        legal_entities = pl.read_parquet(resolved_root / "legal_entities.parquet")
        parents = pl.read_parquet(resolved_root / "parent_organizations.parquet")
        active_records = active_artifact_records(
            self.manifest_store,
            self.registry,
            discovery_root=self.output_root / "manifests" / "discovery",
            source_ids={"dol_lca", "dol_perm", "uscis_h1b", "ipeds"},
        )
        institutions = _read_institutions(self.data_root, active_records)
        herd = pl.read_parquet(self.data_root / "processed" / "herd_observations.parquet")
        lca = _read_lca(self.data_root, active_records)
        perm = _read_perm(self.data_root, active_records)
        uscis = _read_uscis(self.data_root, active_records)
        entity_aliases = pl.read_parquet(resolved_root / "entity_aliases.parquet")
        unresolved_candidate_flags = _unresolved_candidate_evidence_flags(
            entity_aliases,
            legal_entities,
            parents,
            lca,
            perm,
        )

        dimension = _organization_dimension(legal_entities, parents, institutions)
        employer_metrics = _employer_metrics(
            dimension,
            institutions,
            lca,
            perm,
            uscis,
            unresolved_candidate_flags,
        )
        institution_metrics = _institution_metrics(institutions, parents, herd, employer_metrics)
        data_health = _source_health(lca, perm, uscis, institutions, herd)
        everify, opt = _phase6_frames(self.data_root)
        provenance_frames = [lca, perm, uscis, institutions, herd]
        if opt is not None and not opt.is_empty():
            provenance_frames.append(opt)
        source_artifacts = _selected_source_artifacts(
            self.output_root,
            tuple(provenance_frames),
        )
        employer_metrics = _enrich_phase6_metrics(employer_metrics, everify=everify, opt=opt)
        institution_metrics = _enrich_phase6_metrics(
            institution_metrics.with_columns(pl.lit("UNKNOWN").alias("known_opt_observation")),
            everify=everify,
            opt=opt,
        )
        data_health = _append_phase6_health(data_health, everify=everify, opt=opt)
        policy_documents, policy_facts = _phase7_frames(self.data_root)
        try:
            institution_metrics = _enrich_phase7_institution_metrics(
                institution_metrics,
                facts=policy_facts,
            )
        except Exception as error:
            LOGGER.warning(
                "Ignoring incompatible optional policy facts during Product A metrics build (%s)",
                type(error).__name__,
            )
            policy_facts = None
        try:
            data_health = _append_phase7_health(
                data_health,
                documents=policy_documents,
                facts=policy_facts,
            )
        except Exception as error:
            LOGGER.warning(
                "Ignoring incompatible optional policy documents during Product A metrics build "
                "(%s)",
                type(error).__name__,
            )
            policy_documents = None

        employer_metrics_v1: pl.DataFrame | None = None
        institution_metrics_v1: pl.DataFrame | None = None
        try:
            scoring_config = ScoringConfig.from_yaml(self.scoring_config_path)
            employer_metrics_v1 = score_employers(employer_metrics, scoring_config).with_columns(
                pl.lit(V1_METRIC_VERSION).alias("metric_version")
            )
            institution_metrics_v1 = score_institutions(
                institution_metrics,
                policy_facts,
                scoring_config,
            ).with_columns(pl.lit(V1_METRIC_VERSION).alias("metric_version"))
        except Exception as error:
            LOGGER.warning(
                "Skipping optional legacy V1 score sidecars during Product A metrics build (%s)",
                type(error).__name__,
            )

        employer_metrics_v2: pl.DataFrame | None = None
        try:
            scoring_v2_config = ScoringV2Config.from_yaml(self.scoring_v2_config_path)
            employer_metrics_v2 = score_employers_v2(
                employer_metrics,
                scoring_v2_config,
            ).with_columns(pl.lit(V2_METRIC_VERSION).alias("metric_version"))
        except Exception as error:
            LOGGER.warning(
                "Skipping optional legacy V2 score sidecar during Product A metrics build (%s)",
                type(error).__name__,
            )
        product_a_config = ProductAScoringConfig.from_yaml(self.product_a_scoring_config_path)
        employer_metrics = score_employers_product_a(employer_metrics, product_a_config)
        institution_metrics = score_institutions_product_a(institution_metrics, product_a_config)
        sponsorship_columns = [
            column
            for column in employer_metrics.columns
            if column.startswith("h1b_history_")
            or column.startswith("green_card_history_")
            or column.startswith("overall_sponsorship_")
            or column
            in {
                "score_version",
                "metric_version",
                "score_count_percentile_cap",
                "h1b_volume_p95_cap",
                "uscis_initial_approvals_p95_cap",
                "green_card_volume_p95_cap",
            }
        ]
        institution_metrics = institution_metrics.drop(
            [column for column in sponsorship_columns if column in institution_metrics.columns]
        ).join(
            employer_metrics.select("organization_id", *sponsorship_columns),
            on="organization_id",
            how="left",
        )

        employer_scores_v2 = (
            employer_metrics_v2.select(
                "organization_id",
                "score_version",
                "stem_opt_readiness_score",
                "stem_opt_readiness_status",
                "stem_opt_readiness_coverage",
                "stem_opt_readiness_confidence",
                "stem_opt_readiness_explanation",
                "h1b_history_score",
                "h1b_history_coverage",
                "h1b_history_confidence",
                "h1b_history_status",
                "h1b_history_grade",
                "h1b_history_explanation",
                "green_card_history_score",
                "green_card_history_coverage",
                "green_card_history_confidence",
                "green_card_history_status",
                "green_card_history_grade",
                "green_card_history_explanation",
                "sponsorship_history_score",
                "sponsorship_history_coverage",
                "sponsorship_history_confidence",
                "sponsorship_history_confidence_band",
                "sponsorship_history_status",
                "sponsorship_history_grade",
                "sponsorship_history_explanation",
                "metric_version",
            )
            if employer_metrics_v2 is not None
            else None
        )
        product_a_score_columns = [
            "organization_id",
            "entity_resolution_valid",
            "h1b_entity_resolution_valid",
            "perm_entity_resolution_valid",
            "entity_coverage_state",
            "h1b_entity_coverage_state",
            "perm_entity_coverage_state",
            "has_unresolved_h1b_candidate_evidence",
            "has_unresolved_perm_candidate_evidence",
            "score_version",
            "h1b_history_score",
            "h1b_history_status",
            "h1b_history_star_rating",
            "h1b_history_stars",
            "h1b_history_star_label",
            "h1b_history_coverage",
            "h1b_history_explanation",
            "green_card_history_score",
            "green_card_history_status",
            "green_card_history_star_rating",
            "green_card_history_stars",
            "green_card_history_star_label",
            "green_card_history_coverage",
            "green_card_history_explanation",
            "overall_sponsorship_score",
            "overall_sponsorship_status",
            "overall_sponsorship_star_rating",
            "overall_sponsorship_stars",
            "overall_sponsorship_star_label",
            "overall_sponsorship_coverage",
            "overall_sponsorship_explanation",
            "score_count_percentile_cap",
            "h1b_volume_p95_cap",
            "uscis_initial_approvals_p95_cap",
            "green_card_volume_p95_cap",
            "metric_version",
        ]
        employer_scores_product_a = employer_metrics.select(*product_a_score_columns)
        institution_scores_product_a = institution_metrics.select(
            "institution_id",
            *[column for column in product_a_score_columns if column != "organization_id"],
            "research_scale_score",
            "research_scale_status",
            "research_scale_star_rating",
            "research_scale_stars",
            "research_scale_star_label",
            "research_scale_explanation",
        )
        processed = self.data_root / "processed"
        outputs = {
            "parent_organizations.parquet": parents.sort("parent_organization_id"),
            "legal_entities.parquet": legal_entities.sort("legal_entity_id"),
            "institutions.parquet": institutions,
            "lca_cases_resolved.parquet": lca,
            "perm_cases_resolved.parquet": perm,
            "h1b_petitions_resolved.parquet": uscis,
            "employer_metrics.parquet": employer_metrics,
            "employer_scores.parquet": employer_scores_product_a,
            "employer_scores_v2.parquet": employer_scores_v2,
            "employer_scores_v1.parquet": (
                employer_metrics_v1.select(
                    "organization_id",
                    "score_version",
                    "stem_opt_readiness_score",
                    "stem_opt_readiness_status",
                    "stem_opt_readiness_coverage",
                    "stem_opt_readiness_confidence",
                    "stem_opt_readiness_explanation",
                    "h1b_history_score",
                    "h1b_history_coverage",
                    "h1b_history_confidence",
                    "h1b_history_grade",
                    "h1b_history_explanation",
                    "green_card_history_score",
                    "green_card_history_coverage",
                    "green_card_history_confidence",
                    "green_card_history_grade",
                    "green_card_history_explanation",
                    "immigration_evidence_score",
                    "immigration_evidence_coverage",
                    "immigration_evidence_confidence",
                    "immigration_evidence_grade",
                    "immigration_evidence_explanation",
                    "metric_version",
                )
                if employer_metrics_v1 is not None
                else None
            ),
            "institution_scores_v1.parquet": (
                institution_metrics_v1.select(
                    "institution_id",
                    "score_version",
                    "stem_opt_readiness_score",
                    "stem_opt_readiness_status",
                    "stem_opt_readiness_coverage",
                    "stem_opt_readiness_confidence",
                    "stem_opt_readiness_explanation",
                    "h1b_history_score",
                    "h1b_history_coverage",
                    "h1b_history_confidence",
                    "h1b_history_grade",
                    "h1b_history_explanation",
                    "green_card_history_score",
                    "green_card_history_coverage",
                    "green_card_history_confidence",
                    "green_card_history_grade",
                    "green_card_history_explanation",
                    "immigration_evidence_score",
                    "immigration_evidence_coverage",
                    "immigration_evidence_confidence",
                    "immigration_evidence_grade",
                    "immigration_evidence_explanation",
                    "research_strength_score",
                    "research_strength_coverage",
                    "research_strength_confidence",
                    "research_strength_grade",
                    "research_strength_explanation",
                    "policy_support_score",
                    "policy_support_coverage",
                    "policy_support_confidence",
                    "policy_support_grade",
                    "policy_support_explanation",
                    "research_pathway_score",
                    "research_pathway_coverage",
                    "research_pathway_confidence",
                    "research_pathway_grade",
                    "research_pathway_explanation",
                    "metric_version",
                )
                if institution_metrics_v1 is not None
                else None
            ),
            "institution_scores.parquet": institution_scores_product_a,
            "institution_metrics.parquet": institution_metrics,
            "data_health.parquet": data_health,
            "source_artifacts.parquet": source_artifacts,
        }
        for name, frame in outputs.items():
            if frame is not None:
                _write_parquet_atomic(frame, processed / name)

        partial_year = data_health["current_partial_fiscal_year"].max()
        partial_rows = (
            data_health.filter(pl.col("current_partial_fiscal_year") == partial_year)
            if partial_year is not None
            else data_health.head(0)
        )
        latest_complete = data_health.filter(
            pl.col("source_id").is_in(["dol_lca", "dol_perm", "uscis_h1b"])
        )["latest_complete_fiscal_year"].max()
        latest_complete_value = cast(int | None, latest_complete)
        partial_year_value = cast(int | None, partial_year)
        partial_quarter = (
            partial_rows["current_partial_quarter"].max() if partial_rows.height else None
        )
        partial_quarter_value = cast(int | None, partial_quarter)
        summary_path = self.output_root / "reports" / "metrics" / "summary.json"
        summary = MetricsBuildSummary(
            employer_count=employer_metrics.height,
            institution_count=institution_metrics.height,
            lca_case_count=lca.height,
            perm_case_count=perm.height,
            h1b_petition_row_count=uscis.height,
            latest_complete_fiscal_year=latest_complete_value,
            current_partial_fiscal_year=partial_year_value,
            current_partial_quarter=partial_quarter_value,
            metric_version=METRIC_VERSION,
            employer_metrics_path=processed / "employer_metrics.parquet",
            institution_metrics_path=processed / "institution_metrics.parquet",
            data_health_path=processed / "data_health.parquet",
            summary_path=summary_path,
        )
        write_json_atomic(summary_path, summary.model_dump(mode="json"))
        return summary
