"""Build processed case, employer, institution, and health tables."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import cast

import polars as pl

from sponsor_intel.metrics.models import MetricsBuildSummary
from sponsor_intel.sources.manifests import write_json_atomic

METRIC_VERSION = "raw_metrics_v1"


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
    return (
        frame.filter(pl.col("legal_entity_id").is_not_null())
        .group_by("legal_entity_id")
        .agg(
            pl.len().alias(f"{prefix}_case_count"),
            relevant.cast(pl.Int64).sum().alias(f"relevant_{prefix}_count"),
            pl.col("fiscal_year").max().alias(f"last_{prefix}_activity_year"),
        )
    )


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
            pl.col("initial_approvals").sum(),
            pl.col("initial_denials").sum(),
            pl.col("continuing_approvals").sum(),
            pl.col("continuing_denials").sum(),
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
    counts = [
        "total_rd",
        "federal_rd",
        "computing_rd",
        "engineering_rd",
        "rd_personnel",
        "lca_case_count",
        "relevant_lca_count",
        "perm_case_count",
        "relevant_certified_perm_count",
        "initial_approvals",
        "initial_denials",
        "continuing_approvals",
        "continuing_denials",
    ]
    return (
        result.with_columns([pl.col(column).fill_null(0) for column in counts])
        .with_columns(
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


class MetricsPipeline:
    """Create all Phase 5 processed tables without scoring missing evidence."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root

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

        processed = self.data_root / "processed"
        outputs = {
            "parent_organizations.parquet": parents.sort("parent_organization_id"),
            "legal_entities.parquet": legal_entities.sort("legal_entity_id"),
            "institutions.parquet": institutions,
            "lca_cases_resolved.parquet": lca,
            "perm_cases_resolved.parquet": perm,
            "h1b_petitions_resolved.parquet": uscis,
            "employer_metrics.parquet": employer_metrics,
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
