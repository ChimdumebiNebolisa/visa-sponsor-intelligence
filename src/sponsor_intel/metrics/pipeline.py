"""Build processed case, employer, institution, and health tables."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import cast

import polars as pl

from sponsor_intel.metrics.models import MetricsBuildSummary
from sponsor_intel.scoring import (
    DEFAULT_SCORING_CONFIG_PATH,
    DEFAULT_SCORING_V2_CONFIG_PATH,
    ScoringConfig,
    ScoringV2Config,
    score_employers,
    score_employers_v2,
    score_institutions,
    score_institutions_v2,
)
from sponsor_intel.sources.manifests import write_json_atomic

V1_METRIC_VERSION = "scored_metrics_v1"
METRIC_VERSION = "scored_metrics_v2"


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


def _files(root: Path, source_id: str, *, classified: bool = False) -> list[Path]:
    layer = "classified" if classified else "resolved"
    found = sorted((root / layer / "sources" / source_id).rglob("*.parquet"))
    if not found:
        raise ValueError(f"No {layer} {source_id} Parquet files are available")
    return found


def _organization_id() -> pl.Expr:
    return pl.coalesce("parent_organization_id", "legal_entity_id").alias("organization_id")


def _read_lca(data_root: Path) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "dol_lca", classified=True):
        frames.append(
            pl.read_parquet(path).select(
                "case_id",
                "source_artifact_id",
                "source_file_name",
                "ingested_at",
                "fiscal_year",
                "fiscal_quarter",
                "is_partial_period",
                pl.col("case_status").cast(pl.String),
                pl.col("decision_date").cast(pl.Date, strict=False),
                "employer_name_raw",
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                _organization_id(),
                "job_title_raw",
                pl.col("soc_code").cast(pl.String, strict=False),
                pl.col("soc_title").cast(pl.String, strict=False),
                "role_family",
                "technical_role",
                "role_confidence",
                "classification_method",
                "classification_version",
                "review_status",
                pl.col("worksite_state").cast(pl.String, strict=False),
                pl.col("wage_from").cast(pl.Float64, strict=False),
                pl.col("wage_to").cast(pl.Float64, strict=False),
                pl.col("wage_unit").cast(pl.String, strict=False),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "case_id"]
    )


def _read_perm(data_root: Path) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "dol_perm", classified=True):
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
                "case_id",
                "source_artifact_id",
                "source_file_name",
                "ingested_at",
                "fiscal_year",
                "fiscal_quarter",
                "is_partial_period",
                pl.col("case_status").cast(pl.String),
                pl.col("decision_date").cast(pl.Date, strict=False),
                "employer_name_raw",
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                _organization_id(),
                "job_title_raw",
                pl.col("soc_code").cast(pl.String, strict=False),
                pl.col("soc_title").cast(pl.String, strict=False),
                "role_family",
                "technical_role",
                "role_confidence",
                "classification_method",
                "classification_version",
                "review_status",
                pl.col("worksite_state").cast(pl.String, strict=False),
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
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "case_id"]
    )


def _read_uscis(data_root: Path) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "uscis_h1b"):
        frames.append(
            pl.read_parquet(path).select(
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
                pl.col("initial_approvals").cast(pl.Int64),
                pl.col("initial_denials").cast(pl.Int64),
                pl.col("continuing_approvals").cast(pl.Int64),
                pl.col("continuing_denials").cast(pl.Int64),
                pl.col("state").cast(pl.String, strict=False),
                pl.col("city").cast(pl.String, strict=False),
                pl.col("zip_code").cast(pl.String, strict=False),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["fiscal_year", "source_artifact_id", "source_row_number"]
    )


def _read_institutions(data_root: Path) -> pl.DataFrame:
    frames = []
    for path in _files(data_root, "ipeds"):
        frames.append(
            pl.read_parquet(path).select(
                "institution_id",
                "ipeds_unitid",
                "official_name",
                "system_name",
                "control",
                "sector",
                "city",
                pl.col("stabbr").alias("state"),
                "official_domain",
                "highest_degree",
                "active_status",
                pl.col("legal_entity_id").cast(pl.String, strict=False),
                pl.col("parent_organization_id").cast(pl.String, strict=False),
                "match_confidence",
                "review_status",
                "source_artifact_id",
                "directory_year",
            )
        )
    return (
        pl.concat(frames, how="vertical_relaxed")
        .unique("institution_id", keep="last")
        .sort("institution_id")
    )


def _organization_dimension(
    legal_entities: pl.DataFrame,
    parents: pl.DataFrame,
    institutions: pl.DataFrame,
) -> pl.DataFrame:
    parent_lookup = parents.select(
        "parent_organization_id",
        pl.col("canonical_name").alias("parent_name"),
        pl.col("organization_type").alias("parent_type"),
        "headquarters_state",
        "is_staffing_or_consulting",
    )
    institution_control = (
        institutions.with_columns(_organization_id())
        .filter(pl.col("organization_id").is_not_null())
        .group_by("organization_id")
        .agg(pl.col("control").drop_nulls().first().alias("institution_control"))
    )
    dimension = (
        legal_entities.join(parent_lookup, on="parent_organization_id", how="left")
        .with_columns(
            _organization_id(),
            pl.coalesce("parent_name", "legal_name").alias("organization_name"),
            pl.coalesce("parent_type", "organization_type").alias("organization_type_raw"),
            pl.coalesce("headquarters_state", "state").alias("state_display"),
        )
        .group_by("organization_id")
        .agg(
            pl.col("parent_organization_id").drop_nulls().first(),
            pl.col("organization_name").first(),
            pl.col("organization_type_raw").first(),
            pl.col("state_display").drop_nulls().first().alias("state"),
            pl.col("legal_entity_id").n_unique().alias("legal_entity_count"),
            pl.col("is_staffing_or_consulting").drop_nulls().first(),
        )
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
            pl.when(pl.col("parent_organization_id").is_not_null())
            .then(pl.lit("PARENT_ORGANIZATION"))
            .otherwise(pl.lit("LEGAL_ENTITY"))
            .alias("identity_scope"),
        )
    )
    return dimension.sort(["organization_name", "organization_id"])


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


def _uscis_metrics(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame.filter(pl.col("organization_id").is_not_null())
        .group_by("organization_id")
        .agg(
            pl.len().alias("uscis_employer_year_rows"),
            pl.col("initial_approvals").sum(),
            pl.col("initial_denials").sum(),
            pl.col("continuing_approvals").sum(),
            pl.col("continuing_denials").sum(),
            pl.col("fiscal_year").n_unique().alias("uscis_active_years"),
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
) -> pl.DataFrame:
    perm_certified = (pl.col("technical_role") == True) & (  # noqa: E712
        pl.col("case_status").fill_null("").str.to_uppercase().str.starts_with("CERTIFIED")
    )
    result = (
        dimension.join(
            _program_metrics(
                lca,
                prefix="lca",
                relevant=pl.col("technical_role") == True,  # noqa: E712
            ),
            on="organization_id",
            how="left",
        )
        .join(
            _program_metrics(perm, prefix="perm", relevant=perm_certified).rename(
                {"relevant_perm_count": "relevant_certified_perm_count"}
            ),
            on="organization_id",
            how="left",
        )
        .join(_uscis_metrics(uscis), on="organization_id", how="left")
    )
    institution_orgs = (
        institutions.with_columns(_organization_id())
        .select("organization_id")
        .unique()
        .with_columns(pl.lit(True).alias("is_higher_education"))
    )
    count_columns = [
        "lca_case_count",
        "relevant_lca_count",
        "lca_active_years",
        "perm_case_count",
        "relevant_certified_perm_count",
        "perm_active_years",
        "uscis_employer_year_rows",
        "initial_approvals",
        "initial_denials",
        "continuing_approvals",
        "continuing_denials",
        "uscis_active_years",
    ]
    result = result.join(institution_orgs, on="organization_id", how="left").with_columns(
        [pl.col(column).fill_null(0) for column in count_columns]
        + [
            pl.col("is_higher_education").fill_null(False),
            pl.col("lca_has_partial_period").fill_null(False),
            pl.col("perm_has_partial_period").fill_null(False),
            pl.col("uscis_has_partial_period").fill_null(False),
        ]
    )
    result = result.with_columns(
        (
            (pl.col("lca_case_count") > 0).cast(pl.Int8)
            + (pl.col("perm_case_count") > 0).cast(pl.Int8)
            + (pl.col("uscis_employer_year_rows") > 0).cast(pl.Int8)
        ).alias("source_coverage_count"),
        pl.max_horizontal(
            "last_lca_activity_year", "last_perm_activity_year", "last_uscis_activity_year"
        ).alias("last_observed_activity_year"),
        pl.max_horizontal(
            "lca_latest_complete_fiscal_year",
            "perm_latest_complete_fiscal_year",
            "uscis_latest_complete_fiscal_year",
        ).alias("latest_complete_fiscal_year"),
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
        .then(pl.lit("POTENTIALLY_CAP_EXEMPT_HIGHER_ED"))
        .otherwise(pl.lit("UNKNOWN"))
        .alias("cap_exemption_status"),
        pl.lit("NOT_SCORED").alias("evidence_confidence"),
        pl.lit(None, dtype=pl.Float64).alias("h1b_activity_score"),
        pl.lit(None, dtype=pl.Float64).alias("immigration_evidence_score"),
        pl.lit("OBSERVED_GOVERNMENT_RECORD|DERIVED_METRIC").alias("evidence_classes"),
        pl.lit(METRIC_VERSION).alias("metric_version"),
    ).with_columns((pl.col("source_coverage_count") / 3).alias("source_coverage_ratio"))
    return result.sort(["relevant_lca_count", "organization_name"], descending=[True, False])


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


def _institution_metrics(
    institutions: pl.DataFrame,
    parents: pl.DataFrame,
    herd: pl.DataFrame,
    lca: pl.DataFrame,
    perm: pl.DataFrame,
    uscis: pl.DataFrame,
) -> pl.DataFrame:
    herd_latest = (
        herd.sort(["institution_id", "survey_year"])
        .group_by("institution_id", maintain_order=True)
        .last()
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
    parent_lookup = parents.select("parent_organization_id", "canonical_name")
    perm_relevant = (pl.col("technical_role") == True) & (  # noqa: E712
        pl.col("case_status").fill_null("").str.to_uppercase().str.starts_with("CERTIFIED")
    )
    legal_uscis = (
        uscis.filter(pl.col("legal_entity_id").is_not_null())
        .group_by("legal_entity_id")
        .agg(
            pl.len().alias("uscis_employer_year_rows"),
            pl.col("initial_approvals").sum(),
            pl.col("initial_denials").sum(),
            pl.col("continuing_approvals").sum(),
            pl.col("continuing_denials").sum(),
            pl.col("fiscal_year").n_unique().alias("uscis_active_years"),
            pl.col("fiscal_year").max().alias("last_uscis_activity_year"),
        )
    )
    result = (
        institutions.join(parent_lookup, on="parent_organization_id", how="left")
        .with_columns(
            pl.coalesce("canonical_name", "system_name").alias("parent_system"),
            _organization_id(),
        )
        .join(herd_latest, on="institution_id", how="left")
        .join(
            _legal_program_metrics(
                lca,
                prefix="lca",
                relevant=pl.col("technical_role") == True,  # noqa: E712
            ),
            on="legal_entity_id",
            how="left",
        )
        .join(
            _legal_program_metrics(perm, prefix="perm", relevant=perm_relevant).rename(
                {"relevant_perm_count": "relevant_certified_perm_count"}
            ),
            on="legal_entity_id",
            how="left",
        )
        .join(legal_uscis, on="legal_entity_id", how="left")
    )
    immigration_counts = [
        "lca_case_count",
        "relevant_lca_count",
        "lca_active_years",
        "perm_case_count",
        "relevant_certified_perm_count",
        "perm_active_years",
        "uscis_employer_year_rows",
        "initial_approvals",
        "initial_denials",
        "continuing_approvals",
        "continuing_denials",
        "uscis_active_years",
    ]
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
        .with_columns([pl.col(column).fill_null(0) for column in immigration_counts])
        .with_columns(
            pl.max_horizontal(
                "last_lca_activity_year", "last_perm_activity_year", "last_uscis_activity_year"
            ).alias("last_observed_activity_year"),
            pl.lit("UNKNOWN").alias("everify_status"),
            pl.lit("POTENTIALLY_CAP_EXEMPT_HIGHER_ED").alias("cap_exemption_status"),
            pl.lit("UNKNOWN").alias("research_staff_h1b_policy"),
            pl.lit("UNKNOWN").alias("research_staff_permanent_residence_policy"),
            pl.lit("UNKNOWN").alias("general_staff_permanent_residence_policy"),
            pl.lit("UNKNOWN").alias("perm_support"),
            pl.lit("UNKNOWN").alias("eb1b_support"),
            pl.lit("NOT_STARTED").alias("policy_review_status"),
            pl.lit(None, dtype=pl.Float64).alias("research_pathway_score"),
            pl.lit("OBSERVED_GOVERNMENT_RECORD|DERIVED_METRIC").alias("evidence_classes"),
            pl.lit(METRIC_VERSION).alias("metric_version"),
        )
        .sort(["total_rd", "official_name"], descending=[True, False])
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


def _phase7_frames(data_root: Path) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
    processed = data_root / "processed"
    documents_path = processed / "policy_documents.parquet"
    facts_path = processed / "policy_facts.parquet"
    documents = pl.read_parquet(documents_path) if documents_path.is_file() else None
    facts = pl.read_parquet(facts_path) if facts_path.is_file() else None
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
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.scoring_config_path = scoring_config_path
        self.scoring_v2_config_path = scoring_v2_config_path

    def build(self) -> MetricsBuildSummary:
        resolved_root = self.data_root / "resolved"
        legal_entities = pl.read_parquet(resolved_root / "legal_entities.parquet")
        parents = pl.read_parquet(resolved_root / "parent_organizations.parquet")
        institutions = _read_institutions(self.data_root)
        herd = pl.read_parquet(self.data_root / "processed" / "herd_observations.parquet")
        lca = _read_lca(self.data_root)
        perm = _read_perm(self.data_root)
        uscis = _read_uscis(self.data_root)

        dimension = _organization_dimension(legal_entities, parents, institutions)
        employer_metrics = _employer_metrics(dimension, institutions, lca, perm, uscis)
        institution_metrics = _institution_metrics(institutions, parents, herd, lca, perm, uscis)
        data_health = _source_health(lca, perm, uscis, institutions, herd)
        everify, opt = _phase6_frames(self.data_root)
        employer_metrics = _enrich_phase6_metrics(employer_metrics, everify=everify, opt=opt)
        institution_metrics = _enrich_phase6_metrics(
            institution_metrics.with_columns(pl.lit("UNKNOWN").alias("known_opt_observation")),
            everify=everify,
            opt=opt,
        )
        data_health = _append_phase6_health(data_health, everify=everify, opt=opt)
        policy_documents, policy_facts = _phase7_frames(self.data_root)
        institution_metrics = _enrich_phase7_institution_metrics(
            institution_metrics,
            facts=policy_facts,
        )
        data_health = _append_phase7_health(
            data_health,
            documents=policy_documents,
            facts=policy_facts,
        )
        scoring_config = ScoringConfig.from_yaml(self.scoring_config_path)
        employer_metrics_v1 = score_employers(employer_metrics, scoring_config).with_columns(
            pl.lit(V1_METRIC_VERSION).alias("metric_version")
        )
        institution_metrics_v1 = score_institutions(
            institution_metrics,
            policy_facts,
            scoring_config,
        ).with_columns(pl.lit(V1_METRIC_VERSION).alias("metric_version"))
        scoring_v2_config = ScoringV2Config.from_yaml(self.scoring_v2_config_path)
        employer_metrics = score_employers_v2(employer_metrics, scoring_v2_config).with_columns(
            pl.lit(METRIC_VERSION).alias("metric_version")
        )
        institution_metrics = score_institutions_v2(
            institution_metrics,
            policy_facts,
            scoring_v2_config,
        ).with_columns(pl.lit(METRIC_VERSION).alias("metric_version"))
        legacy_immigration_columns = [
            "immigration_evidence_score",
            "immigration_evidence_coverage",
            "immigration_evidence_confidence",
            "immigration_evidence_grade",
            "immigration_evidence_explanation",
        ]
        employer_metrics = (
            employer_metrics.drop(
                [
                    column
                    for column in legacy_immigration_columns
                    if column in employer_metrics.columns
                ]
            )
            .join(
                employer_metrics_v1.select("organization_id", *legacy_immigration_columns),
                on="organization_id",
                how="left",
            )
            .with_columns(
                pl.lit(scoring_config.version).alias("immigration_evidence_score_version")
            )
        )
        institution_metrics = (
            institution_metrics.drop(
                [
                    column
                    for column in legacy_immigration_columns
                    if column in institution_metrics.columns
                ]
            )
            .join(
                institution_metrics_v1.select("institution_id", *legacy_immigration_columns),
                on="institution_id",
                how="left",
            )
            .with_columns(
                pl.lit(scoring_config.version).alias("immigration_evidence_score_version")
            )
        )

        employer_scores_v2 = employer_metrics.select(
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
        processed = self.data_root / "processed"
        outputs = {
            "parent_organizations.parquet": parents.sort("parent_organization_id"),
            "legal_entities.parquet": legal_entities.sort("legal_entity_id"),
            "institutions.parquet": institutions,
            "lca_cases_resolved.parquet": lca,
            "perm_cases_resolved.parquet": perm,
            "h1b_petitions_resolved.parquet": uscis,
            "employer_metrics.parquet": employer_metrics,
            "employer_scores.parquet": employer_scores_v2,
            "employer_scores_v2.parquet": employer_scores_v2,
            "employer_scores_v1.parquet": employer_metrics_v1.select(
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
            ),
            "institution_scores_v1.parquet": institution_metrics_v1.select(
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
            ),
            "institution_metrics.parquet": institution_metrics,
            "data_health.parquet": data_health,
        }
        for name, frame in outputs.items():
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
