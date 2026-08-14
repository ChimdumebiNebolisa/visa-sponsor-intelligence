"""Materialize processed Parquet as indexed DuckDB presentation tables and views."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb

from sponsor_intel.database.models import DatabaseBuildSummary

REQUIRED_VIEWS = (
    "vw_employer_explorer",
    "vw_institution_explorer",
    "vw_organization_detail",
    "vw_h1b_trends",
    "vw_perm_trends",
    "vw_relevant_titles",
    "vw_everify_evidence",
    "vw_opt_evidence",
    "vw_policy_evidence",
    "vw_entity_review_queue",
    "vw_everify_review_queue",
    "vw_policy_review_queue",
    "vw_data_health",
)


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


class DuckDBBuilder:
    """Build a portable local database used by every Streamlit query."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        database_path: Path = Path("db/immigration.duckdb"),
    ) -> None:
        self.data_root = data_root
        self.database_path = database_path

    def _table_path(self, name: str) -> Path:
        path = self.data_root / "processed" / f"{name}.parquet"
        if not path.is_file():
            raise ValueError(f"Required processed table is unavailable: {path}")
        return path

    def build(self) -> DatabaseBuildSummary:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.database_path.name}-",
            suffix=".tmp",
            dir=self.database_path.parent,
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        temporary_path.unlink()
        tables = (
            "parent_organizations",
            "legal_entities",
            "institutions",
            "lca_cases_resolved",
            "perm_cases_resolved",
            "h1b_petitions_resolved",
            "herd_observations",
            "employer_metrics",
            "institution_metrics",
            "data_health",
        )
        optional_tables = (
            "everify_lookup_priorities",
            "everify_observations",
            "opt_employer_observations",
        )
        aliases_path = self.data_root / "resolved" / "entity_aliases.parquet"
        if not aliases_path.is_file():
            raise ValueError(f"Required resolved table is unavailable: {aliases_path}")
        connection = duckdb.connect(str(temporary_path))
        try:
            for table in tables:
                connection.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                    [_sql_path(self._table_path(table))],
                )
            for table in optional_tables:
                path = self.data_root / "processed" / f"{table}.parquet"
                if path.is_file():
                    connection.execute(
                        f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                        [_sql_path(path)],
                    )
            if "everify_observations" not in {
                row[0] for row in connection.execute("SHOW TABLES").fetchall()
            }:
                connection.execute(
                    """
                    CREATE TABLE everify_observations AS SELECT
                        CAST(NULL AS VARCHAR) AS lookup_id,
                        CAST(NULL AS BIGINT) AS priority_rank,
                        CAST(NULL AS VARCHAR) AS organization_id,
                        CAST(NULL AS VARCHAR) AS queried_name,
                        CAST(NULL AS VARCHAR) AS state,
                        CAST(NULL AS VARCHAR) AS enrollment_status,
                        CAST(NULL AS VARCHAR) AS matched_name,
                        CAST(NULL AS VARCHAR) AS matched_dba,
                        CAST(NULL AS VARCHAR) AS enrollment_date,
                        CAST(NULL AS VARCHAR) AS termination_date,
                        CAST(NULL AS VARCHAR) AS workforce_size,
                        CAST(NULL AS BIGINT) AS hiring_site_count,
                        CAST(NULL AS VARCHAR) AS hiring_site_locations,
                        CAST(NULL AS VARCHAR) AS retrieved_at,
                        CAST(NULL AS DOUBLE) AS match_confidence,
                        CAST(NULL AS VARCHAR) AS match_method,
                        CAST(NULL AS VARCHAR) AS review_status,
                        CAST(NULL AS VARCHAR) AS review_reason,
                        CAST(NULL AS VARCHAR) AS source_url,
                        CAST(NULL AS VARCHAR) AS source_evidence_json
                    WHERE false
                    """
                )
            if "opt_employer_observations" not in {
                row[0] for row in connection.execute("SHOW TABLES").fetchall()
            }:
                connection.execute(
                    """
                    CREATE TABLE opt_employer_observations AS SELECT
                        CAST(NULL AS VARCHAR) AS observation_id,
                        CAST(NULL AS VARCHAR) AS organization_id,
                        CAST(NULL AS INTEGER) AS report_year,
                        CAST(NULL AS INTEGER) AS rank,
                        CAST(NULL AS VARCHAR) AS employer_name_raw,
                        CAST(NULL AS VARCHAR) AS program_type,
                        CAST(NULL AS BIGINT) AS reported_count,
                        CAST(NULL AS BOOLEAN) AS is_positive,
                        CAST(NULL AS VARCHAR) AS source_artifact_id,
                        CAST(NULL AS VARCHAR) AS source_url,
                        CAST(NULL AS VARCHAR) AS retrieved_at,
                        CAST(NULL AS VARCHAR) AS coverage_note,
                        CAST(NULL AS VARCHAR) AS match_method,
                        CAST(NULL AS DOUBLE) AS match_confidence,
                        CAST(NULL AS VARCHAR) AS review_status
                    WHERE false
                    """
                )
            connection.execute(
                "CREATE TABLE entity_aliases AS SELECT * FROM read_parquet(?)",
                [_sql_path(aliases_path)],
            )
            connection.execute(
                "CREATE INDEX idx_employer_metrics_org ON employer_metrics(organization_id)"
            )
            connection.execute(
                "CREATE INDEX idx_institution_metrics_id ON institution_metrics(institution_id)"
            )
            connection.execute("CREATE INDEX idx_lca_org ON lca_cases_resolved(organization_id)")
            connection.execute("CREATE INDEX idx_perm_org ON perm_cases_resolved(organization_id)")
            connection.execute(
                "CREATE INDEX idx_uscis_org ON h1b_petitions_resolved(organization_id)"
            )
            connection.execute(
                "CREATE INDEX idx_everify_org ON everify_observations(organization_id)"
            )
            connection.execute(
                "CREATE INDEX idx_opt_org ON opt_employer_observations(organization_id)"
            )
            connection.execute("CREATE VIEW vw_employer_explorer AS SELECT * FROM employer_metrics")
            connection.execute(
                "CREATE VIEW vw_institution_explorer AS SELECT * FROM institution_metrics"
            )
            connection.execute(
                "CREATE VIEW vw_organization_detail AS SELECT * FROM employer_metrics"
            )
            connection.execute(
                """
                CREATE VIEW vw_h1b_trends AS
                WITH lca AS (
                    SELECT
                        organization_id,
                        fiscal_year,
                        count(*) AS lca_count,
                        count_if(technical_role IS TRUE) AS relevant_lca_count,
                        bool_or(is_partial_period) AS is_partial_period
                    FROM lca_cases_resolved
                    WHERE organization_id IS NOT NULL
                    GROUP BY organization_id, fiscal_year
                ), petitions AS (
                    SELECT
                        organization_id,
                        fiscal_year,
                        sum(initial_approvals) AS initial_approvals,
                        sum(initial_denials) AS initial_denials,
                        sum(continuing_approvals) AS continuing_approvals,
                        sum(continuing_denials) AS continuing_denials,
                        bool_or(is_partial_period) AS is_partial_period
                    FROM h1b_petitions_resolved
                    WHERE organization_id IS NOT NULL
                    GROUP BY organization_id, fiscal_year
                )
                SELECT
                    coalesce(l.organization_id, p.organization_id) AS organization_id,
                    coalesce(l.fiscal_year, p.fiscal_year) AS fiscal_year,
                    coalesce(l.lca_count, 0) AS lca_count,
                    coalesce(l.relevant_lca_count, 0) AS relevant_lca_count,
                    coalesce(p.initial_approvals, 0) AS initial_approvals,
                    coalesce(p.initial_denials, 0) AS initial_denials,
                    coalesce(p.continuing_approvals, 0) AS continuing_approvals,
                    coalesce(p.continuing_denials, 0) AS continuing_denials,
                    coalesce(l.is_partial_period, false) OR coalesce(p.is_partial_period, false)
                        AS is_partial_period,
                    'DERIVED_METRIC' AS evidence_class
                FROM lca AS l
                FULL OUTER JOIN petitions AS p
                    ON l.organization_id = p.organization_id AND l.fiscal_year = p.fiscal_year
                """
            )
            connection.execute(
                """
                CREATE VIEW vw_perm_trends AS
                SELECT
                    organization_id,
                    fiscal_year,
                    count(*) AS perm_count,
                    count_if(
                        technical_role IS TRUE
                        AND upper(coalesce(case_status, '')) LIKE 'CERTIFIED%'
                    )
                        AS relevant_certified_perm_count,
                    count_if(upper(coalesce(case_status, '')) LIKE 'CERTIFIED%') AS certified_count,
                    count_if(upper(coalesce(case_status, '')) LIKE 'DENIED%') AS denied_count,
                    count_if(upper(coalesce(case_status, '')) LIKE 'WITHDRAWN%') AS withdrawn_count,
                    bool_or(is_partial_period) AS is_partial_period,
                    'DERIVED_METRIC' AS evidence_class
                FROM perm_cases_resolved
                WHERE organization_id IS NOT NULL
                GROUP BY organization_id, fiscal_year
                """
            )
            connection.execute(
                """
                CREATE VIEW vw_relevant_titles AS
                SELECT
                    organization_id,
                    'dol_lca' AS source_id,
                    job_title_raw,
                    soc_code,
                    soc_title,
                    role_family,
                    classification_version,
                    count(*) AS record_count,
                    max(fiscal_year) AS last_activity_year,
                    'DERIVED_METRIC' AS evidence_class
                FROM lca_cases_resolved
                WHERE organization_id IS NOT NULL AND technical_role IS TRUE
                GROUP BY ALL
                UNION ALL
                SELECT
                    organization_id,
                    'dol_perm' AS source_id,
                    job_title_raw,
                    soc_code,
                    soc_title,
                    role_family,
                    classification_version,
                    count(*) AS record_count,
                    max(fiscal_year) AS last_activity_year,
                    'DERIVED_METRIC' AS evidence_class
                FROM perm_cases_resolved
                WHERE organization_id IS NOT NULL AND technical_role IS TRUE
                GROUP BY ALL
                """
            )
            connection.execute(
                """
                CREATE VIEW vw_policy_evidence AS
                SELECT
                    CAST(NULL AS VARCHAR) AS institution_id,
                    CAST(NULL AS VARCHAR) AS fact_type,
                    CAST(NULL AS VARCHAR) AS fact_value,
                    CAST(NULL AS VARCHAR) AS source_url,
                    CAST(NULL AS VARCHAR) AS evidence_class
                WHERE false
                """
            )
            connection.execute(
                "CREATE VIEW vw_everify_evidence AS SELECT * FROM everify_observations"
            )
            connection.execute(
                "CREATE VIEW vw_opt_evidence AS SELECT * FROM opt_employer_observations"
            )
            connection.execute(
                """
                CREATE VIEW vw_entity_review_queue AS
                SELECT *
                FROM entity_aliases
                WHERE match_status = 'REVIEW' OR review_status IN ('REVIEW', 'NEEDS_REVIEW')
                """
            )
            connection.execute(
                """
                CREATE VIEW vw_policy_review_queue AS
                SELECT
                    CAST(NULL AS VARCHAR) AS institution_id,
                    CAST(NULL AS VARCHAR) AS review_status,
                    CAST(NULL AS VARCHAR) AS reason
                WHERE false
                """
            )
            connection.execute(
                """
                CREATE VIEW vw_everify_review_queue AS
                SELECT * FROM everify_observations WHERE review_status = 'NEEDS_REVIEW'
                """
            )
            connection.execute("CREATE VIEW vw_data_health AS SELECT * FROM data_health")
            connection.execute("CHECKPOINT")
            employer_row = connection.execute(
                "SELECT count(*) FROM vw_employer_explorer"
            ).fetchone()
            institution_row = connection.execute(
                "SELECT count(*) FROM vw_institution_explorer"
            ).fetchone()
            if employer_row is None or institution_row is None:
                raise RuntimeError("DuckDB count verification did not return a row")
            employer_count = employer_row[0]
            institution_count = institution_row[0]
        finally:
            connection.close()
        try:
            os.replace(temporary_path, self.database_path)
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
        return DatabaseBuildSummary(
            database_path=self.database_path,
            employer_count=int(employer_count),
            institution_count=int(institution_count),
            view_names=REQUIRED_VIEWS,
        )
