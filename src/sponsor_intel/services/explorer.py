"""Read-only Product A query boundary for the Streamlit explorer."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import duckdb
import polars as pl

from sponsor_intel.case_status import canonical_case_status_sql
from sponsor_intel.config import load_settings

EvidenceStatus = Literal["UNKNOWN", "AVAILABLE"]
ExportFormat = Literal["csv", "parquet"]
EmployerSort = Literal[
    "overall_sponsorship",
    "sponsorship_history",
    "green_card_history",
    "h1b_history",
    "recent_activity",
    "name",
]
InstitutionSort = Literal[
    "overall_sponsorship",
    "green_card_history",
    "h1b_history",
    "recent_activity",
    "name",
]

EMPLOYER_SORT_LABELS: dict[EmployerSort, str] = {
    "overall_sponsorship": "Strongest overall sponsorship history",
    "green_card_history": "Strongest PERM sponsorship history",
    "h1b_history": "Strongest H-1B history",
    "recent_activity": "Most recent observed activity",
    "name": "Employer name",
}
INSTITUTION_SORT_LABELS: dict[InstitutionSort, str] = {
    "overall_sponsorship": "Strongest overall sponsorship history",
    "green_card_history": "Strongest PERM sponsorship history",
    "h1b_history": "Strongest H-1B history",
    "recent_activity": "Most recent observed activity",
    "name": "Institution name",
}

EVIDENCE_DISCLAIMER = (
    "Ratings summarize observed historical evidence from official sources. They are not "
    "sponsorship guarantees or legal advice. Verify the exact position and current employer "
    "policy before relying on the result."
)

EXPECTED_METRIC_VERSION = "product_a_metrics_v1"
EXPECTED_SCORE_VERSION = "product_a_scores_v1"


class IncompatibleProductADatabaseError(ValueError):
    """Raised when a presentation database is not a complete Product A build."""


@dataclass(frozen=True, slots=True)
class ExplorerStatus:
    """Current availability, version, and partial-period state."""

    phase: str
    build_id: str
    data_available: bool
    evidence_status: EvidenceStatus
    message: str
    disclaimer: str
    score_version: str | None = None
    latest_complete_fiscal_year: int | None = None
    current_partial_fiscal_year: int | None = None
    current_partial_quarter: int | None = None
    build_date: str | None = None
    release_tag: str | None = None


@dataclass(frozen=True, slots=True)
class OverviewMetrics:
    """Coverage metrics shown on the Product A home page."""

    legal_entity_count: int
    parent_organization_count: int
    institution_count: int
    relevant_lca_count: int
    relevant_certified_perm_count: int
    unresolved_entity_match_count: int
    source_coverage: pl.DataFrame


@dataclass(frozen=True, slots=True)
class EmployerFilters:
    """Safe, parameterized Product A employer filters."""

    search: str = ""
    organization_types: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    everify_statuses: tuple[str, ...] = ()
    role_family: str | None = None
    minimum_relevant_lca: int = 0
    minimum_relevant_perm: int = 0
    minimum_initial_approvals: int = 0
    minimum_h1b_stars: int | None = None
    minimum_green_card_stars: int | None = None
    minimum_overall_stars: int | None = None
    minimum_last_activity_year: int | None = None
    exclude_known_staffing_consulting: bool = False
    sort_by: EmployerSort = "overall_sponsorship"
    # Deprecated compatibility inputs. They are hidden scores and are not exposed in the UI.
    minimum_h1b_score: float | None = None
    minimum_green_card_score: float | None = None
    minimum_sponsorship_score: float | None = None
    opt_statuses: tuple[str, ...] = ()
    cap_exemption_statuses: tuple[str, ...] = ()
    evidence_confidences: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InstitutionFilters:
    """Safe, parameterized Product A institution filters."""

    search: str = ""
    controls: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    everify_statuses: tuple[str, ...] = ()
    minimum_relevant_lca: int = 0
    minimum_relevant_perm: int = 0
    minimum_h1b_stars: int | None = None
    minimum_green_card_stars: int | None = None
    minimum_overall_stars: int | None = None
    minimum_last_activity_year: int | None = None
    sort_by: InstitutionSort = "overall_sponsorship"
    # Read-only research context may still be narrowed for exported analysis.
    minimum_total_rd: int = 0
    minimum_computing_rd: int = 0
    minimum_engineering_rd: int = 0
    # Deprecated Product B inputs are accepted but never affect Product A queries.
    decision_readiness_tiers: tuple[str, ...] = ()
    cap_exemption_statuses: tuple[str, ...] = ()
    score_confidences: tuple[str, ...] = ()
    research_staff_h1b_policies: tuple[str, ...] = ()
    research_staff_pr_policies: tuple[str, ...] = ()
    perm_support_policies: tuple[str, ...] = ()
    eb1b_support_policies: tuple[str, ...] = ()
    minimum_core_policy_review_coverage: float | None = None
    minimum_h1b_score: float | None = None
    minimum_green_card_score: float | None = None
    minimum_sponsorship_score: float | None = None
    minimum_research_pathway_score: float | None = None


@dataclass(frozen=True, slots=True)
class OrganizationDetail:
    """Identity, ratings, raw evidence, context, and provenance for one organization."""

    summary: pl.DataFrame
    legal_entities: pl.DataFrame
    aliases: pl.DataFrame
    h1b_trends: pl.DataFrame
    perm_trends: pl.DataFrame
    rating_supporting_cases: pl.DataFrame
    relevant_titles: pl.DataFrame
    case_statuses: pl.DataFrame
    worksite_states: pl.DataFrame
    wage_summary: pl.DataFrame
    institutions: pl.DataFrame
    everify_evidence: pl.DataFrame
    opt_evidence: pl.DataFrame
    policy_evidence: pl.DataFrame
    provenance: pl.DataFrame


@dataclass(frozen=True, slots=True)
class EvidenceReviewQueues:
    """Reviewable entity and supplemental evidence queues."""

    entity: pl.DataFrame
    everify: pl.DataFrame
    opt: pl.DataFrame
    policy: pl.DataFrame


@dataclass(frozen=True, slots=True)
class DataHealthSnapshot:
    """Selected source artifacts, source coverage, and quality checks."""

    source_coverage: pl.DataFrame
    quality_checks: pl.DataFrame
    source_artifacts: pl.DataFrame


@runtime_checkable
class ExplorerService(Protocol):
    """Contract that keeps Streamlit independent of analytical storage."""

    def get_status(self) -> ExplorerStatus: ...

    def get_overview(self) -> OverviewMetrics: ...

    def list_employers(
        self, filters: EmployerFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame: ...

    def list_institutions(
        self, filters: InstitutionFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame: ...

    def employer_facets(self) -> dict[str, list[str]]: ...

    def institution_facets(self) -> dict[str, list[str]]: ...

    def search_organizations(self, search: str, *, limit: int = 50) -> pl.DataFrame: ...

    def compare_organizations(self, organization_ids: tuple[str, ...]) -> pl.DataFrame: ...

    def get_rating_supporting_cases(
        self, organization_id: str, *, limit: int = 250
    ) -> pl.DataFrame: ...

    def get_organization_detail(self, organization_id: str) -> OrganizationDetail | None: ...

    def get_evidence_review(self, *, limit: int = 500) -> EvidenceReviewQueues: ...

    def get_data_health(self) -> DataHealthSnapshot: ...

    def export_employers(self, filters: EmployerFilters, file_format: ExportFormat) -> bytes: ...

    def export_institutions(
        self, filters: InstitutionFilters, file_format: ExportFormat
    ) -> bytes: ...


def _empty() -> pl.DataFrame:
    return pl.DataFrame()


class FoundationExplorerService:
    """Fallback that never fabricates evidence when no database is available."""

    def __init__(self, message: str | None = None) -> None:
        self._message = message or (
            "No presentation database has been built. Evidence remains UNKNOWN."
        )

    def get_status(self) -> ExplorerStatus:
        return ExplorerStatus(
            phase="Product A",
            build_id="database-unavailable",
            data_available=False,
            evidence_status="UNKNOWN",
            message=self._message,
            disclaimer=EVIDENCE_DISCLAIMER,
        )

    def get_overview(self) -> OverviewMetrics:
        return OverviewMetrics(0, 0, 0, 0, 0, 0, _empty())

    def list_employers(
        self, filters: EmployerFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame:
        return _empty()

    def list_institutions(
        self, filters: InstitutionFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame:
        return _empty()

    def employer_facets(self) -> dict[str, list[str]]:
        return {}

    def institution_facets(self) -> dict[str, list[str]]:
        return {}

    def search_organizations(self, search: str, *, limit: int = 50) -> pl.DataFrame:
        return _empty()

    def compare_organizations(self, organization_ids: tuple[str, ...]) -> pl.DataFrame:
        return _empty()

    def get_rating_supporting_cases(
        self, organization_id: str, *, limit: int = 250
    ) -> pl.DataFrame:
        return _empty()

    def get_organization_detail(self, organization_id: str) -> OrganizationDetail | None:
        return None

    def get_evidence_review(self, *, limit: int = 500) -> EvidenceReviewQueues:
        return EvidenceReviewQueues(_empty(), _empty(), _empty(), _empty())

    def get_data_health(self) -> DataHealthSnapshot:
        return DataHealthSnapshot(_empty(), _empty(), _empty())

    def export_employers(self, filters: EmployerFilters, file_format: ExportFormat) -> bytes:
        return b""

    def export_institutions(self, filters: InstitutionFilters, file_format: ExportFormat) -> bytes:
        return b""


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _available_column(columns: set[str], column: str, fallback: str) -> str:
    return column if column in columns else fallback


def _serialized(frame: pl.DataFrame, file_format: ExportFormat) -> bytes:
    if file_format == "csv":
        csv_frame = frame.with_columns(
            *[
                (
                    pl.col(column)
                    .list.eval(pl.element().cast(pl.String))
                    .list.join("|")
                    .alias(column)
                    if isinstance(dtype, pl.List)
                    else pl.col(column).struct.json_encode().alias(column)
                )
                for column, dtype in frame.schema.items()
                if isinstance(dtype, (pl.List, pl.Struct))
            ]
        )
        return csv_frame.write_csv().encode("utf-8")
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


_EMPLOYER_FIELDS: dict[str, tuple[tuple[str, ...], str]] = {
    "organization_id": (("organization_id",), "NULL::VARCHAR"),
    "legal_entity_id": (("legal_entity_id",), "NULL::VARCHAR"),
    "parent_organization_id": (("parent_organization_id",), "NULL::VARCHAR"),
    "organization_name": (
        ("display_name", "organization_name", "canonical_name"),
        "'UNKNOWN'::VARCHAR",
    ),
    "identity_scope": (("identity_scope",), "'LEGAL_ENTITY'::VARCHAR"),
    "legal_entity_count": (("legal_entity_count",), "1::BIGINT"),
    "organization_type": (("organization_type",), "'UNKNOWN'::VARCHAR"),
    "state": (("state",), "NULL::VARCHAR"),
    "is_staffing_or_consulting": (("is_staffing_or_consulting",), "false"),
    "relevant_lca_count": (
        ("relevant_certified_lca_count", "relevant_lca_count"),
        "0::BIGINT",
    ),
    "relevant_certified_withdrawn_lca_count": (
        ("relevant_certified_withdrawn_lca_count",),
        "0::BIGINT",
    ),
    "weighted_relevant_lca_count": (
        ("weighted_relevant_lca_count", "relevant_lca_count"),
        "0::DOUBLE",
    ),
    "relevant_certified_perm_count": (
        ("relevant_certified_perm_count",),
        "0::BIGINT",
    ),
    "relevant_certified_expired_perm_count": (
        ("relevant_certified_expired_perm_count",),
        "0::BIGINT",
    ),
    "weighted_relevant_perm_count": (
        ("weighted_relevant_perm_count", "relevant_certified_perm_count"),
        "0::DOUBLE",
    ),
    "initial_approvals": (("initial_approvals",), "0::BIGINT"),
    "initial_denials": (("initial_denials",), "0::BIGINT"),
    "lca_case_count": (("lca_case_count",), "0::BIGINT"),
    "perm_case_count": (("perm_case_count",), "0::BIGINT"),
    "lca_active_years": (("lca_complete_active_years", "lca_active_years"), "0::BIGINT"),
    "perm_active_years": (("perm_complete_active_years", "perm_active_years"), "0::BIGINT"),
    "last_lca_activity_year": (("last_lca_activity_year",), "NULL::INTEGER"),
    "last_perm_activity_year": (("last_perm_activity_year",), "NULL::INTEGER"),
    "last_observed_activity_year": (
        ("latest_observed_year", "last_observed_activity_year"),
        "NULL::INTEGER",
    ),
    "lca_role_families": (
        ("lca_relevant_job_families", "lca_role_families"),
        "[]::VARCHAR[]",
    ),
    "perm_role_families": (
        ("perm_relevant_job_families", "perm_role_families"),
        "[]::VARCHAR[]",
    ),
    "everify_status": (("everify_status",), "'NOT_CHECKED'::VARCHAR"),
    "known_opt_observation": (("known_opt_observation",), "'UNKNOWN'::VARCHAR"),
    "cap_exemption_status": (("cap_exemption_status",), "'UNKNOWN'::VARCHAR"),
    "source_coverage_ratio": (("source_coverage_ratio",), "NULL::DOUBLE"),
    "source_coverage_count": (("source_coverage_count",), "NULL::BIGINT"),
    "entity_coverage_state": (("entity_coverage_state",), "'UNRESOLVED_IDENTITY'::VARCHAR"),
    "h1b_entity_coverage_state": (
        ("h1b_entity_coverage_state",),
        "'UNRESOLVED_IDENTITY'::VARCHAR",
    ),
    "perm_entity_coverage_state": (
        ("perm_entity_coverage_state",),
        "'UNRESOLVED_IDENTITY'::VARCHAR",
    ),
    "has_partial_period": (("has_partial_period",), "false"),
    "current_partial_fiscal_year": (("current_partial_fiscal_year",), "NULL::INTEGER"),
    "current_partial_quarter": (("current_partial_quarter",), "NULL::INTEGER"),
    "h1b_history_score": (("h1b_history_score",), "NULL::DOUBLE"),
    "h1b_history_status": (("h1b_history_status",), "'UNRATED'::VARCHAR"),
    "h1b_history_star_rating": (("h1b_history_star_rating",), "NULL::INTEGER"),
    "h1b_history_stars": (("h1b_history_stars",), "'Unrated'::VARCHAR"),
    "h1b_history_star_label": (
        ("h1b_history_star_label",),
        "'Unrated'::VARCHAR",
    ),
    "h1b_history_explanation": (("h1b_history_explanation",), "'Unrated'::VARCHAR"),
    "h1b_history_coverage": (("h1b_history_coverage",), "0::DOUBLE"),
    "green_card_history_score": (("green_card_history_score",), "NULL::DOUBLE"),
    "green_card_history_status": (("green_card_history_status",), "'UNRATED'::VARCHAR"),
    "green_card_history_star_rating": (("green_card_history_star_rating",), "NULL::INTEGER"),
    "green_card_history_stars": (
        ("green_card_history_stars",),
        "'Unrated'::VARCHAR",
    ),
    "green_card_history_star_label": (
        ("green_card_history_star_label",),
        "'Unrated'::VARCHAR",
    ),
    "green_card_history_explanation": (
        ("green_card_history_explanation",),
        "'Unrated'::VARCHAR",
    ),
    "green_card_history_coverage": (("green_card_history_coverage",), "0::DOUBLE"),
    "overall_sponsorship_score": (
        ("overall_sponsorship_score",),
        "NULL::DOUBLE",
    ),
    "overall_sponsorship_status": (
        ("overall_sponsorship_status",),
        "'UNRATED'::VARCHAR",
    ),
    "overall_sponsorship_star_rating": (
        ("overall_sponsorship_star_rating",),
        "NULL::INTEGER",
    ),
    "overall_sponsorship_stars": (
        ("overall_sponsorship_stars",),
        "'Unrated'::VARCHAR",
    ),
    "overall_sponsorship_star_label": (
        ("overall_sponsorship_star_label",),
        "'Unrated'::VARCHAR",
    ),
    "overall_sponsorship_explanation": (
        ("overall_sponsorship_explanation",),
        "'Unrated'::VARCHAR",
    ),
    "overall_sponsorship_coverage": (
        ("overall_sponsorship_coverage",),
        "0::DOUBLE",
    ),
    "score_version": (("score_version",), "'score-version-unknown'::VARCHAR"),
    "metric_version": (("metric_version",), "'metric-version-unknown'::VARCHAR"),
}


_INSTITUTION_FIELDS: dict[str, tuple[tuple[str, ...], str]] = {
    **_EMPLOYER_FIELDS,
    "organization_type": (("organization_type", "organization_type_raw"), "'UNKNOWN'::VARCHAR"),
    "institution_id": (("institution_id",), "NULL::VARCHAR"),
    "official_name": (("official_name", "institution_name"), "'UNKNOWN'::VARCHAR"),
    "legal_employer_name": (
        ("legal_employer_name", "legal_name", "official_name"),
        "'UNKNOWN'::VARCHAR",
    ),
    "parent_organization_name": (
        ("parent_organization_name", "parent_system", "system_name"),
        "NULL::VARCHAR",
    ),
    "control": (("control",), "'UNKNOWN'::VARCHAR"),
    "sector": (("sector",), "'UNKNOWN'::VARCHAR"),
    "ipeds_unitid": (("ipeds_unitid",), "NULL::VARCHAR"),
    "higher_education_context": (
        ("higher_education_context",),
        "'Higher-education institution; exact cap-exempt status requires verification.'::VARCHAR",
    ),
    "latest_herd_year": (("survey_year",), "NULL::INTEGER"),
    "total_rd": (("total_rd",), "NULL::DOUBLE"),
    "computing_rd": (("computing_rd",), "NULL::DOUBLE"),
    "engineering_rd": (("engineering_rd",), "NULL::DOUBLE"),
    "federal_rd": (("federal_rd",), "NULL::DOUBLE"),
    "research_scale_score": (("research_scale_score",), "NULL::DOUBLE"),
    "research_scale_status": (
        ("research_scale_status",),
        "'UNRATED'::VARCHAR",
    ),
    "research_scale_star_rating": (("research_scale_star_rating",), "NULL::INTEGER"),
    "research_scale_stars": (
        ("research_scale_stars",),
        "'Unrated'::VARCHAR",
    ),
    "research_scale_star_label": (
        ("research_scale_star_label",),
        "'Unrated'::VARCHAR",
    ),
    "research_scale_explanation": (
        ("research_scale_explanation",),
        "'Unrated'::VARCHAR",
    ),
    "research_staff_h1b_policy": (
        ("research_staff_h1b_policy",),
        "'UNKNOWN'::VARCHAR",
    ),
    "research_staff_permanent_residence_policy": (
        ("research_staff_permanent_residence_policy",),
        "'UNKNOWN'::VARCHAR",
    ),
    "general_staff_permanent_residence_policy": (
        ("general_staff_permanent_residence_policy",),
        "'UNKNOWN'::VARCHAR",
    ),
    "perm_support": (("perm_support",), "'UNKNOWN'::VARCHAR"),
    "eb1b_support": (("eb1b_support",), "'UNKNOWN'::VARCHAR"),
    "policy_review_status": (("policy_review_status",), "'NOT_STARTED'::VARCHAR"),
    "policy_evidence_role": (
        ("policy_evidence_role",),
        "'Supplemental; incomplete; not used in sponsorship ratings'::VARCHAR",
    ),
}


_PRODUCT_A_REQUIRED_RELATION_COLUMNS: dict[str, frozenset[str]] = {
    "vw_employer_explorer": frozenset(_EMPLOYER_FIELDS),
    "vw_institution_explorer": frozenset(
        (set(_INSTITUTION_FIELDS) - {"latest_herd_year", "organization_type"})
        | {"survey_year", "organization_type_raw"}
    ),
    "legal_entities": frozenset(
        {
            "legal_entity_id",
            "legal_name",
            "parent_organization_id",
            "city",
            "state",
            "postal_code",
            "organization_type",
            "institution_id",
            "review_status",
        }
    ),
    "parent_organizations": frozenset({"parent_organization_id"}),
    "institutions": frozenset({"institution_id"}),
    "entity_aliases": frozenset(
        {
            "alias_raw",
            "source_id",
            "city",
            "state",
            "match_method",
            "match_score",
            "review_status",
            "occurrence_count",
            "legal_entity_id",
            "parent_organization_id",
        }
    ),
    "lca_cases_resolved": frozenset(
        {
            "case_id",
            "source_artifact_id",
            "source_file_name",
            "ingested_at",
            "fiscal_year",
            "fiscal_quarter",
            "is_partial_period",
            "visa_class",
            "case_status",
            "legal_entity_id",
            "parent_organization_id",
            "organization_id",
            "job_title_raw",
            "role_family",
            "technical_role",
            "worksite_city",
            "worksite_state",
            "wage_from",
            "wage_to",
            "wage_unit",
            "schema_version",
            "source_url",
            "source_sha256",
        }
    ),
    "perm_cases_resolved": frozenset(
        {
            "case_id",
            "source_artifact_id",
            "source_file_name",
            "ingested_at",
            "fiscal_year",
            "fiscal_quarter",
            "is_partial_period",
            "case_status",
            "legal_entity_id",
            "parent_organization_id",
            "organization_id",
            "job_title_raw",
            "role_family",
            "technical_role",
            "worksite_city",
            "worksite_state",
            "wage_from",
            "wage_to",
            "wage_unit",
            "schema_version",
            "source_url",
            "source_sha256",
        }
    ),
    "h1b_petitions_resolved": frozenset(
        {
            "source_artifact_id",
            "source_file_name",
            "ingested_at",
            "fiscal_year",
            "is_partial_period",
            "legal_entity_id",
            "parent_organization_id",
            "organization_id",
        }
    ),
    "vw_h1b_trends": frozenset({"organization_id", "fiscal_year"}),
    "vw_perm_trends": frozenset({"organization_id", "fiscal_year"}),
    "vw_relevant_titles": frozenset(
        {"organization_id", "source_id", "job_title_raw", "role_family", "record_count"}
    ),
    "vw_everify_evidence": frozenset({"organization_id", "retrieved_at"}),
    "vw_opt_evidence": frozenset({"organization_id", "report_year", "rank", "review_status"}),
    "vw_policy_evidence": frozenset(
        {
            "organization_id",
            "institution_id",
            "fact_type",
            "retrieved_at",
            "human_review_status",
            "exact_excerpt_verified",
            "fact_is_current",
            "valid_to",
        }
    ),
    "vw_entity_review_queue": frozenset({"review_status"}),
    "vw_everify_review_queue": frozenset({"priority_rank"}),
    "vw_policy_review_queue": frozenset({"policy_fact_id"}),
    "vw_data_health": frozenset(
        {
            "source_id",
            "latest_complete_fiscal_year",
            "current_partial_fiscal_year",
            "current_partial_quarter",
        }
    ),
    "vw_source_artifacts": frozenset(
        {"source_artifact_id", "source_id", "fiscal_year", "download_url", "sha256"}
    ),
    "vw_quality_checks": frozenset(
        {"check_id", "category", "status", "critical", "build_id", "checked_at"}
    ),
}

_PRODUCT_A_NONEMPTY_RELATIONS = (
    "vw_employer_explorer",
    "vw_institution_explorer",
    "lca_cases_resolved",
    "perm_cases_resolved",
    "vw_data_health",
    "vw_source_artifacts",
)


_EMPLOYER_ORDER_BY: dict[EmployerSort, str] = {
    "overall_sponsorship": """
        overall_sponsorship_score DESC NULLS LAST,
        green_card_history_score DESC NULLS LAST,
        h1b_history_score DESC NULLS LAST,
        last_observed_activity_year DESC NULLS LAST,
        organization_name
    """,
    "sponsorship_history": """
        overall_sponsorship_score DESC NULLS LAST,
        green_card_history_score DESC NULLS LAST,
        h1b_history_score DESC NULLS LAST,
        last_observed_activity_year DESC NULLS LAST,
        organization_name
    """,
    "green_card_history": """
        green_card_history_score DESC NULLS LAST,
        overall_sponsorship_score DESC NULLS LAST,
        h1b_history_score DESC NULLS LAST,
        last_observed_activity_year DESC NULLS LAST,
        organization_name
    """,
    "h1b_history": """
        h1b_history_score DESC NULLS LAST,
        overall_sponsorship_score DESC NULLS LAST,
        green_card_history_score DESC NULLS LAST,
        last_observed_activity_year DESC NULLS LAST,
        organization_name
    """,
    "recent_activity": """
        last_observed_activity_year DESC NULLS LAST,
        overall_sponsorship_score DESC NULLS LAST,
        green_card_history_score DESC NULLS LAST,
        h1b_history_score DESC NULLS LAST,
        organization_name
    """,
    "name": "organization_name",
}

_INSTITUTION_ORDER_BY: dict[InstitutionSort, str] = {
    "overall_sponsorship": _EMPLOYER_ORDER_BY["overall_sponsorship"].replace(
        "organization_name", "official_name"
    ),
    "green_card_history": _EMPLOYER_ORDER_BY["green_card_history"].replace(
        "organization_name", "official_name"
    ),
    "h1b_history": _EMPLOYER_ORDER_BY["h1b_history"].replace("organization_name", "official_name"),
    "recent_activity": _EMPLOYER_ORDER_BY["recent_activity"].replace(
        "organization_name", "official_name"
    ),
    "name": "official_name",
}


class DuckDBExplorerService:
    """Parameterized, read-only queries over Product A presentation views."""

    def __init__(
        self,
        database_path: Path,
        *,
        release_tag: str | None = None,
        build_id: str | None = None,
        build_date: str | None = None,
    ) -> None:
        if not database_path.is_file():
            raise ValueError(f"Presentation database is unavailable: {database_path}")
        self.database_path = database_path
        self.release_tag = release_tag
        self.runtime_build_id = build_id
        self.runtime_build_date = build_date
        self._connection = duckdb.connect(str(self.database_path), read_only=True)
        self._columns: dict[str, set[str]] = {}
        try:
            self._validate_product_a_database()
        except Exception:
            self._connection.close()
            raise

    def _query(self, sql: str, parameters: list[object] | None = None) -> pl.DataFrame:
        return self._connection.execute(sql, parameters or []).pl()

    def _relation_exists(self, relation: str) -> bool:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [relation],
        ).fetchone()
        return row is not None and int(row[0]) > 0

    def _relation_columns(self, relation: str) -> set[str]:
        if relation not in self._columns:
            self._columns[relation] = {
                str(row[0]) for row in self._connection.execute(f"DESCRIBE {relation}").fetchall()
            }
        return self._columns[relation]

    def _validate_product_a_database(self) -> None:
        problems: list[str] = []
        for relation, required_columns in _PRODUCT_A_REQUIRED_RELATION_COLUMNS.items():
            if not self._relation_exists(relation):
                problems.append(f"missing relation {relation}")
                continue
            missing_columns = required_columns.difference(self._relation_columns(relation))
            if missing_columns:
                problems.append(f"{relation} missing columns: {', '.join(sorted(missing_columns))}")
        if problems:
            raise IncompatibleProductADatabaseError(
                "Presentation database is incompatible with Product A (" + "; ".join(problems) + ")"
            )

        for relation in _PRODUCT_A_NONEMPTY_RELATIONS:
            count = self._connection.execute(f"SELECT count(*) FROM {relation}").fetchone()
            if count is None or int(count[0]) == 0:
                raise IncompatibleProductADatabaseError(
                    f"Presentation database is incompatible with Product A ({relation} is empty)"
                )

        for relation in ("vw_employer_explorer", "vw_institution_explorer"):
            versions = {
                (str(metric_version), str(score_version))
                for metric_version, score_version in self._connection.execute(
                    f"SELECT DISTINCT metric_version, score_version FROM {relation}"
                ).fetchall()
            }
            expected = {(EXPECTED_METRIC_VERSION, EXPECTED_SCORE_VERSION)}
            if versions != expected:
                raise IncompatibleProductADatabaseError(
                    "Presentation database is incompatible with Product A "
                    f"({relation} has versions {sorted(versions)!r}; expected {sorted(expected)!r})"
                )

    def _relation_sql(
        self,
        relation: str,
        fields: dict[str, tuple[tuple[str, ...], str]],
    ) -> str:
        columns = self._relation_columns(relation)
        projections: list[str] = []
        for alias, (candidates, _) in fields.items():
            selected = next((candidate for candidate in candidates if candidate in columns), None)
            if selected is None:
                raise IncompatibleProductADatabaseError(
                    f"Presentation database is incompatible with Product A ({relation} cannot "
                    f"provide {alias})"
                )
            projections.append(f"{selected} AS {alias}")
        return f"SELECT {', '.join(projections)} FROM {relation}"

    def _employer_sql(self) -> str:
        return self._relation_sql("vw_employer_explorer", _EMPLOYER_FIELDS)

    def _institution_sql(self) -> str:
        return self._relation_sql("vw_institution_explorer", _INSTITUTION_FIELDS)

    def close(self) -> None:
        """Release the read-only database handle."""

        self._connection.close()

    def get_status(self) -> ExplorerStatus:
        health = self._query(
            """
            SELECT
                max(latest_complete_fiscal_year) AS latest_complete_fiscal_year,
                max(current_partial_fiscal_year) AS current_partial_fiscal_year,
                max(current_partial_quarter) AS current_partial_quarter
            FROM vw_data_health
            WHERE source_id IN ('dol_lca', 'dol_perm', 'uscis_h1b')
            """
        ).to_dicts()[0]
        partial = health["current_partial_fiscal_year"]
        message = "Observed government and institution evidence is available."
        if partial is not None:
            period = f"FY{partial}"
            if health["current_partial_quarter"] is not None:
                period += f" Q{health['current_partial_quarter']}"
            message += f" {period} is partial and is not comparable with a complete year."
        build = self._query(
            """
            SELECT
                coalesce(
                    (SELECT max(build_id) FROM vw_quality_checks),
                    (SELECT max(metric_version) FROM vw_employer_explorer)
                ) AS build_id,
                (SELECT max(checked_at) FROM vw_quality_checks) AS build_date,
                (SELECT max(score_version) FROM vw_employer_explorer) AS score_version
            """
        ).to_dicts()[0]
        return ExplorerStatus(
            phase="Product A",
            build_id=self.runtime_build_id or build["build_id"] or "build-unknown",
            data_available=True,
            evidence_status="AVAILABLE",
            message=message,
            disclaimer=EVIDENCE_DISCLAIMER,
            score_version=build["score_version"],
            latest_complete_fiscal_year=health["latest_complete_fiscal_year"],
            current_partial_fiscal_year=partial,
            current_partial_quarter=health["current_partial_quarter"],
            build_date=self.runtime_build_date or build["build_date"],
            release_tag=self.release_tag,
        )

    def get_overview(self) -> OverviewMetrics:
        counts = self._query(
            f"""
            WITH employers AS ({self._employer_sql()})
            SELECT
                (SELECT count(*) FROM legal_entities) AS legal_entity_count,
                (SELECT count(*) FROM parent_organizations) AS parent_organization_count,
                (SELECT count(*) FROM institutions) AS institution_count,
                (SELECT coalesce(sum(relevant_lca_count), 0) FROM employers
                    WHERE identity_scope = 'LEGAL_ENTITY') AS relevant_lca_count,
                (SELECT coalesce(sum(relevant_certified_perm_count), 0) FROM employers
                    WHERE identity_scope = 'LEGAL_ENTITY') AS relevant_certified_perm_count,
                (SELECT count(*) FROM vw_entity_review_queue) AS unresolved_entity_match_count
            """
        ).to_dicts()[0]
        return OverviewMetrics(
            legal_entity_count=int(counts["legal_entity_count"]),
            parent_organization_count=int(counts["parent_organization_count"]),
            institution_count=int(counts["institution_count"]),
            relevant_lca_count=int(counts["relevant_lca_count"]),
            relevant_certified_perm_count=int(counts["relevant_certified_perm_count"]),
            unresolved_entity_match_count=int(counts["unresolved_entity_match_count"]),
            source_coverage=self._query("SELECT * FROM vw_data_health ORDER BY source_id"),
        )

    @staticmethod
    def _employer_where(filters: EmployerFilters) -> tuple[str, list[object]]:
        clauses = [
            "relevant_lca_count >= ?",
            "relevant_certified_perm_count >= ?",
            "initial_approvals >= ?",
        ]
        parameters: list[object] = [
            filters.minimum_relevant_lca,
            filters.minimum_relevant_perm,
            filters.minimum_initial_approvals,
        ]
        if filters.search.strip():
            clauses.append(
                "(organization_name ILIKE ? OR organization_id IN ("
                "SELECT legal_entity_id FROM entity_aliases WHERE alias_raw ILIKE ? "
                "UNION SELECT parent_organization_id FROM entity_aliases WHERE alias_raw ILIKE ?"
                "))"
            )
            term = f"%{filters.search.strip()}%"
            parameters.extend([term, term, term])
        for column, values in (
            ("organization_type", filters.organization_types),
            ("state", filters.states),
            ("everify_status", filters.everify_statuses),
        ):
            if values:
                clauses.append(f"{column} IN ({_placeholders(values)})")
                parameters.extend(values)
        if filters.role_family:
            clauses.append(
                "EXISTS (SELECT 1 FROM vw_relevant_titles AS title "
                "WHERE title.organization_id = explorer.organization_id "
                "AND title.role_family = ?)"
            )
            parameters.append(filters.role_family)
        if filters.minimum_last_activity_year is not None:
            clauses.append("last_observed_activity_year >= ?")
            parameters.append(filters.minimum_last_activity_year)
        for column, minimum in (
            ("h1b_history_star_rating", filters.minimum_h1b_stars),
            ("green_card_history_star_rating", filters.minimum_green_card_stars),
            ("overall_sponsorship_star_rating", filters.minimum_overall_stars),
            ("h1b_history_score", filters.minimum_h1b_score),
            ("green_card_history_score", filters.minimum_green_card_score),
            ("overall_sponsorship_score", filters.minimum_sponsorship_score),
        ):
            if minimum is not None:
                clauses.append(f"{column} >= ?")
                parameters.append(minimum)
        if filters.exclude_known_staffing_consulting:
            clauses.append("coalesce(is_staffing_or_consulting, false) IS FALSE")
        return " AND ".join(clauses), parameters

    def list_employers(
        self, filters: EmployerFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame:
        selected = filters if filters is not None else EmployerFilters()
        where, parameters = self._employer_where(selected)
        limit_sql = "" if limit is None else " LIMIT ?"
        if limit is not None:
            parameters.append(max(1, min(limit, 10_000)))
        return self._query(
            f"""
            WITH explorer AS ({self._employer_sql()})
            SELECT * FROM explorer
            WHERE {where}
            ORDER BY {_EMPLOYER_ORDER_BY[selected.sort_by]}
            {limit_sql}
            """,
            parameters,
        )

    @staticmethod
    def _institution_where(filters: InstitutionFilters) -> tuple[str, list[object]]:
        clauses = ["relevant_lca_count >= ?", "relevant_certified_perm_count >= ?"]
        parameters: list[object] = [filters.minimum_relevant_lca, filters.minimum_relevant_perm]
        if filters.search.strip():
            clauses.append(
                "(official_name ILIKE ? OR legal_employer_name ILIKE ? "
                "OR parent_organization_name ILIKE ?)"
            )
            term = f"%{filters.search.strip()}%"
            parameters.extend([term, term, term])
        for column, values in (
            ("control", filters.controls),
            ("state", filters.states),
            ("everify_status", filters.everify_statuses),
        ):
            if values:
                clauses.append(f"{column} IN ({_placeholders(values)})")
                parameters.extend(values)
        for column, minimum in (
            ("total_rd", filters.minimum_total_rd if filters.minimum_total_rd > 0 else None),
            (
                "computing_rd",
                filters.minimum_computing_rd if filters.minimum_computing_rd > 0 else None,
            ),
            (
                "engineering_rd",
                filters.minimum_engineering_rd if filters.minimum_engineering_rd > 0 else None,
            ),
            ("h1b_history_star_rating", filters.minimum_h1b_stars),
            ("green_card_history_star_rating", filters.minimum_green_card_stars),
            ("overall_sponsorship_star_rating", filters.minimum_overall_stars),
            ("h1b_history_score", filters.minimum_h1b_score),
            ("green_card_history_score", filters.minimum_green_card_score),
            ("overall_sponsorship_score", filters.minimum_sponsorship_score),
        ):
            if minimum is not None:
                clauses.append(f"{column} >= ?")
                parameters.append(minimum)
        if filters.minimum_last_activity_year is not None:
            clauses.append("last_observed_activity_year >= ?")
            parameters.append(filters.minimum_last_activity_year)
        return " AND ".join(clauses), parameters

    def list_institutions(
        self, filters: InstitutionFilters | None = None, *, limit: int | None = 500
    ) -> pl.DataFrame:
        selected = filters if filters is not None else InstitutionFilters()
        where, parameters = self._institution_where(selected)
        limit_sql = "" if limit is None else " LIMIT ?"
        if limit is not None:
            parameters.append(max(1, min(limit, 10_000)))
        return self._query(
            f"""
            WITH explorer AS ({self._institution_sql()})
            SELECT * FROM explorer
            WHERE {where}
            ORDER BY {_INSTITUTION_ORDER_BY[selected.sort_by]}
            {limit_sql}
            """,
            parameters,
        )

    def employer_facets(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        relation = self._employer_sql()
        for name, column in (
            ("organization_types", "organization_type"),
            ("states", "state"),
            ("everify_statuses", "everify_status"),
        ):
            result[name] = self._query(
                f"""
                WITH explorer AS ({relation})
                SELECT DISTINCT {column} AS value FROM explorer
                WHERE {column} IS NOT NULL ORDER BY value
                """
            )["value"].to_list()
        result["role_families"] = self._query(
            """
            SELECT DISTINCT role_family AS value FROM vw_relevant_titles
            WHERE role_family IS NOT NULL ORDER BY value
            """
        )["value"].to_list()
        return result

    def institution_facets(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        relation = self._institution_sql()
        for name, column in (
            ("controls", "control"),
            ("states", "state"),
            ("everify_statuses", "everify_status"),
        ):
            result[name] = self._query(
                f"""
                WITH explorer AS ({relation})
                SELECT DISTINCT {column} AS value FROM explorer
                WHERE {column} IS NOT NULL ORDER BY value
                """
            )["value"].to_list()
        return result

    def search_organizations(self, search: str, *, limit: int = 50) -> pl.DataFrame:
        term = f"%{search.strip()}%"
        return self._query(
            f"""
            WITH explorer AS ({self._employer_sql()})
            SELECT organization_id, organization_name, identity_scope, organization_type, state,
                overall_sponsorship_stars, overall_sponsorship_star_label
            FROM explorer
            WHERE organization_name ILIKE ? OR organization_id IN (
                SELECT legal_entity_id FROM entity_aliases WHERE alias_raw ILIKE ?
                UNION SELECT parent_organization_id FROM entity_aliases WHERE alias_raw ILIKE ?
            )
            ORDER BY {_EMPLOYER_ORDER_BY["overall_sponsorship"]}
            LIMIT ?
            """,
            [term, term, term, max(1, min(limit, 100))],
        )

    def compare_organizations(self, organization_ids: tuple[str, ...]) -> pl.DataFrame:
        """Return one Product A comparison row for each of at most five organizations."""

        selected = tuple(dict.fromkeys(value for value in organization_ids if value.strip()))
        if not selected:
            return _empty()
        if len(selected) > 5:
            raise ValueError("Comparison supports at most five organizations")
        result = self._query(
            f"""
            WITH employers AS ({self._employer_sql()}),
            institutions AS ({self._institution_sql()})
            SELECT
                e.*,
                i.institution_id,
                i.official_name AS research_institution,
                i.control,
                i.sector,
                i.higher_education_context,
                i.latest_herd_year,
                i.total_rd,
                i.computing_rd,
                i.engineering_rd,
                i.federal_rd,
                i.research_scale_status,
                i.research_scale_star_rating,
                i.research_scale_stars,
                i.research_scale_star_label,
                i.research_scale_explanation
            FROM employers AS e
            LEFT JOIN LATERAL (
                SELECT * FROM institutions AS candidate
                WHERE candidate.organization_id = e.organization_id
                    OR candidate.legal_entity_id = e.organization_id
                    OR candidate.parent_organization_id = e.organization_id
                ORDER BY candidate.official_name
                LIMIT 1
            ) AS i ON TRUE
            WHERE e.organization_id IN ({_placeholders(selected)})
            """,
            list(selected),
        )
        order = pl.DataFrame(
            {"organization_id": selected, "_selection_order": range(len(selected))}
        )
        return (
            result.join(order, on="organization_id", how="left")
            .sort("_selection_order")
            .drop("_selection_order")
        )

    @staticmethod
    def _organization_predicate(alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        return (
            f"({prefix}organization_id = ? OR {prefix}legal_entity_id = ? "
            f"OR {prefix}parent_organization_id = ?)"
        )

    def get_rating_supporting_cases(
        self, organization_id: str, *, limit: int = 250
    ) -> pl.DataFrame:
        """Return bounded, exact DOL rows that contribute positive Product A rating weight."""

        selected_limit = max(1, min(limit, 1_000))
        lca_status = canonical_case_status_sql("l.case_status")
        perm_status = canonical_case_status_sql("p.case_status")
        lca_predicate = self._organization_predicate("l")
        perm_predicate = self._organization_predicate("p")
        parameters: list[object] = [organization_id] * 7
        parameters.append(selected_limit)
        return self._query(
            f"""
            WITH rating_scope AS (
                SELECT h1b_history_status, green_card_history_status
                FROM vw_employer_explorer
                WHERE organization_id = ?
            )
            SELECT * FROM (
                SELECT
                    'dol_lca' AS source_id,
                    'H-1B LCA' AS program,
                    l.visa_class,
                    l.case_id,
                    l.job_title_raw,
                    l.role_family,
                    l.case_status AS case_status_raw,
                    {lca_status} AS canonical_status,
                    l.worksite_city,
                    l.worksite_state,
                    l.wage_from,
                    l.wage_to,
                    l.wage_unit,
                    l.fiscal_year,
                    l.fiscal_quarter,
                    l.is_partial_period,
                    l.source_artifact_id,
                    l.source_file_name,
                    l.source_url AS official_url,
                    l.source_sha256 AS sha256,
                    l.schema_version
                FROM lca_cases_resolved AS l
                CROSS JOIN rating_scope AS rating
                WHERE {lca_predicate}
                    AND rating.h1b_history_status = 'RATED'
                    AND l.technical_role IS TRUE
                    AND upper(trim(coalesce(l.visa_class, ''))) = 'H-1B'
                    AND {lca_status} IN ('CERTIFIED', 'CERTIFIED-WITHDRAWN')
                UNION ALL
                SELECT
                    'dol_perm' AS source_id,
                    'PERM' AS program,
                    NULL::VARCHAR AS visa_class,
                    p.case_id,
                    p.job_title_raw,
                    p.role_family,
                    p.case_status AS case_status_raw,
                    {perm_status} AS canonical_status,
                    p.worksite_city,
                    p.worksite_state,
                    p.wage_from,
                    p.wage_to,
                    p.wage_unit,
                    p.fiscal_year,
                    p.fiscal_quarter,
                    p.is_partial_period,
                    p.source_artifact_id,
                    p.source_file_name,
                    p.source_url AS official_url,
                    p.source_sha256 AS sha256,
                    p.schema_version
                FROM perm_cases_resolved AS p
                CROSS JOIN rating_scope AS rating
                WHERE {perm_predicate}
                    AND rating.green_card_history_status = 'RATED'
                    AND p.technical_role IS TRUE
                    AND {perm_status} IN ('CERTIFIED', 'CERTIFIED-EXPIRED')
            ) AS supporting
            ORDER BY fiscal_year DESC, fiscal_quarter DESC NULLS LAST, source_id, case_id
            LIMIT ?
            """,
            parameters,
        )

    def get_organization_detail(self, organization_id: str) -> OrganizationDetail | None:
        summary = self._query(
            f"""
            WITH explorer AS ({self._employer_sql()})
            SELECT * FROM explorer WHERE organization_id = ?
            """,
            [organization_id],
        )
        if summary.is_empty():
            return None
        legal_entities = self._query(
            """
            SELECT legal_entity_id, legal_name, parent_organization_id, city, state, postal_code,
                organization_type, institution_id, review_status
            FROM legal_entities
            WHERE parent_organization_id = ? OR legal_entity_id = ?
            ORDER BY legal_name
            """,
            [organization_id, organization_id],
        )
        aliases = self._query(
            """
            SELECT alias_raw, source_id, city, state, match_method, match_score, review_status
            FROM entity_aliases
            WHERE parent_organization_id = ? OR legal_entity_id = ?
            ORDER BY occurrence_count DESC, alias_raw
            LIMIT 500
            """,
            [organization_id, organization_id],
        )
        predicate = self._organization_predicate()
        scope_parameters: list[object] = [organization_id, organization_id, organization_id]
        case_statuses = self._query(
            f"""
            SELECT source_id, case_status, count(*) AS case_count,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, case_status FROM lca_cases_resolved
                    WHERE {predicate}
                UNION ALL
                SELECT 'dol_perm' AS source_id, case_status FROM perm_cases_resolved
                    WHERE {predicate}
            )
            GROUP BY source_id, case_status
            ORDER BY source_id, case_count DESC
            """,
            [*scope_parameters, *scope_parameters],
        )
        worksite_states = self._query(
            f"""
            SELECT source_id, worksite_state, count(*) AS case_count,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, worksite_state FROM lca_cases_resolved
                    WHERE {predicate}
                UNION ALL
                SELECT 'dol_perm' AS source_id, worksite_state FROM perm_cases_resolved
                    WHERE {predicate}
            )
            WHERE worksite_state IS NOT NULL
            GROUP BY source_id, worksite_state
            ORDER BY case_count DESC, source_id, worksite_state
            """,
            [*scope_parameters, *scope_parameters],
        )
        wage_summary = self._query(
            f"""
            SELECT source_id, wage_unit, count(*) AS observation_count,
                quantile_cont(wage_from, 0.25) AS wage_p25,
                median(wage_from) AS wage_median,
                quantile_cont(wage_from, 0.75) AS wage_p75,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, wage_unit, wage_from FROM lca_cases_resolved
                    WHERE {predicate}
                UNION ALL
                SELECT 'dol_perm' AS source_id, wage_unit, wage_from FROM perm_cases_resolved
                    WHERE {predicate}
            )
            WHERE wage_from > 0 AND wage_unit IS NOT NULL
            GROUP BY source_id, wage_unit
            ORDER BY source_id, observation_count DESC
            """,
            [*scope_parameters, *scope_parameters],
        )
        provenance_sources: list[str] = []
        for source_id, relation in (
            ("dol_lca", "lca_cases_resolved"),
            ("dol_perm", "perm_cases_resolved"),
            ("uscis_h1b", "h1b_petitions_resolved"),
        ):
            columns = self._relation_columns(relation)
            provenance_sources.append(
                f"""
                SELECT '{source_id}' AS source_id, source_artifact_id, source_file_name,
                    fiscal_year, is_partial_period, ingested_at,
                    {_available_column(columns, "source_url", "NULL::VARCHAR")} AS source_url,
                    {_available_column(columns, "source_sha256", "NULL::VARCHAR")}
                        AS source_sha256,
                    {_available_column(columns, "schema_version", "NULL::VARCHAR")}
                        AS schema_version,
                    {_available_column(columns, "form_version", "NULL::VARCHAR")}
                        AS form_version
                FROM {relation} WHERE {predicate}
                """
            )
        provenance = self._query(
            f"""
            SELECT source_id, source_artifact_id, source_file_name, fiscal_year,
                bool_or(is_partial_period) AS is_partial_period,
                max(ingested_at) AS retrieved_at,
                any_value(source_url) AS official_url,
                any_value(source_sha256) AS sha256,
                any_value(schema_version) AS schema_version,
                any_value(form_version) AS form_version,
                count(*) AS record_count,
                'OBSERVED_GOVERNMENT_RECORD' AS evidence_class
            FROM ({" UNION ALL ".join(provenance_sources)})
            GROUP BY source_id, source_artifact_id, source_file_name, fiscal_year
            ORDER BY source_id, fiscal_year
            """,
            [*scope_parameters, *scope_parameters, *scope_parameters],
        )
        institution_relation = self._institution_sql()
        return OrganizationDetail(
            summary=summary,
            legal_entities=legal_entities,
            aliases=aliases,
            h1b_trends=self._query(
                "SELECT * FROM vw_h1b_trends WHERE organization_id = ? ORDER BY fiscal_year",
                [organization_id],
            ),
            perm_trends=self._query(
                "SELECT * FROM vw_perm_trends WHERE organization_id = ? ORDER BY fiscal_year",
                [organization_id],
            ),
            rating_supporting_cases=self.get_rating_supporting_cases(organization_id),
            relevant_titles=self._query(
                """
                SELECT * FROM vw_relevant_titles WHERE organization_id = ?
                ORDER BY record_count DESC, source_id, job_title_raw LIMIT 500
                """,
                [organization_id],
            ),
            case_statuses=case_statuses,
            worksite_states=worksite_states,
            wage_summary=wage_summary,
            institutions=self._query(
                f"""
                WITH explorer AS ({institution_relation})
                SELECT * FROM explorer
                WHERE organization_id = ? OR legal_entity_id = ? OR parent_organization_id = ?
                ORDER BY overall_sponsorship_score DESC NULLS LAST, official_name
                """,
                scope_parameters,
            ),
            everify_evidence=self._query(
                """
                SELECT * FROM vw_everify_evidence
                WHERE organization_id = ? ORDER BY retrieved_at DESC
                """,
                [organization_id],
            ),
            opt_evidence=self._query(
                """
                SELECT * FROM vw_opt_evidence
                WHERE organization_id = ? ORDER BY report_year DESC, rank
                """,
                [organization_id],
            ),
            policy_evidence=self._query(
                """
                SELECT * FROM vw_policy_evidence
                WHERE organization_id = ?
                    AND human_review_status IN ('REVIEWED_ACCEPTED', 'REVIEWED_NOT_STATED')
                    AND exact_excerpt_verified IS TRUE
                    AND fact_is_current IS TRUE AND valid_to IS NULL
                ORDER BY institution_id, fact_type, retrieved_at DESC
                """,
                [organization_id],
            ),
            provenance=provenance,
        )

    def get_evidence_review(self, *, limit: int = 500) -> EvidenceReviewQueues:
        selected_limit = max(1, min(limit, 5_000))
        return EvidenceReviewQueues(
            entity=self._query("SELECT * FROM vw_entity_review_queue LIMIT ?", [selected_limit]),
            everify=self._query(
                "SELECT * FROM vw_everify_review_queue ORDER BY priority_rank LIMIT ?",
                [selected_limit],
            ),
            opt=self._query(
                """
                SELECT * FROM vw_opt_evidence WHERE review_status = 'NEEDS_REVIEW'
                ORDER BY report_year DESC, rank LIMIT ?
                """,
                [selected_limit],
            ),
            policy=self._query("SELECT * FROM vw_policy_review_queue LIMIT ?", [selected_limit]),
        )

    def get_data_health(self) -> DataHealthSnapshot:
        """Return source selection, artifact provenance, and persisted quality checks."""

        artifacts = _empty()
        for relation in ("vw_source_artifacts", "source_artifacts"):
            if self._relation_exists(relation):
                artifacts = self._query(f"SELECT * FROM {relation} ORDER BY source_id, fiscal_year")
                break
        if artifacts.is_empty():
            artifact_queries: list[str] = []
            for source_id, relation in (
                ("dol_lca", "lca_cases_resolved"),
                ("dol_perm", "perm_cases_resolved"),
                ("uscis_h1b", "h1b_petitions_resolved"),
            ):
                if not self._relation_exists(relation):
                    continue
                columns = self._relation_columns(relation)
                artifact_queries.append(
                    f"""
                    SELECT
                        '{source_id}' AS source_id,
                        source_artifact_id,
                        {_available_column(columns, "source_file_name", "NULL::VARCHAR")}
                            AS source_file_name,
                        fiscal_year,
                        max({_available_column(columns, "fiscal_quarter", "NULL::INTEGER")})
                            AS fiscal_quarter,
                        bool_or(is_partial_period) AS is_partial_period,
                        max({_available_column(columns, "ingested_at", "NULL::VARCHAR")})
                            AS retrieved_at,
                        any_value({_available_column(columns, "source_url", "NULL::VARCHAR")})
                            AS official_url,
                        any_value({_available_column(columns, "source_sha256", "NULL::VARCHAR")})
                            AS sha256,
                        any_value({_available_column(columns, "schema_version", "NULL::VARCHAR")})
                            AS schema_version,
                        any_value({_available_column(columns, "form_version", "NULL::VARCHAR")})
                            AS form_version,
                        NULL::BIGINT AS raw_row_count,
                        count(*) AS normalized_row_count
                    FROM {relation}
                    GROUP BY source_artifact_id, source_file_name, fiscal_year
                    """
                )
            if artifact_queries:
                artifacts = self._query(
                    " UNION ALL ".join(artifact_queries)
                    + " ORDER BY source_id, fiscal_year, fiscal_quarter, source_artifact_id"
                )
        return DataHealthSnapshot(
            source_coverage=self._query("SELECT * FROM vw_data_health ORDER BY source_id"),
            quality_checks=self._query(
                """
                SELECT * FROM vw_quality_checks
                ORDER BY CASE status WHEN 'FAIL' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END,
                    critical DESC, category, check_id
                """
            ),
            source_artifacts=artifacts,
        )

    def export_employers(self, filters: EmployerFilters, file_format: ExportFormat) -> bytes:
        return _serialized(self.list_employers(filters, limit=None), file_format)

    def export_institutions(self, filters: InstitutionFilters, file_format: ExportFormat) -> bytes:
        return _serialized(self.list_institutions(filters, limit=None), file_format)


def get_explorer_service(*, database_path: Path | None = None) -> ExplorerService:
    """Construct the DuckDB service, or an honest unavailable-data fallback."""

    selected = database_path if database_path is not None else load_settings().db_path
    if not selected.is_file():
        return FoundationExplorerService()
    try:
        return DuckDBExplorerService(selected)
    except (duckdb.Error, IncompatibleProductADatabaseError):
        return FoundationExplorerService(
            "The presentation database is incompatible with Product A. Evidence remains UNKNOWN "
            "until a current Product A database is built."
        )
