"""Read-only DuckDB query boundary for the Streamlit explorer."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import duckdb
import polars as pl

from sponsor_intel.config import load_settings

EvidenceStatus = Literal["UNKNOWN", "AVAILABLE"]
ExportFormat = Literal["csv", "parquet"]

EVIDENCE_DISCLAIMER = (
    "This product reports historical and official evidence. It does not provide legal advice "
    "or guarantee sponsorship for a particular person or role."
)


@dataclass(frozen=True, slots=True)
class ExplorerStatus:
    """Current availability and partial-period state for the application."""

    phase: str
    build_id: str
    data_available: bool
    evidence_status: EvidenceStatus
    message: str
    disclaimer: str
    latest_complete_fiscal_year: int | None = None
    current_partial_fiscal_year: int | None = None
    current_partial_quarter: int | None = None


@dataclass(frozen=True, slots=True)
class OverviewMetrics:
    """Coverage metrics shown on the application overview."""

    legal_entity_count: int
    parent_organization_count: int
    institution_count: int
    relevant_lca_count: int
    relevant_certified_perm_count: int
    reviewed_policy_institution_count: int
    unresolved_entity_match_count: int
    source_coverage: pl.DataFrame


@dataclass(frozen=True, slots=True)
class EmployerFilters:
    """Safe, parameterized filters for the employer explorer."""

    search: str = ""
    organization_types: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    everify_statuses: tuple[str, ...] = ()
    opt_statuses: tuple[str, ...] = ()
    cap_exemption_statuses: tuple[str, ...] = ()
    evidence_confidences: tuple[str, ...] = ()
    role_family: str | None = None
    minimum_relevant_lca: int = 0
    minimum_relevant_perm: int = 0
    minimum_initial_approvals: int = 0
    minimum_last_activity_year: int | None = None
    exclude_known_staffing_consulting: bool = False


@dataclass(frozen=True, slots=True)
class InstitutionFilters:
    """Safe, parameterized filters for the institution explorer."""

    search: str = ""
    controls: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    cap_exemption_statuses: tuple[str, ...] = ()
    minimum_total_rd: int = 0
    minimum_computing_rd: int = 0
    minimum_engineering_rd: int = 0
    minimum_relevant_lca: int = 0
    minimum_relevant_perm: int = 0


@dataclass(frozen=True, slots=True)
class OrganizationDetail:
    """Identity, immigration, title, wage, and provenance evidence for one organization."""

    summary: pl.DataFrame
    legal_entities: pl.DataFrame
    aliases: pl.DataFrame
    h1b_trends: pl.DataFrame
    perm_trends: pl.DataFrame
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
    """Reviewable entity, E-Verify, OPT, and policy evidence."""

    entity: pl.DataFrame
    everify: pl.DataFrame
    opt: pl.DataFrame
    policy: pl.DataFrame


@dataclass(frozen=True, slots=True)
class DataHealthSnapshot:
    """Source freshness and publication-gating quality checks."""

    source_coverage: pl.DataFrame
    quality_checks: pl.DataFrame


@runtime_checkable
class ExplorerService(Protocol):
    """Contract that keeps presentation code independent of analytical storage."""

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
    """Fallback that never fabricates evidence when a database has not been built."""

    def get_status(self) -> ExplorerStatus:
        return ExplorerStatus(
            phase="Phase 9",
            build_id="database-unavailable",
            data_available=False,
            evidence_status="UNKNOWN",
            message="No presentation database has been built. Evidence remains UNKNOWN.",
            disclaimer=EVIDENCE_DISCLAIMER,
        )

    def get_overview(self) -> OverviewMetrics:
        return OverviewMetrics(0, 0, 0, 0, 0, 0, 0, _empty())

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

    def get_organization_detail(self, organization_id: str) -> OrganizationDetail | None:
        return None

    def get_evidence_review(self, *, limit: int = 500) -> EvidenceReviewQueues:
        return EvidenceReviewQueues(_empty(), _empty(), _empty(), _empty())

    def get_data_health(self) -> DataHealthSnapshot:
        return DataHealthSnapshot(_empty(), _empty())

    def export_employers(self, filters: EmployerFilters, file_format: ExportFormat) -> bytes:
        return b""

    def export_institutions(self, filters: InstitutionFilters, file_format: ExportFormat) -> bytes:
        return b""


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _serialized(frame: pl.DataFrame, file_format: ExportFormat) -> bytes:
    if file_format == "csv":
        return frame.write_csv().encode("utf-8")
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


class DuckDBExplorerService:
    """Parameterized read-only queries over processed presentation views."""

    def __init__(self, database_path: Path) -> None:
        if not database_path.is_file():
            raise ValueError(f"Presentation database is unavailable: {database_path}")
        self.database_path = database_path
        self._connection = duckdb.connect(str(self.database_path), read_only=True)

    def _query(self, sql: str, parameters: list[object] | None = None) -> pl.DataFrame:
        return self._connection.execute(sql, parameters or []).pl()

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
        message = "Processed government and institution metrics are available."
        if partial is not None:
            message += (
                f" FY{partial} is partial and must not be compared directly with complete years."
            )
        build = self._query("SELECT max(metric_version) AS metric_version FROM employer_metrics")
        build_id = build["metric_version"].item() or "scored_metrics_v1"
        return ExplorerStatus(
            phase="Phase 9",
            build_id=build_id,
            data_available=True,
            evidence_status="AVAILABLE",
            message=message,
            disclaimer=EVIDENCE_DISCLAIMER,
            latest_complete_fiscal_year=health["latest_complete_fiscal_year"],
            current_partial_fiscal_year=partial,
            current_partial_quarter=health["current_partial_quarter"],
        )

    def get_overview(self) -> OverviewMetrics:
        counts = self._query(
            """
            SELECT
                (SELECT count(*) FROM legal_entities) AS legal_entity_count,
                (SELECT count(*) FROM parent_organizations) AS parent_organization_count,
                (SELECT count(*) FROM institutions) AS institution_count,
                (SELECT coalesce(sum(relevant_lca_count), 0) FROM employer_metrics)
                    AS relevant_lca_count,
                (SELECT coalesce(sum(relevant_certified_perm_count), 0) FROM employer_metrics)
                    AS relevant_certified_perm_count,
                (SELECT count(DISTINCT institution_id) FROM vw_policy_evidence
                    WHERE human_review_status = 'REVIEWED_ACCEPTED'
                        AND exact_excerpt_verified IS TRUE
                        AND fact_is_current IS TRUE
                        AND valid_to IS NULL
                        AND starts_with(source_url, 'https://'))
                    AS reviewed_policy_institution_count,
                (SELECT count(*) FROM vw_entity_review_queue) AS unresolved_entity_match_count
            """
        ).to_dicts()[0]
        return OverviewMetrics(
            **counts,
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
                "SELECT coalesce(parent_organization_id, legal_entity_id) "
                "FROM legal_entities WHERE legal_name ILIKE ?"
                ") OR organization_id IN ("
                "SELECT coalesce(parent_organization_id, legal_entity_id) "
                "FROM entity_aliases WHERE alias_raw ILIKE ?"
                "))"
            )
            term = f"%{filters.search.strip()}%"
            parameters.extend([term, term, term])
        for column, values in (
            ("organization_type", filters.organization_types),
            ("state", filters.states),
            ("everify_status", filters.everify_statuses),
            ("known_opt_observation", filters.opt_statuses),
            ("cap_exemption_status", filters.cap_exemption_statuses),
            ("evidence_confidence", filters.evidence_confidences),
        ):
            if values:
                clauses.append(f"{column} IN ({_placeholders(values)})")
                parameters.extend(values)
        if filters.role_family:
            clauses.append(
                "(list_contains(lca_role_families, ?) OR list_contains(perm_role_families, ?))"
            )
            parameters.extend([filters.role_family, filters.role_family])
        if filters.minimum_last_activity_year is not None:
            clauses.append("last_observed_activity_year >= ?")
            parameters.append(filters.minimum_last_activity_year)
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
            SELECT
                organization_id,
                organization_name,
                legal_entity_count,
                organization_type,
                state,
                everify_status,
                everify_lookup_status,
                everify_retrieved_at,
                everify_source_url,
                known_opt_observation,
                opt_report_year,
                opt_reported_count,
                opt_report_rank,
                opt_source_url,
                relevant_lca_count,
                initial_approvals,
                relevant_certified_perm_count,
                last_lca_activity_year,
                last_perm_activity_year,
                cap_exemption_status,
                source_coverage_ratio,
                evidence_confidence,
                stem_opt_readiness_score,
                stem_opt_readiness_status,
                stem_opt_readiness_coverage,
                stem_opt_readiness_confidence,
                stem_opt_readiness_explanation,
                h1b_history_score,
                h1b_history_coverage,
                h1b_history_confidence,
                h1b_history_grade,
                h1b_history_explanation,
                h1b_activity_score,
                green_card_history_score,
                green_card_history_coverage,
                green_card_history_confidence,
                green_card_history_grade,
                green_card_history_explanation,
                immigration_evidence_score,
                immigration_evidence_coverage,
                immigration_evidence_confidence,
                immigration_evidence_grade,
                immigration_evidence_explanation,
                score_version,
                has_partial_period,
                current_partial_fiscal_year,
                metric_version,
                evidence_classes
            FROM vw_employer_explorer
            WHERE {where}
            ORDER BY relevant_lca_count DESC, initial_approvals DESC, organization_name
            {limit_sql}
            """,
            parameters,
        )

    @staticmethod
    def _institution_where(filters: InstitutionFilters) -> tuple[str, list[object]]:
        clauses = [
            "total_rd >= ?",
            "computing_rd >= ?",
            "engineering_rd >= ?",
            "relevant_lca_count >= ?",
            "relevant_certified_perm_count >= ?",
        ]
        parameters: list[object] = [
            filters.minimum_total_rd,
            filters.minimum_computing_rd,
            filters.minimum_engineering_rd,
            filters.minimum_relevant_lca,
            filters.minimum_relevant_perm,
        ]
        if filters.search.strip():
            clauses.append("(official_name ILIKE ? OR parent_system ILIKE ?)")
            term = f"%{filters.search.strip()}%"
            parameters.extend([term, term])
        for column, values in (
            ("control", filters.controls),
            ("state", filters.states),
            ("cap_exemption_status", filters.cap_exemption_statuses),
        ):
            if values:
                clauses.append(f"{column} IN ({_placeholders(values)})")
                parameters.extend(values)
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
            SELECT
                institution_id,
                organization_id,
                official_name,
                parent_system,
                control,
                ipeds_unitid,
                state,
                survey_year AS latest_herd_year,
                total_rd,
                computing_rd,
                engineering_rd,
                federal_rd,
                relevant_lca_count,
                relevant_certified_perm_count,
                initial_approvals,
                everify_status,
                everify_lookup_status,
                everify_retrieved_at,
                known_opt_observation,
                opt_report_year,
                opt_reported_count,
                cap_exemption_status,
                research_staff_h1b_policy,
                research_staff_permanent_residence_policy,
                general_staff_permanent_residence_policy,
                perm_support,
                eb1b_support,
                policy_review_status,
                stem_opt_readiness_score,
                stem_opt_readiness_status,
                stem_opt_readiness_coverage,
                h1b_history_score,
                h1b_history_coverage,
                h1b_history_grade,
                green_card_history_score,
                green_card_history_coverage,
                green_card_history_grade,
                immigration_evidence_score,
                immigration_evidence_coverage,
                immigration_evidence_confidence,
                immigration_evidence_grade,
                research_strength_score,
                research_strength_coverage,
                research_strength_confidence,
                research_strength_grade,
                policy_support_score,
                policy_support_coverage,
                policy_support_confidence,
                policy_support_grade,
                research_pathway_score,
                research_pathway_coverage,
                research_pathway_confidence,
                research_pathway_grade,
                score_version,
                metric_version,
                evidence_classes
            FROM vw_institution_explorer
            WHERE {where}
            ORDER BY total_rd DESC, relevant_lca_count DESC, official_name
            {limit_sql}
            """,
            parameters,
        )

    def employer_facets(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, column in (
            ("organization_types", "organization_type"),
            ("states", "state"),
            ("everify_statuses", "everify_status"),
            ("opt_statuses", "known_opt_observation"),
            ("cap_exemption_statuses", "cap_exemption_status"),
            ("evidence_confidences", "evidence_confidence"),
        ):
            result[name] = self._query(
                f"SELECT DISTINCT {column} AS value FROM vw_employer_explorer "
                f"WHERE {column} IS NOT NULL ORDER BY value"
            )["value"].to_list()
        result["role_families"] = self._query(
            """
            SELECT DISTINCT role_family AS value
            FROM vw_relevant_titles
            WHERE role_family IS NOT NULL
            ORDER BY value
            """
        )["value"].to_list()
        return result

    def institution_facets(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, column in (
            ("controls", "control"),
            ("states", "state"),
            ("cap_exemption_statuses", "cap_exemption_status"),
        ):
            result[name] = self._query(
                f"SELECT DISTINCT {column} AS value FROM vw_institution_explorer "
                f"WHERE {column} IS NOT NULL ORDER BY value"
            )["value"].to_list()
        return result

    def search_organizations(self, search: str, *, limit: int = 50) -> pl.DataFrame:
        term = f"%{search.strip()}%"
        return self._query(
            """
            SELECT organization_id, organization_name, organization_type, state
            FROM vw_employer_explorer
            WHERE organization_name ILIKE ? OR organization_id IN (
                SELECT coalesce(parent_organization_id, legal_entity_id)
                FROM entity_aliases
                WHERE alias_raw ILIKE ?
            )
            ORDER BY relevant_lca_count DESC, organization_name
            LIMIT ?
            """,
            [term, term, max(1, min(limit, 100))],
        )

    def compare_organizations(self, organization_ids: tuple[str, ...]) -> pl.DataFrame:
        """Return one evidence-first comparison row for each of at most five organizations."""

        selected = tuple(dict.fromkeys(value for value in organization_ids if value.strip()))
        if not selected:
            return _empty()
        if len(selected) > 5:
            raise ValueError("Comparison supports at most five organizations")
        result = self._query(
            f"""
            SELECT
                e.organization_id,
                e.organization_name,
                e.organization_type,
                e.state,
                e.everify_status,
                e.known_opt_observation,
                e.opt_report_year,
                e.opt_reported_count,
                e.lca_case_count,
                e.relevant_lca_count,
                e.lca_active_years,
                e.initial_approvals,
                e.initial_denials,
                e.uscis_active_years,
                e.last_lca_activity_year,
                e.last_uscis_activity_year,
                e.perm_case_count,
                e.relevant_certified_perm_count,
                e.perm_active_years,
                e.last_perm_activity_year,
                e.top_perm_technical_title,
                e.top_perm_technical_title_count,
                e.cap_exemption_status,
                e.stem_opt_readiness_score,
                e.stem_opt_readiness_status,
                e.stem_opt_readiness_coverage,
                e.h1b_history_score,
                e.h1b_history_coverage,
                e.h1b_history_grade,
                e.h1b_history_explanation,
                e.green_card_history_score,
                e.green_card_history_coverage,
                e.green_card_history_grade,
                e.green_card_history_explanation,
                e.immigration_evidence_score,
                e.immigration_evidence_coverage,
                e.immigration_evidence_confidence,
                e.immigration_evidence_grade,
                e.immigration_evidence_explanation,
                e.evidence_confidence,
                e.score_version,
                i.official_name AS research_institution,
                i.total_rd,
                i.computing_rd,
                i.engineering_rd,
                i.federal_rd,
                i.research_staff_h1b_policy,
                i.research_staff_permanent_residence_policy,
                i.perm_support,
                i.eb1b_support,
                i.policy_review_status,
                i.research_strength_score,
                i.research_strength_coverage,
                i.research_strength_grade,
                i.research_strength_explanation,
                i.policy_support_score,
                i.policy_support_coverage,
                i.policy_support_grade,
                i.policy_support_explanation,
                i.research_pathway_score,
                i.research_pathway_coverage,
                i.research_pathway_confidence,
                i.research_pathway_grade,
                i.research_pathway_explanation
            FROM vw_employer_explorer AS e
            LEFT JOIN LATERAL (
                SELECT *
                FROM vw_institution_explorer AS candidate
                WHERE candidate.organization_id = e.organization_id
                ORDER BY candidate.total_rd DESC NULLS LAST, candidate.official_name
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

    def get_organization_detail(self, organization_id: str) -> OrganizationDetail | None:
        summary = self._query(
            "SELECT * FROM vw_organization_detail WHERE organization_id = ?", [organization_id]
        )
        if summary.is_empty():
            return None
        legal_entities = self._query(
            """
            SELECT legal_entity_id, legal_name, city, state, postal_code, organization_type,
                institution_id, review_status
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
        case_statuses = self._query(
            """
            SELECT source_id, case_status, count(*) AS case_count,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, case_status FROM lca_cases_resolved
                    WHERE organization_id = ?
                UNION ALL
                SELECT 'dol_perm' AS source_id, case_status FROM perm_cases_resolved
                    WHERE organization_id = ?
            )
            GROUP BY source_id, case_status
            ORDER BY source_id, case_count DESC
            """,
            [organization_id, organization_id],
        )
        worksite_states = self._query(
            """
            SELECT source_id, worksite_state, count(*) AS case_count,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, worksite_state FROM lca_cases_resolved
                    WHERE organization_id = ?
                UNION ALL
                SELECT 'dol_perm' AS source_id, worksite_state FROM perm_cases_resolved
                    WHERE organization_id = ?
            )
            WHERE worksite_state IS NOT NULL
            GROUP BY source_id, worksite_state
            ORDER BY case_count DESC, source_id, worksite_state
            """,
            [organization_id, organization_id],
        )
        wage_summary = self._query(
            """
            SELECT source_id, wage_unit, count(*) AS observation_count,
                quantile_cont(wage_from, 0.25) AS wage_p25,
                median(wage_from) AS wage_median,
                quantile_cont(wage_from, 0.75) AS wage_p75,
                'DERIVED_METRIC' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, wage_unit, wage_from FROM lca_cases_resolved
                    WHERE organization_id = ?
                UNION ALL
                SELECT 'dol_perm' AS source_id, wage_unit, wage_from FROM perm_cases_resolved
                    WHERE organization_id = ?
            )
            WHERE wage_from > 0 AND wage_unit IS NOT NULL
            GROUP BY source_id, wage_unit
            ORDER BY source_id, observation_count DESC
            """,
            [organization_id, organization_id],
        )
        provenance = self._query(
            """
            SELECT source_id, source_artifact_id, source_file_name, fiscal_year,
                bool_or(is_partial_period) AS is_partial_period, max(ingested_at) AS ingested_at,
                count(*) AS record_count, 'OBSERVED_GOVERNMENT_RECORD' AS evidence_class
            FROM (
                SELECT 'dol_lca' AS source_id, source_artifact_id, source_file_name,
                    fiscal_year, is_partial_period, ingested_at
                FROM lca_cases_resolved WHERE organization_id = ?
                UNION ALL
                SELECT 'dol_perm' AS source_id, source_artifact_id, source_file_name,
                    fiscal_year, is_partial_period, ingested_at
                FROM perm_cases_resolved WHERE organization_id = ?
                UNION ALL
                SELECT 'uscis_h1b' AS source_id, source_artifact_id, source_file_name,
                    fiscal_year, is_partial_period, ingested_at
                FROM h1b_petitions_resolved WHERE organization_id = ?
            )
            GROUP BY source_id, source_artifact_id, source_file_name, fiscal_year
            ORDER BY source_id, fiscal_year
            """,
            [organization_id, organization_id, organization_id],
        )
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
            relevant_titles=self._query(
                """
                SELECT * FROM vw_relevant_titles
                WHERE organization_id = ?
                ORDER BY record_count DESC, source_id, job_title_raw
                LIMIT 500
                """,
                [organization_id],
            ),
            case_statuses=case_statuses,
            worksite_states=worksite_states,
            wage_summary=wage_summary,
            institutions=self._query(
                """
                SELECT * FROM vw_institution_explorer
                WHERE organization_id = ?
                ORDER BY total_rd DESC, official_name
                """,
                [organization_id],
            ),
            everify_evidence=self._query(
                """
                SELECT lookup_id, queried_name, enrollment_status, matched_name, matched_dba,
                    state, enrollment_date, termination_date, workforce_size,
                    hiring_site_count, hiring_site_locations, retrieved_at,
                    match_confidence, match_method, review_status, review_reason, source_url
                FROM vw_everify_evidence
                WHERE organization_id = ?
                ORDER BY retrieved_at DESC
                """,
                [organization_id],
            ),
            opt_evidence=self._query(
                """
                SELECT observation_id, report_year, rank, employer_name_raw, program_type,
                    reported_count, is_positive, match_method, match_confidence,
                    review_status, retrieved_at, source_url, coverage_note
                FROM vw_opt_evidence
                WHERE organization_id = ?
                ORDER BY report_year DESC, rank, program_type
                """,
                [organization_id],
            ),
            policy_evidence=self._query(
                """
                SELECT policy_fact_id, institution_id, official_name, document_type,
                    document_title, fact_type, fact_value, qualifier, supporting_excerpt,
                    section_or_page, source_url, retrieved_at, published_or_updated_date,
                    document_is_current, fact_is_current, confidence, human_review_status,
                    reviewer_note, reviewer_id, reviewed_at, valid_from, valid_to,
                    evidence_class
                FROM vw_policy_evidence
                WHERE organization_id = ?
                    AND human_review_status = 'REVIEWED_ACCEPTED'
                    AND exact_excerpt_verified IS TRUE
                    AND fact_is_current IS TRUE
                    AND valid_to IS NULL
                    AND starts_with(source_url, 'https://')
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
                SELECT * FROM vw_opt_evidence
                WHERE review_status = 'NEEDS_REVIEW'
                ORDER BY report_year DESC, rank, program_type
                LIMIT ?
                """,
                [selected_limit],
            ),
            policy=self._query("SELECT * FROM vw_policy_review_queue LIMIT ?", [selected_limit]),
        )

    def get_data_health(self) -> DataHealthSnapshot:
        """Return source-period health and all persisted quality checks."""

        return DataHealthSnapshot(
            source_coverage=self._query("SELECT * FROM vw_data_health ORDER BY source_id"),
            quality_checks=self._query(
                """
                SELECT * FROM vw_quality_checks
                ORDER BY
                    CASE status WHEN 'FAIL' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END,
                    critical DESC,
                    category,
                    check_id
                """
            ),
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
    return DuckDBExplorerService(selected)
