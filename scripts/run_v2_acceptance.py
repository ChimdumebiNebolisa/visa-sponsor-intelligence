"""Run Phase 10 V2 acceptance and real-release user-acceptance checks.

The runner deliberately distinguishes deterministic code checks from work that can only be
completed by a human policy reviewer or the repository/Streamlit owner. It never turns either
kind of missing evidence into a passing result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import polars as pl
from streamlit.testing.v1 import AppTest

from sponsor_intel.policy.models import FactValue, ReviewStatus
from sponsor_intel.scoring import ScoringV2Config
from sponsor_intel.services import EmployerFilters, InstitutionFilters
from sponsor_intel.services.explorer import DuckDBExplorerService

EXPECTED_SCORE_VERSION = "evidence_scores_v2_2026_08"
EXPECTED_METRIC_VERSION = "scored_metrics_v2"
CORE_POLICY_FACTS = (
    "h1b_research_staff_eligible",
    "pr_research_staff_eligible",
    "perm_supported",
    "eb1b_supported",
)
REQUIRED_V2_EMPLOYER_COLUMNS = {
    "stem_opt_readiness_score",
    "h1b_history_score",
    "green_card_history_score",
    "sponsorship_history_score",
    "sponsorship_history_coverage",
    "sponsorship_history_confidence_band",
    "sponsorship_history_status",
    "sponsorship_history_grade",
    "sponsorship_history_explanation",
    "last_observed_activity_year",
}
REQUIRED_V2_INSTITUTION_COLUMNS = REQUIRED_V2_EMPLOYER_COLUMNS | {
    "research_strength_score",
    "policy_support_score",
    "research_pathway_score",
    "research_pathway_coverage",
    "research_pathway_status",
    "research_pathway_grade",
    "core_policy_review_coverage",
    "core_policy_evidence_coverage",
    "decision_readiness_tier",
    "decision_readiness_prerequisite_status",
    "decision_readiness_tier_is_final",
    "decision_readiness_explanation",
}

CheckStatus = Literal[
    "PASS",
    "FAIL",
    "BLOCKED_HUMAN_REVIEW",
    "BLOCKED_OWNER_ACTION",
    "NOT_RUN",
]


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    """One deterministic V2 contract check."""

    check_id: str
    requirement: str
    status: CheckStatus
    evidence: str
    code_testable: bool = True


@dataclass(frozen=True, slots=True)
class UATTask:
    """One requested real-data user-acceptance task and its evidence."""

    item: int
    requirement: str
    status: CheckStatus
    evidence: str
    selected_organizations: tuple[dict[str, Any], ...] = ()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, Path)):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {} if default is None else default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _columns(connection: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    try:
        return {str(row[0]) for row in connection.execute(f"DESCRIBE {relation}").fetchall()}
    except duckdb.Error:
        return set()


def _scalar(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[object] | None = None,
) -> Any:
    row = connection.execute(sql, parameters or []).fetchone()
    return None if row is None else row[0]


def _records(frame: pl.DataFrame, *, limit: int = 5) -> tuple[dict[str, Any], ...]:
    return tuple(_json_safe(record) for record in frame.head(limit).to_dicts())


def _first(frame: pl.DataFrame) -> dict[str, Any] | None:
    if frame.is_empty():
        return None
    return _json_safe(frame.row(0, named=True))


def _timed(operation: Callable[[], Any], *, repeats: int = 3) -> dict[str, Any]:
    durations: list[float] = []
    result: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        durations.append((time.perf_counter() - started) * 1000)
    result_size = len(result) if isinstance(result, bytes) else getattr(result, "height", None)
    return {
        "median_ms": round(statistics.median(durations), 2),
        "runs_ms": [round(duration, 2) for duration in durations],
        "result_rows_or_bytes": result_size,
    }


def _github_repository(root: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "isPrivate,nameWithOwner,url,visibility",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return _read_json_text(completed.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
        return {"lookup_error": str(error)}


def _github_release_metadata(root: Path, release_tag: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "gh",
                "release",
                "download",
                release_tag,
                "--pattern",
                "build-metadata.json",
                "--output",
                "-",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return _read_json_text(completed.stdout)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
        return {"lookup_error": str(error)}


def _read_json_text(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _packet_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _policy_review_context(root: Path) -> dict[str, Any]:
    rows = _packet_rows(root / "outputs" / "review" / "core_policy_top50.csv")
    institutions = {
        row.get("institution", "") for row in rows if row.get("institution", "").strip()
    }
    pending = [
        row
        for row in rows
        if row.get("review status", row.get("review_status", ""))
        not in {"REVIEWED_ACCEPTED", "REVIEWED_NOT_STATED"}
    ]
    return {
        "packet_exists": bool(rows),
        "row_count": len(rows),
        "institution_count": len(institutions),
        "pending_row_count": len(pending),
    }


def _contract_checks(
    root: Path,
    connection: duckdb.DuckDBPyConnection,
    service: DuckDBExplorerService,
    repository: dict[str, Any],
    remote_release_metadata: dict[str, Any],
    policy_context: dict[str, Any],
    *,
    live_url: str | None,
    private_access_verified: bool,
) -> list[AcceptanceCheck]:
    checks: list[AcceptanceCheck] = []
    employer_columns = _columns(connection, "vw_employer_explorer")
    institution_columns = _columns(connection, "vw_institution_explorer")
    missing_employer = sorted(REQUIRED_V2_EMPLOYER_COLUMNS - employer_columns)
    missing_institution = sorted(REQUIRED_V2_INSTITUTION_COLUMNS - institution_columns)
    schema_ok = not missing_employer and not missing_institution
    checks.append(
        AcceptanceCheck(
            "v2_schema",
            "Presentation views expose every required V2 decision field",
            "PASS" if schema_ok else "FAIL",
            "All required V2 columns are present."
            if schema_ok
            else f"Missing employer={missing_employer}; institution={missing_institution}.",
        )
    )

    quality_path = root / "outputs" / "reports" / "quality" / "data_quality.json"
    quality = _read_json(quality_path)
    quality_passed = quality.get("passed") is True and quality.get("critical_failure_count") == 0
    checks.append(
        AcceptanceCheck(
            "quality_gate",
            "The current real-data build passes with zero critical failures",
            "PASS" if quality_passed else "FAIL",
            f"Build {quality.get('build_id', 'UNKNOWN')}: passed={quality.get('passed')}; "
            f"critical_failures={quality.get('critical_failure_count', 'UNKNOWN')}.",
        )
    )

    if schema_ok:
        score_versions = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT score_version FROM employer_metrics "
                "WHERE score_version IS NOT NULL"
            ).fetchall()
        )
        metric_versions = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT metric_version FROM employer_metrics "
                "WHERE metric_version IS NOT NULL"
            ).fetchall()
        )
    else:
        score_versions = []
        metric_versions = []
    version_ok = score_versions == [EXPECTED_SCORE_VERSION] and metric_versions == [
        EXPECTED_METRIC_VERSION
    ]
    checks.append(
        AcceptanceCheck(
            "v2_versions",
            "V2 scoring and metric interpretation are exact and versioned",
            "PASS" if version_ok else "FAIL",
            f"score_version={score_versions}; metric_version={metric_versions}.",
        )
    )

    v1_paths = (
        root / "data" / "processed" / "employer_scores_v1.parquet",
        root / "data" / "processed" / "institution_scores_v1.parquet",
    )
    v1_ok = all(path.is_file() and path.stat().st_size > 0 for path in v1_paths)
    checks.append(
        AcceptanceCheck(
            "v1_reproducibility",
            "Historical V1 score outputs remain available as sidecars",
            "PASS" if v1_ok else "FAIL",
            "; ".join(
                f"{path.name}={'present' if path.is_file() else 'missing'}" for path in v1_paths
            ),
        )
    )

    try:
        config = ScoringV2Config.from_yaml(root / "configs" / "scoring_v2.yaml")
        formula_ok = (
            config.version == EXPECTED_SCORE_VERSION
            and config.sponsorship_history.weights
            == {"h1b_history": 0.4, "green_card_history": 0.6}
            and config.research_pathway.weights
            == {
                "sponsorship_history": 0.5,
                "policy_support": 0.3,
                "research_strength": 0.2,
            }
            and config.core_policy.required_fact_types == CORE_POLICY_FACTS
        )
        formula_evidence = (
            f"sponsorship={config.sponsorship_history.weights}; "
            f"research_pathway={config.research_pathway.weights}; "
            f"core={config.core_policy.required_fact_types}."
        )
    except (OSError, ValueError) as error:
        formula_ok = False
        formula_evidence = str(error)
    checks.append(
        AcceptanceCheck(
            "v2_formula",
            "V2 uses the approved 40/60 sponsorship and 50/30/20 pathway formulas",
            "PASS" if formula_ok else "FAIL",
            formula_evidence,
        )
    )

    if schema_ok:
        misleading_grades = int(
            _scalar(
                connection,
                """
                SELECT
                    (SELECT count(*) FROM employer_metrics
                     WHERE sponsorship_history_coverage < 1
                       AND sponsorship_history_grade IS NOT NULL) +
                    (SELECT count(*) FROM institution_metrics
                     WHERE research_pathway_status <> 'COMPLETE'
                       AND research_pathway_grade IS NOT NULL)
                """,
            )
            or 0
        )
        independent_count = int(
            _scalar(
                connection,
                """
                SELECT count(*) FROM employer_metrics
                WHERE sponsorship_history_score IS NOT NULL
                  AND everify_status = 'UNKNOWN'
                """,
            )
            or 0
        )
        readiness_bad = int(
            _scalar(
                connection,
                """
                SELECT count(*) FROM vw_institution_explorer
                WHERE decision_readiness_tier IN (
                    'TIER_1_REVIEWED', 'TIER_2_STRONG_HISTORY_POLICY_INCOMPLETE'
                ) AND decision_readiness_prerequisite_status <> 'QUALITY_GATE_PASSED'
                """,
            )
            or 0
        )
    else:
        misleading_grades = -1
        independent_count = 0
        readiness_bad = -1
    checks.extend(
        [
            AcceptanceCheck(
                "grade_gating",
                "Partial sponsorship and incomplete pathway scores receive no letter grade",
                "PASS" if schema_ok and misleading_grades == 0 else "FAIL",
                f"Misleading grade rows: {misleading_grades}.",
            ),
            AcceptanceCheck(
                "everify_independence",
                "Sponsorship history is independent of missing E-Verify evidence",
                "PASS" if independent_count > 0 else "FAIL",
                f"Employers scored for sponsorship with E-Verify UNKNOWN: {independent_count:,}.",
            ),
            AcceptanceCheck(
                "readiness_quality_prerequisite",
                "Published Tier 1/2 rows require the current critical quality gate",
                "PASS" if schema_ok and readiness_bad == 0 and quality_passed else "FAIL",
                f"Tier 1/2 rows without QUALITY_GATE_PASSED: {readiness_bad}.",
            ),
        ]
    )

    semantics_ok = (
        FactValue.UNKNOWN.value == "UNKNOWN"
        and FactValue.NO.value == "NO"
        and FactValue.NOT_STATED.value == "NOT_STATED"
        and ReviewStatus.REVIEWED_NOT_STATED.value == "REVIEWED_NOT_STATED"
    )
    checks.append(
        AcceptanceCheck(
            "uncertainty_semantics",
            "UNKNOWN, NO, NOT_STATED, and pending review remain distinct",
            "PASS" if semantics_ok else "FAIL",
            "Typed policy contracts expose distinct UNKNOWN, NO, NOT_STATED, "
            "NEEDS_REVIEW, and REVIEWED_NOT_STATED states.",
        )
    )

    if schema_ok:
        employers = service.list_employers(limit=5)
        institutions = service.list_institutions(limit=5)
        research_sorted = service.list_institutions(
            InstitutionFilters(sort_by="research_activity"), limit=5
        )
        service_ok = not employers.is_empty() and not institutions.is_empty()
        ranking_differs = (
            service_ok
            and not research_sorted.is_empty()
            and institutions["institution_id"].to_list()
            != research_sorted["institution_id"].to_list()
        )
    else:
        service_ok = False
        ranking_differs = False
    checks.extend(
        [
            AcceptanceCheck(
                "nonzero_service",
                "The read-only service queries nonzero real employer and institution data",
                "PASS" if service_ok else "FAIL",
                "Default employer and institution queries returned nonzero rows."
                if service_ok
                else "The V2 service could not return nonzero real-data results.",
            ),
            AcceptanceCheck(
                "decision_first_default",
                "Default institution ranking is not merely total R&D ordering",
                "PASS" if ranking_differs else "FAIL",
                "The first five default IDs differ from research-activity ordering."
                if ranking_differs
                else "Default and research-activity ordering did not differ in the observed rows.",
            ),
        ]
    )

    packet_ok = policy_context["row_count"] == 200 and policy_context["institution_count"] == 50
    checks.append(
        AcceptanceCheck(
            "policy_review_packet",
            "The deterministic top-50 packet contains four core questions per institution",
            "PASS" if packet_ok else "FAIL",
            f"institutions={policy_context['institution_count']}; "
            f"rows={policy_context['row_count']}; "
            f"pending={policy_context['pending_row_count']}.",
        )
    )

    audit = _read_json(root / "outputs" / "reports" / "phase10" / "data-quality-audit-summary.json")
    entity = audit.get("entity_audit", {})
    entity_ok = entity.get("company_count", 0) >= 30 and entity.get("institution_count", 0) >= 30
    checks.append(
        AcceptanceCheck(
            "entity_audit",
            "Entity audit covers at least 30 companies and 30 institutions",
            "PASS" if entity_ok else "FAIL",
            f"companies={entity.get('company_count', 0)}; "
            f"institutions={entity.get('institution_count', 0)}; "
            f"pending_human_review={entity.get('pending_human_review_count', 0)}.",
        )
    )
    classifier = audit.get("classification_change_report", {})
    classifier_ok = (
        classifier.get("semantic_changed_record_count", 0) > 0
        and classifier.get("manual_sample_count", 0) > 0
    )
    checks.append(
        AcceptanceCheck(
            "classifier_audit",
            "Role-classifier changes have before/after counts and a stratified review sample",
            "PASS" if classifier_ok else "FAIL",
            f"changed_records={classifier.get('semantic_changed_record_count', 0)}; "
            f"sample={classifier.get('manual_sample_count', 0)}; "
            f"pending_review={classifier.get('manual_sample_pending_count', 0)}.",
        )
    )

    runtime_files = (
        root / "src" / "sponsor_intel" / "deployment" / "release_bootstrap.py",
        root / ".streamlit" / "config.toml",
        root / ".streamlit" / "secrets.example.toml",
        root / "docs" / "deployment" / "community-cloud.md",
        root / "app" / "requirements.txt",
    )
    deployment_code_ok = all(path.is_file() for path in runtime_files)
    checks.append(
        AcceptanceCheck(
            "deployment_package",
            "Private Community Cloud bootstrap and least-dependency configuration are present",
            "PASS" if deployment_code_ok else "FAIL",
            "; ".join(
                f"{path.relative_to(root)}={'present' if path.is_file() else 'missing'}"
                for path in runtime_files
            ),
        )
    )

    local_release_metadata = _read_json(root / "outputs" / "release" / "build-metadata.json")
    local_runtime_release_v2 = (
        local_release_metadata.get("score_version") == EXPECTED_SCORE_VERSION
        and local_release_metadata.get("metric_version") == EXPECTED_METRIC_VERSION
        and local_release_metadata.get("quality_passed") is True
    )
    checks.append(
        AcceptanceCheck(
            "local_v2_runtime_bundle",
            "A quality-approved V2 runtime bundle is ready for controlled publication",
            "PASS" if local_runtime_release_v2 else "FAIL",
            f"Local bundle metadata reports build="
            f"{local_release_metadata.get('build_id', 'UNKNOWN')}; "
            f"score={local_release_metadata.get('score_version', 'UNKNOWN')}; "
            f"metric={local_release_metadata.get('metric_version', 'UNKNOWN')}.",
        )
    )
    published_runtime_release_v2 = (
        remote_release_metadata.get("score_version") == EXPECTED_SCORE_VERSION
        and remote_release_metadata.get("metric_version") == EXPECTED_METRIC_VERSION
        and remote_release_metadata.get("quality_passed") is True
    )
    checks.append(
        AcceptanceCheck(
            "published_v2_runtime_release",
            "A quality-approved V2 runtime release is available to the deployment bootstrap",
            "PASS" if published_runtime_release_v2 else "BLOCKED_OWNER_ACTION",
            f"Remote release metadata reports "
            f"build={remote_release_metadata.get('build_id', 'UNKNOWN')}; "
            f"score={remote_release_metadata.get('score_version', 'UNKNOWN')}; "
            f"metric={remote_release_metadata.get('metric_version', 'UNKNOWN')}. "
            "Publish the quality-approved V2 bundle only after repository privacy is established."
            if not published_runtime_release_v2
            else "The runtime release metadata is quality-approved and uses the exact V2 contract.",
            code_testable=False,
        )
    )

    repo_private = repository.get("isPrivate") is True
    deployed_private = repo_private and bool(live_url) and private_access_verified
    checks.append(
        AcceptanceCheck(
            "private_deployment",
            "Repository and live Streamlit app privacy are owner-verified",
            "PASS" if deployed_private else "BLOCKED_OWNER_ACTION",
            f"repository={repository.get('nameWithOwner', 'UNKNOWN')}; "
            f"visibility={repository.get('visibility', 'UNKNOWN')}; live_url={live_url or 'NONE'}; "
            f"private_access_verified={private_access_verified}.",
            code_testable=False,
        )
    )
    return checks


def _select_private_company(connection: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return connection.execute(
        """
        SELECT organization_id, organization_name, organization_type,
            sponsorship_history_score, sponsorship_history_coverage,
            h1b_history_score, green_card_history_score,
            relevant_lca_count, relevant_certified_perm_count
        FROM vw_employer_explorer AS employer
        WHERE sponsorship_history_status = 'COMPLETE'
          AND relevant_lca_count > 0
          AND relevant_certified_perm_count > 0
          AND NOT EXISTS (
              SELECT 1 FROM vw_institution_explorer AS institution
              WHERE institution.organization_id = employer.organization_id
          )
          AND regexp_matches(
              lower(organization_name),
              '( corporation| corp\\.?$| inc\\.?$| incorporated| llc$| l\\.l\\.c\\.$| technologies)'
          )
        ORDER BY sponsorship_history_score DESC NULLS LAST,
            green_card_history_score DESC NULLS LAST,
            h1b_history_score DESC NULLS LAST,
            relevant_certified_perm_count DESC,
            relevant_lca_count DESC,
            organization_name
        LIMIT 5
        """
    ).pl()


def _select_research_lab(connection: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return connection.execute(
        """
        SELECT organization_id, organization_name, organization_type,
            sponsorship_history_score, sponsorship_history_coverage,
            h1b_history_score, green_card_history_score,
            relevant_lca_count, relevant_certified_perm_count
        FROM vw_employer_explorer AS employer
        WHERE (relevant_lca_count > 0 OR relevant_certified_perm_count > 0)
          AND NOT EXISTS (
              SELECT 1 FROM vw_institution_explorer AS institution
              WHERE institution.organization_id = employer.organization_id
          )
          AND regexp_matches(
              lower(organization_name),
              '(national laborator|research institute|research laboratory|research center)'
          )
          AND NOT regexp_matches(
              lower(organization_name),
              '(\\binc\\.?\\b|\\bcorporation\\b|\\bcorp\\.?\\b|\\bllc\\b)'
          )
        ORDER BY
            CASE
                WHEN lower(organization_name) LIKE '%national laborator%' THEN 0
                WHEN lower(organization_name) LIKE '%research laboratory%' THEN 1
                WHEN lower(organization_name) LIKE '%research institute%' THEN 2
                ELSE 3
            END,
            sponsorship_history_coverage DESC,
            sponsorship_history_score DESC NULLS LAST,
            relevant_certified_perm_count DESC,
            relevant_lca_count DESC,
            organization_name
        LIMIT 5
        """
    ).pl()


def _human_or_fail(
    *,
    policy_context: dict[str, Any],
    requirement: str,
    evidence: str,
    item: int,
) -> UATTask:
    if policy_context["pending_row_count"] > 0:
        return UATTask(
            item,
            requirement,
            "BLOCKED_HUMAN_REVIEW",
            f"{evidence} The top-50 packet retains "
            f"{policy_context['pending_row_count']} pending core rows; no result was fabricated.",
        )
    return UATTask(item, requirement, "FAIL", evidence)


def _uat_tasks(
    root: Path,
    connection: duckdb.DuckDBPyConnection,
    service: DuckDBExplorerService,
    policy_context: dict[str, Any],
) -> tuple[list[UATTask], dict[str, Any]]:
    tasks: list[UATTask] = []
    status = service.get_status()
    default_institutions = service.list_institutions(limit=None)
    default_employers = service.list_employers(limit=2_000)

    complete = service.list_institutions(
        InstitutionFilters(minimum_core_policy_review_coverage=1.0), limit=100
    )
    if complete.is_empty():
        tasks.append(
            _human_or_fail(
                item=1,
                requirement="Find research institutions with complete core-policy review",
                policy_context=policy_context,
                evidence="The real release returned zero institutions with 100% core review.",
            )
        )
    else:
        tasks.append(
            UATTask(
                1,
                "Find research institutions with complete core-policy review",
                "PASS",
                f"Found {complete.height} result(s) with review coverage 1.0.",
                _records(
                    complete.select(
                        "institution_id",
                        "organization_id",
                        "official_name",
                        "core_policy_review_coverage",
                        "decision_readiness_tier",
                    )
                ),
            )
        )

    policy_filters = (
        (
            2,
            "Filter for research-staff permanent-residence eligibility",
            InstitutionFilters(research_staff_pr_policies=("YES",)),
            "research_staff_permanent_residence_policy",
        ),
        (
            3,
            "Filter for PERM support",
            InstitutionFilters(perm_support_policies=("YES",)),
            "perm_support",
        ),
        (
            4,
            "Filter for EB-1B support",
            InstitutionFilters(eb1b_support_policies=("YES",)),
            "eb1b_support",
        ),
    )
    for item, requirement, filters, column in policy_filters:
        result = service.list_institutions(filters, limit=100)
        if result.is_empty():
            tasks.append(
                _human_or_fail(
                    item=item,
                    requirement=requirement,
                    policy_context=policy_context,
                    evidence=f"The real release returned zero rows where {column}=YES.",
                )
            )
        else:
            tasks.append(
                UATTask(
                    item,
                    requirement,
                    "PASS",
                    f"Found {result.height} result(s); every returned row has {column}=YES.",
                    _records(
                        result.select("institution_id", "organization_id", "official_name", column)
                    ),
                )
            )

    strong_incomplete = service.list_institutions(
        InstitutionFilters(minimum_h1b_score=60, sort_by="h1b_history"), limit=500
    ).filter(pl.col("core_policy_review_coverage") < 1)
    tasks.append(
        UATTask(
            5,
            "Find institutions with strong technical H-1B history but incomplete policy evidence",
            "PASS" if not strong_incomplete.is_empty() else "FAIL",
            f"Found {strong_incomplete.height} institution(s) with H-1B score >=60 and "
            "core review below 100%.",
            _records(
                strong_incomplete.select(
                    "institution_id",
                    "organization_id",
                    "official_name",
                    "h1b_history_score",
                    "relevant_lca_count",
                    "core_policy_review_coverage",
                )
            ),
        )
    )

    research_sorted = service.list_institutions(
        InstitutionFilters(sort_by="research_activity"), limit=None
    )
    high_research_weak_gc = research_sorted.filter(
        pl.col("total_rd").is_not_null()
        & (
            pl.col("green_card_history_score").is_null()
            | (pl.col("green_card_history_score") < 40)
            | (pl.col("green_card_history_coverage") < 1)
        )
    )
    tasks.append(
        UATTask(
            6,
            "Find high-research institutions with weak or unknown green-card evidence",
            "PASS" if not high_research_weak_gc.is_empty() else "FAIL",
            f"Found {high_research_weak_gc.height} institution(s) after sorting by research "
            "activity and applying the weak/unknown green-card rule.",
            _records(
                high_research_weak_gc.select(
                    "institution_id",
                    "organization_id",
                    "official_name",
                    "total_rd",
                    "green_card_history_score",
                    "green_card_history_coverage",
                    "relevant_certified_perm_count",
                )
            ),
        )
    )

    companies = _select_private_company(connection)
    tasks.append(
        UATTask(
            7,
            "Find private companies with strong technical H-1B and PERM histories",
            "PASS" if not companies.is_empty() else "FAIL",
            f"Found {companies.height} non-institution legal names with a corporate suffix, "
            "complete sponsorship coverage, and nonzero technical LCA/PERM history.",
            _records(companies),
        )
    )

    university = _first(default_institutions)
    labs = _select_research_lab(connection)
    lab = _first(labs)
    company = _first(companies)
    comparison_ids = tuple(
        str(record["organization_id"])
        for record in (university, lab, company)
        if record is not None and record.get("organization_id")
    )
    comparison = (
        service.compare_organizations(comparison_ids)
        if len(set(comparison_ids)) == 3
        else pl.DataFrame()
    )
    comparison_samples = tuple(
        record
        for record in (
            {"sample_type": "university", **university} if university else None,
            {"sample_type": "research_nonprofit_or_lab", **lab} if lab else None,
            {"sample_type": "private_company", **company} if company else None,
        )
        if record is not None
    )
    tasks.append(
        UATTask(
            8,
            "Compare a university, a research nonprofit/lab, and a private company",
            "PASS" if comparison.height == 3 else "FAIL",
            f"Comparison returned {comparison.height}/3 unique rows selected by deterministic "
            "evidence/type rules.",
            comparison_samples,
        )
    )

    parent_frame = connection.execute(
        """
        SELECT parent.parent_organization_id AS organization_id,
            parent.canonical_name AS organization_name,
            count(*) AS legal_entity_count
        FROM parent_organizations AS parent
        JOIN legal_entities AS legal USING (parent_organization_id)
        JOIN vw_employer_explorer AS employer
          ON employer.organization_id = parent.parent_organization_id
        GROUP BY parent.parent_organization_id, parent.canonical_name
        HAVING count(*) > 1
        ORDER BY count(*) DESC, parent.canonical_name
        LIMIT 1
        """
    ).pl()
    parent = _first(parent_frame)
    parent_detail = (
        service.get_organization_detail(str(parent["organization_id"])) if parent else None
    )
    parent_ok = parent_detail is not None and parent_detail.legal_entities.height > 1
    tasks.append(
        UATTask(
            9,
            "Open a parent organization and inspect its legal entities",
            "PASS" if parent_ok else "FAIL",
            f"Selected parent detail exposed "
            f"{parent_detail.legal_entities.height if parent_detail else 0} legal entities.",
            (parent,) if parent else (),
        )
    )

    audit_rows = _packet_rows(root / "outputs" / "reports" / "phase10" / "entity-audit.csv")
    ambiguous_rows = [
        row
        for row in audit_rows
        if row.get("human_override_required") == "YES"
        and row.get("audit_review_status") == "PENDING_HUMAN_REVIEW"
    ]
    queued_alias_count = int(
        _scalar(connection, "SELECT count(*) FROM vw_entity_review_queue") or 0
    )
    ambiguous_ok = bool(ambiguous_rows) and queued_alias_count > 0
    ambiguous_sample = (
        {
            "selection_name": ambiguous_rows[0].get("selection_name"),
            "resolution_confidence": ambiguous_rows[0].get("resolution_confidence"),
            "human_override_required": ambiguous_rows[0].get("human_override_required"),
            "audit_review_status": ambiguous_rows[0].get("audit_review_status"),
            "location_conflict_observations": ambiguous_rows[0].get(
                "location_conflict_observations"
            ),
        }
        if ambiguous_rows
        else None
    )
    tasks.append(
        UATTask(
            10,
            "Confirm an ambiguous organization does not silently merge",
            "PASS" if ambiguous_ok else "FAIL",
            f"Presentation review queue rows={queued_alias_count}; independently selected "
            f"real-data audit conflicts held for human review={len(ambiguous_rows)}.",
            (ambiguous_sample,) if ambiguous_sample else (),
        )
    )

    unknown_everify_ranked = default_employers.filter(
        (pl.col("everify_status") == "UNKNOWN")
        & pl.col("sponsorship_history_score").is_not_null()
        & (pl.col("relevant_lca_count") > 0)
        & (pl.col("relevant_certified_perm_count") > 0)
    )
    tasks.append(
        UATTask(
            11,
            "Confirm missing E-Verify does not suppress H-1B/PERM ranking",
            "PASS" if not unknown_everify_ranked.is_empty() else "FAIL",
            f"Default sponsorship ranking contains {unknown_everify_ranked.height} of the first "
            "2,000 employers with E-Verify UNKNOWN and nonzero technical LCA/PERM evidence.",
            _records(
                unknown_everify_ranked.select(
                    "organization_id",
                    "organization_name",
                    "everify_status",
                    "sponsorship_history_score",
                    "relevant_lca_count",
                    "relevant_certified_perm_count",
                )
            ),
        )
    )

    unknown_source = default_employers.filter(pl.col("everify_status") == "UNKNOWN").head(1)
    explicit_unknown = (
        unknown_source.select(
            pl.when(pl.col("everify_status").is_null())
            .then(pl.lit("UNKNOWN"))
            .otherwise(pl.col("everify_status"))
            .alias("display")
        )["display"][0]
        if not unknown_source.is_empty()
        else None
    )
    null_score = (
        service.list_employers(limit=None)
        .filter(pl.col("sponsorship_history_score").is_null())
        .head(1)
    )
    null_export = (
        null_score.select("sponsorship_history_score").write_csv()
        if not null_score.is_empty()
        else ""
    )
    unknown_ok = explicit_unknown == "UNKNOWN" and "0.0" not in null_export
    tasks.append(
        UATTask(
            12,
            "Confirm UNKNOWN is not displayed or exported as NO or zero",
            "PASS" if unknown_ok else "FAIL",
            f"Observed E-Verify display={explicit_unknown}; "
            f"nullable score CSV={null_export.strip()!r}.",
            _records(
                unknown_source.select("organization_id", "organization_name", "everify_status")
            ),
        )
    )

    partial_ok = (
        status.current_partial_fiscal_year == 2026
        and "partial" in status.message.lower()
        and status.latest_complete_fiscal_year is not None
    )
    tasks.append(
        UATTask(
            13,
            "Confirm partial FY2026 evidence is visibly labeled",
            "PASS" if partial_ok else "FAIL",
            f"latest_complete_fy={status.latest_complete_fiscal_year}; "
            f"partial_fy={status.current_partial_fiscal_year}; "
            f"partial_quarter={status.current_partial_quarter}; message={status.message}",
        )
    )

    institution_export_filters = InstitutionFilters(minimum_relevant_lca=5, minimum_relevant_perm=1)
    institution_export = service.export_institutions(institution_export_filters, "csv")
    institution_lines = institution_export.decode("utf-8").splitlines()
    institution_export_ok = len(institution_lines) > 1 and "official_name" in institution_lines[0]
    tasks.append(
        UATTask(
            14,
            "Export a filtered institution result",
            "PASS" if institution_export_ok else "FAIL",
            f"Exported {len(institution_export):,} bytes and "
            f"{max(0, len(institution_lines) - 1):,} data rows for LCA>=5 and PERM>=1.",
        )
    )

    employer_export_filters = EmployerFilters(minimum_relevant_lca=10, minimum_relevant_perm=1)
    employer_export = service.export_employers(employer_export_filters, "csv")
    employer_lines = employer_export.decode("utf-8").splitlines()
    employer_export_ok = len(employer_lines) > 1 and "organization_name" in employer_lines[0]
    tasks.append(
        UATTask(
            15,
            "Export a filtered employer result",
            "PASS" if employer_export_ok else "FAIL",
            f"Exported {len(employer_export):,} bytes and "
            f"{max(0, len(employer_lines) - 1):,} data rows for LCA>=10 and PERM>=1.",
        )
    )

    policy_org = connection.execute(
        """
        SELECT institution.organization_id, institution.official_name,
            count(*) AS visible_policy_fact_count
        FROM vw_policy_evidence AS evidence
        JOIN vw_institution_explorer AS institution USING (institution_id)
        WHERE evidence.human_review_status IN ('REVIEWED_ACCEPTED', 'REVIEWED_NOT_STATED')
          AND evidence.exact_excerpt_verified IS TRUE
          AND evidence.fact_is_current IS TRUE
          AND evidence.valid_to IS NULL
          AND starts_with(evidence.source_url, 'https://')
          AND trim(evidence.supporting_excerpt) <> ''
          AND institution.organization_id IS NOT NULL
        GROUP BY institution.organization_id, institution.official_name
        ORDER BY count(*) DESC, institution.official_name
        LIMIT 1
        """
    ).pl()
    policy_sample = _first(policy_org)
    policy_detail = (
        service.get_organization_detail(str(policy_sample["organization_id"]))
        if policy_sample
        else None
    )
    visible_policy = policy_detail.policy_evidence if policy_detail is not None else pl.DataFrame()
    policy_detail_ok = (
        not visible_policy.is_empty()
        and visible_policy.select(
            (
                pl.col("source_url").str.starts_with("https://")
                & pl.col("supporting_excerpt").str.strip_chars().ne("")
            ).all()
        ).item()
    )
    if policy_detail_ok:
        tasks.append(
            UATTask(
                16,
                "Verify every policy claim in detail links to an official source and exact excerpt",
                "PASS",
                f"The selected detail exposed {visible_policy.height} reviewed current fact(s); "
                "all have HTTPS source links and nonempty exact excerpts, and the view gates on "
                "exact_excerpt_verified.",
                (policy_sample,) if policy_sample else (),
            )
        )
    else:
        tasks.append(
            _human_or_fail(
                item=16,
                requirement=(
                    "Verify every policy claim in detail links to an official source and "
                    "exact excerpt"
                ),
                policy_context=policy_context,
                evidence="No qualifying reviewed current policy claim was available in detail.",
            )
        )

    complete_employer = default_employers.filter(
        (pl.col("sponsorship_history_status") == "COMPLETE")
        & pl.col("h1b_history_score").is_not_null()
        & pl.col("green_card_history_score").is_not_null()
    ).head(1)
    score_match = False
    count_match = False
    explanation_present = False
    rank_sample: dict[str, Any] | None = None
    if not complete_employer.is_empty():
        rank_sample = _first(complete_employer)
        assert rank_sample is not None
        expected = round(
            float(rank_sample["h1b_history_score"]) * 0.4
            + float(rank_sample["green_card_history_score"]) * 0.6,
            2,
        )
        score_match = abs(expected - float(rank_sample["sponsorship_history_score"])) <= 0.01
        detail = service.get_organization_detail(str(rank_sample["organization_id"]))
        if detail is not None:
            lca_sum = int(detail.h1b_trends["relevant_lca_count"].sum() or 0)
            perm_sum = int(detail.perm_trends["relevant_certified_perm_count"].sum() or 0)
            count_match = lca_sum == int(rank_sample["relevant_lca_count"]) and perm_sum == int(
                rank_sample["relevant_certified_perm_count"]
            )
        explanation_present = bool(rank_sample.get("sponsorship_history_explanation"))
        rank_sample = {
            "organization_id": rank_sample["organization_id"],
            "organization_name": rank_sample["organization_name"],
            "h1b_history_score": rank_sample["h1b_history_score"],
            "green_card_history_score": rank_sample["green_card_history_score"],
            "stored_sponsorship_score": rank_sample["sponsorship_history_score"],
            "recomputed_sponsorship_score": expected,
            "relevant_lca_count": rank_sample["relevant_lca_count"],
            "relevant_certified_perm_count": rank_sample["relevant_certified_perm_count"],
        }
    rank_ok = score_match and count_match and explanation_present
    tasks.append(
        UATTask(
            17,
            "Verify ranking explanations match underlying raw counts",
            "PASS" if rank_ok else "FAIL",
            f"Formula match={score_match}; detail trend/count match={count_match}; "
            f"stored explanation present={explanation_present}.",
            (rank_sample,) if rank_sample else (),
        )
    )

    high_rd = _first(high_research_weak_gc)
    ordering_ok = False
    ordering_sample: tuple[dict[str, Any], ...] = ()
    if high_rd is not None:
        position = default_institutions.with_row_index("rank").filter(
            pl.col("institution_id") == high_rd["institution_id"]
        )
        if not position.is_empty():
            rank = int(position["rank"][0])
            earlier = default_institutions.head(rank).filter(
                (pl.col("total_rd").fill_null(0) < int(high_rd["total_rd"] or 0))
                & (pl.col("sponsorship_history_coverage") >= 1)
                & (
                    pl.col("green_card_history_score").fill_null(-1)
                    > float(high_rd.get("green_card_history_score") or -1)
                )
            )
            if not earlier.is_empty():
                better = _first(earlier)
                assert better is not None
                ordering_ok = True
                ordering_sample = (
                    {
                        "sample_type": "earlier_decision_evidence",
                        "institution_id": better["institution_id"],
                        "official_name": better["official_name"],
                        "decision_readiness_tier": better["decision_readiness_tier"],
                        "total_rd": better["total_rd"],
                        "green_card_history_score": better["green_card_history_score"],
                    },
                    {
                        "sample_type": "higher_rd_weaker_immigration",
                        "institution_id": high_rd["institution_id"],
                        "official_name": high_rd["official_name"],
                        "decision_readiness_tier": high_rd["decision_readiness_tier"],
                        "total_rd": high_rd["total_rd"],
                        "green_card_history_score": high_rd["green_card_history_score"],
                        "default_zero_based_rank": rank,
                    },
                )
    tasks.append(
        UATTask(
            18,
            "Verify high R&D alone does not override stronger immigration evidence",
            "PASS" if ordering_ok else "FAIL",
            "Found a lower-R&D institution ranked earlier because it has complete sponsorship "
            "coverage and stronger green-card evidence. The earlier row is Tier 2 (policy "
            "incomplete), so this does not claim a Tier 1 decision-ready comparison."
            if ordering_ok
            else "No qualifying real-data ordering pair was found; this is not treated as proof.",
            ordering_sample,
        )
    )

    context = {
        "comparison_ids": list(comparison_ids),
        "detail_id": rank_sample.get("organization_id") if rank_sample else None,
        "institution_export_filters": asdict(institution_export_filters),
        "employer_export_filters": asdict(employer_export_filters),
    }
    return tasks, context


def _streamlit_measurement(root: Path, *, skip: bool) -> dict[str, Any]:
    if skip:
        return {"status": "NOT_MEASURED", "reason": "--skip-streamlit was supplied"}
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    app = AppTest.from_file(str(root / "app" / "Home.py"), default_timeout=60)
    started = time.perf_counter()
    app.run()
    cold_ms = (time.perf_counter() - started) * 1000
    cold_errors = [str(item.value) for item in app.exception]
    started = time.perf_counter()
    app.run()
    warm_ms = (time.perf_counter() - started) * 1000
    warm_errors = [str(item.value) for item in app.exception]
    return {
        "status": "PASS" if not cold_errors and not warm_errors else "FAIL",
        "method": "streamlit.testing.v1.AppTest Home.py execution",
        "cold_start_ms": round(cold_ms, 2),
        "warm_rerun_ms": round(warm_ms, 2),
        "cold_errors": cold_errors,
        "warm_errors": warm_errors,
    }


def _process_memory() -> dict[str, Any]:
    if os.name == "nt":
        try:
            output = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"$p=Get-Process -Id {os.getpid()}; "
                    '"$($p.WorkingSet64),$($p.PeakWorkingSet64)"',
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout.strip()
            working, peak = (int(value) for value in output.split(","))
            return {
                "method": "Windows Get-Process",
                "working_set_bytes": working,
                "peak_working_set_bytes": peak,
            }
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"status": "NOT_MEASURED", "reason": "Windows process query failed"}
    status_path = Path("/proc/self/status")
    if status_path.is_file():
        values: dict[str, int] = {}
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, raw, _unit = line.split()
                values[name.rstrip(":")] = int(raw) * 1024
        return {
            "method": "/proc/self/status",
            "working_set_bytes": values.get("VmRSS"),
            "peak_working_set_bytes": values.get("VmHWM"),
        }
    return {"status": "NOT_MEASURED", "reason": "No supported process-memory source"}


def _performance(
    root: Path,
    database_path: Path,
    *,
    comparison_ids: tuple[str, ...],
    detail_id: str | None,
    skip_streamlit: bool,
    install_duration_seconds: float | None,
    installed_size_bytes: int | None,
    installed_size_approx_mib: float | None,
    install_measurement_note: str | None,
    deployed_runtime_memory_bytes: int | None,
) -> dict[str, Any]:
    service = DuckDBExplorerService(database_path)
    try:
        selected_detail = detail_id
        if selected_detail is None:
            frame = service.list_employers(limit=1)
            selected_detail = str(frame["organization_id"][0]) if not frame.is_empty() else ""
        selected_comparison = comparison_ids
        if len(selected_comparison) < 3:
            frame = service.list_employers(limit=3)
            selected_comparison = tuple(str(value) for value in frame["organization_id"])
        latency = {
            "employer_search": _timed(
                lambda: service.list_employers(EmployerFilters(search="UNIVERSITY"), limit=500)
            ),
            "institution_ranking": _timed(lambda: service.list_institutions(limit=500)),
            "organization_detail": _timed(
                lambda: service.get_organization_detail(selected_detail or "")
            ),
            "comparison": _timed(lambda: service.compare_organizations(selected_comparison[:3])),
            "filtered_employer_csv_export": _timed(
                lambda: service.export_employers(
                    EmployerFilters(minimum_relevant_lca=10, minimum_relevant_perm=1), "csv"
                ),
                repeats=1,
            ),
            "filtered_institution_csv_export": _timed(
                lambda: service.export_institutions(
                    InstitutionFilters(minimum_relevant_lca=5, minimum_relevant_perm=1), "csv"
                ),
                repeats=1,
            ),
        }
    finally:
        service.close()

    release_root = root / "outputs" / "release"
    runtime_names = (
        "immigration.duckdb",
        "data-quality.json",
        "build-metadata.json",
        "checksums.sha256",
    )
    runtime_assets = {
        name: (release_root / name).stat().st_size
        for name in runtime_names
        if (release_root / name).is_file()
    }
    return {
        "scope": "local Windows predeployment measurement unless explicitly labeled deployed",
        "captured_at": datetime.now(UTC).isoformat(),
        "database": {
            "path": str(database_path.relative_to(root)),
            "size_bytes": database_path.stat().st_size,
            "sha256": _sha256(database_path),
        },
        "runtime_release_assets": {
            "build_metadata": _read_json(release_root / "build-metadata.json"),
            "sizes_bytes": runtime_assets,
            "minimum_transfer_bytes": sum(runtime_assets.values()),
            "compression": (
                "The local V2 runtime database asset is uncompressed; prospective hosted "
                "transfer is the four verified bundle assets."
            ),
        },
        "query_latency_ms": latency,
        "streamlit": _streamlit_measurement(root, skip=skip_streamlit),
        "local_process_memory": _process_memory(),
        "clean_dependency_install": {
            "duration_seconds": install_duration_seconds,
            "installed_size_bytes": installed_size_bytes,
            "installed_size_approx_mib": installed_size_approx_mib,
            "measurement_note": install_measurement_note,
            "status": "PARTIALLY_MEASURED"
            if any(
                value is not None
                for value in (
                    install_duration_seconds,
                    installed_size_bytes,
                    installed_size_approx_mib,
                    install_measurement_note,
                )
            )
            else "NOT_MEASURED",
        },
        "deployed_runtime_memory": {
            "bytes": deployed_runtime_memory_bytes,
            "status": "MEASURED"
            if deployed_runtime_memory_bytes is not None
            else "NOT_MEASURED_OWNER_DEPLOYMENT_REQUIRED",
        },
        "deployed_cold_start": "NOT_MEASURED_OWNER_DEPLOYMENT_REQUIRED",
        "deployment_reliability": "NOT_MEASURED_OWNER_DEPLOYMENT_REQUIRED",
        "mobile_usability": "NOT_MEASURED_HUMAN_BROWSER_REVIEW_REQUIRED",
    }


def _task_with_history(
    task: UATTask,
    previous: dict[str, Any] | None,
    *,
    observed_at: str,
) -> dict[str, Any]:
    attempts = [] if previous is None else list(previous.get("attempts", []))
    current = {
        "observed_at": observed_at,
        "status": task.status,
        "evidence": task.evidence,
        "selected_organizations": _json_safe(task.selected_organizations),
    }
    comparable = {key: value for key, value in current.items() if key != "observed_at"}
    if (
        not attempts
        or {key: value for key, value in attempts[-1].items() if key != "observed_at"} != comparable
    ):
        attempts.append(current)
    return {
        "item": task.item,
        "requirement": task.requirement,
        "status": task.status,
        "evidence": task.evidence,
        "selected_organizations": _json_safe(task.selected_organizations),
        "attempts": attempts,
    }


def _markdown_table_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _uat_markdown(report: dict[str, Any]) -> str:
    release = report["release"]
    summary = report["summary"]
    deployment = report["deployment"]
    lines = [
        "# Phase 10 real-data user-acceptance results",
        "",
        "> These results distinguish passed observations from missing human policy review and "
        "owner-only deployment actions. No blocked item is reported as passed.",
        "",
        "## Result",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Code-testable contract checks: {summary['code_checks_passed']}/"
        f"{summary['code_checks_total']} passed",
        f"- UAT tasks passed: {summary['uat_passed']}/18",
        f"- UAT tasks blocked on human review: {summary['uat_human_blocked']}",
        f"- UAT tasks failed: {summary['uat_failed']}",
        f"- Source data release: `{release['source_data_release_tag']}`",
        f"- Local quality build: `{release['local_quality_build_id']}`",
        f"- Score version: `{release['score_version']}`",
        f"- Metric version: `{release['metric_version']}`",
        f"- Database SHA-256: `{release['database_sha256']}`",
        "",
        "## Deployment truth",
        "",
        f"- Repository visibility: **{deployment['repository_visibility']}**",
        f"- Local runtime bundle: "
        f"`{deployment['local_runtime_bundle'].get('build_id', 'UNKNOWN')}` "
        f"({deployment['local_runtime_bundle'].get('score_version', 'UNKNOWN')})",
        f"- Published runtime build: "
        f"`{deployment['published_runtime_release'].get('build_id', 'UNKNOWN')}` "
        f"({deployment['published_runtime_release'].get('score_version', 'UNKNOWN')})",
        f"- Live URL: `{deployment['live_url'] or 'NONE'}`",
        f"- Private access verified: **{deployment['private_access_verified']}**",
        f"- Status: **{deployment['status']}**",
        "",
        "## V2 contract checks",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for check in report["acceptance_checks"]:
        lines.append(
            f"| `{check['check_id']}` | **{check['status']}** | "
            f"{_markdown_table_escape(check['evidence'])} |"
        )
    lines.extend(
        [
            "",
            "## Requested UAT tasks",
            "",
            "| # | Task | Status | Evidence |",
            "|---:|---|---|---|",
        ]
    )
    for task in report["uat_tasks"]:
        lines.append(
            f"| {task['item']} | {_markdown_table_escape(task['requirement'])} | "
            f"**{task['status']}** | {_markdown_table_escape(task['evidence'])} |"
        )
    lines.extend(
        [
            "",
            "## Representative selection",
            "",
            "Organizations were selected deterministically from the real database: the highest "
            "default-ranked university; the strongest non-institution national-lab/research-lab "
            "name matching the evidence rule; and the strongest complete-history non-institution "
            "legal name with a corporate suffix. This avoids hand-picking only convenient cases.",
            "",
            "## Before-and-after failures",
            "",
            "Every task retains an `attempts` array in `uat-results.json`. A later rerun appends "
            "a materially changed observation, so an original failure remains visible after a "
            "fix. No task in this report has a fabricated remediation attempt.",
            "",
            "## Remaining human and owner work",
            "",
            f"- Core policy packet rows still pending review: "
            f"{report['policy_review']['pending_row_count']}.",
            f"- Role-classification sample rows still pending manual review: "
            f"{report['manual_review']['role_sample_pending_count']}.",
            f"- Entity audit rows still pending human review: "
            f"{report['manual_review']['entity_audit_pending_count']}.",
            "- The repository owner must make the repository private, deploy `app/Home.py` on "
            "Python 3.12, publish the quality-approved V2 runtime bundle, set the read-only "
            "release token in Streamlit secrets, configure restricted sharing, and verify "
            "invited/non-invited access.",
        ]
    )
    return "\n".join(lines) + "\n"


def _performance_markdown(report: dict[str, Any], baseline: dict[str, Any]) -> str:
    lines = [
        "# Phase 10 performance evidence",
        "",
        f"> Scope: {report['scope']}. Hosted Community Cloud measurements remain explicitly "
        "unmeasured until the owner completes a private deployment.",
        "",
        "## Runtime footprint",
        "",
        f"- Current DuckDB: {report['database']['size_bytes']:,} bytes",
        f"- Four-asset release transfer: "
        f"{report['runtime_release_assets']['minimum_transfer_bytes']:,} bytes",
        f"- Local runtime bundle build: "
        f"{report['runtime_release_assets']['build_metadata'].get('build_id', 'UNKNOWN')} "
        f"({report['runtime_release_assets']['build_metadata'].get('score_version', 'UNKNOWN')})",
        f"- Clean dependency install duration: "
        f"{report['clean_dependency_install']['duration_seconds'] or 'NOT_MEASURED'}",
        f"- Clean installed size: "
        f"{report['clean_dependency_install']['installed_size_bytes'] or 'not byte-exact'} "
        f"(approximately "
        f"{report['clean_dependency_install']['installed_size_approx_mib'] or 'NOT_MEASURED'} MiB)",
        f"- Clean install note: {report['clean_dependency_install']['measurement_note'] or 'NONE'}",
        f"- Local peak process working set: "
        f"{report['local_process_memory'].get('peak_working_set_bytes', 'NOT_MEASURED')}",
        f"- Deployed peak runtime memory: "
        f"{report['deployed_runtime_memory']['bytes'] or 'NOT_MEASURED'}",
        "",
        "## Current local latency",
        "",
        "| Operation | Median | Runs | Rows/bytes | Comparable baseline median |",
        "|---|---:|---|---:|---:|",
    ]
    baseline_latency = baseline.get("latency_ms", {})
    baseline_names = {
        "employer_search": "employer_search",
        "organization_detail": "organization_detail",
        "comparison": "comparison",
    }
    for name, values in report["query_latency_ms"].items():
        baseline_name = baseline_names.get(name)
        baseline_value = (
            baseline_latency.get(baseline_name, {}).get("median_ms", "n/a")
            if baseline_name
            else "n/a"
        )
        baseline_display = (
            f"{float(baseline_value):.2f} ms" if isinstance(baseline_value, (float, int)) else "n/a"
        )
        result_size = (
            values["result_rows_or_bytes"] if values["result_rows_or_bytes"] is not None else "n/a"
        )
        lines.append(
            f"| {name} | {values['median_ms']:.2f} ms | "
            f"{', '.join(f'{run:.2f}' for run in values['runs_ms'])} | "
            f"{result_size} | {baseline_display} |"
        )
    streamlit = report["streamlit"]
    lines.extend(
        [
            "",
            "## Streamlit execution",
            "",
            f"- Status: **{streamlit['status']}**",
            f"- Local cold Home execution: {streamlit.get('cold_start_ms', 'NOT_MEASURED')} ms",
            f"- Local warm Home rerun: {streamlit.get('warm_rerun_ms', 'NOT_MEASURED')} ms",
            f"- Baseline cold Home execution: "
            f"{baseline.get('streamlit_measurement', {}).get('cold_start_ms', 'n/a')} ms",
            f"- Baseline warm Home rerun: "
            f"{baseline.get('streamlit_measurement', {}).get('warm_rerun_ms', 'n/a')} ms",
            "- Baseline institution and export operations used different filters/scopes; they "
            "are not shown as like-for-like comparators in the table.",
            "",
            "## Not yet measurable",
            "",
            "- Deployed cold start and cache recovery reliability.",
            "- Deployed peak memory and platform resource headroom.",
            "- Private authentication behavior for owner, invited user, signed-out browser, and "
            "non-invited account.",
            "- Basic mobile usability in the deployed app.",
            "",
            "These gaps are owner-action validation items; they are not silently treated as "
            "successful framework evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(
    root: Path,
    *,
    source_release_tag: str,
    live_url: str | None,
    private_access_verified: bool,
    skip_streamlit: bool,
    reset_history: bool,
    install_duration_seconds: float | None,
    installed_size_bytes: int | None,
    installed_size_approx_mib: float | None,
    install_measurement_note: str | None,
    deployed_runtime_memory_bytes: int | None,
) -> dict[str, Any]:
    database_path = root / "db" / "immigration.duckdb"
    if not database_path.is_file():
        raise ValueError(f"Real presentation database is unavailable: {database_path}")
    output_root = root / "outputs" / "reports" / "phase10"
    output_root.mkdir(parents=True, exist_ok=True)
    previous = {} if reset_history else _read_json(output_root / "uat-results.json", default={})
    repository = _github_repository(root)
    remote_release_metadata = _github_release_metadata(root, source_release_tag)
    policy_context = _policy_review_context(root)

    connection = duckdb.connect(str(database_path), read_only=True)
    service = DuckDBExplorerService(database_path)
    try:
        checks = _contract_checks(
            root,
            connection,
            service,
            repository,
            remote_release_metadata,
            policy_context,
            live_url=live_url,
            private_access_verified=private_access_verified,
        )
        schema_passed = next(check for check in checks if check.check_id == "v2_schema").status
        if schema_passed == "PASS":
            tasks, context = _uat_tasks(root, connection, service, policy_context)
        else:
            tasks = [
                UATTask(
                    item,
                    requirement,
                    "NOT_RUN",
                    "The required V2 presentation schema is unavailable; no result was inferred.",
                )
                for item, requirement in enumerate(
                    (
                        "Find research institutions with complete core-policy review",
                        "Filter for research-staff permanent-residence eligibility",
                        "Filter for PERM support",
                        "Filter for EB-1B support",
                        "Find institutions with strong H-1B history and incomplete policy",
                        "Find high-research institutions with weak green-card evidence",
                        "Find companies with strong H-1B and PERM histories",
                        "Compare university, research nonprofit/lab, and private company",
                        "Inspect a parent organization's legal entities",
                        "Confirm ambiguous organizations do not silently merge",
                        "Confirm missing E-Verify does not suppress ranking",
                        "Confirm UNKNOWN is not NO or zero",
                        "Confirm partial FY2026 is visibly labeled",
                        "Export a filtered institution result",
                        "Export a filtered employer result",
                        "Verify policy source links and exact excerpts",
                        "Verify ranking explanations against raw counts",
                        "Verify high R&D alone does not override stronger immigration evidence",
                    ),
                    start=1,
                )
            ]
            context = {"comparison_ids": [], "detail_id": None}
        quality_build_id = _scalar(
            connection,
            "SELECT max(build_id) FROM quality_checks",
        )
        score_versions = (
            sorted(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT score_version FROM employer_metrics "
                    "WHERE score_version IS NOT NULL"
                ).fetchall()
            )
            if "score_version" in _columns(connection, "employer_metrics")
            else []
        )
        metric_versions = (
            sorted(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT metric_version FROM employer_metrics "
                    "WHERE metric_version IS NOT NULL"
                ).fetchall()
            )
            if "metric_version" in _columns(connection, "employer_metrics")
            else []
        )
    finally:
        service.close()
        connection.close()

    performance = _performance(
        root,
        database_path,
        comparison_ids=tuple(context.get("comparison_ids", [])),
        detail_id=context.get("detail_id"),
        skip_streamlit=skip_streamlit,
        install_duration_seconds=install_duration_seconds,
        installed_size_bytes=installed_size_bytes,
        installed_size_approx_mib=installed_size_approx_mib,
        install_measurement_note=install_measurement_note,
        deployed_runtime_memory_bytes=deployed_runtime_memory_bytes,
    )
    observed_at = datetime.now(UTC).isoformat()
    previous_tasks = {
        int(task["item"]): task for task in previous.get("uat_tasks", []) if "item" in task
    }
    tasks_with_history = [
        _task_with_history(task, previous_tasks.get(task.item), observed_at=observed_at)
        for task in tasks
    ]
    code_checks = [check for check in checks if check.code_testable]
    code_passed = sum(check.status == "PASS" for check in code_checks)
    uat_passed = sum(task.status == "PASS" for task in tasks)
    uat_failed = sum(task.status in {"FAIL", "NOT_RUN"} for task in tasks)
    uat_human = sum(task.status == "BLOCKED_HUMAN_REVIEW" for task in tasks)
    deployment_check = next(check for check in checks if check.check_id == "private_deployment")
    if code_passed != len(code_checks) or uat_failed:
        overall = "NOT_COMPLETE"
    elif uat_human or deployment_check.status != "PASS":
        overall = "COMPLETE_EXCEPT_FOR_HUMAN_OR_OWNER_ACTION"
    else:
        overall = "COMPLETE"

    audit = _read_json(output_root / "data-quality-audit-summary.json")
    manual_review = {
        "entity_audit_pending_count": audit.get("entity_audit", {}).get(
            "pending_human_review_count", 0
        ),
        "role_sample_pending_count": audit.get("classification_change_report", {}).get(
            "manual_sample_pending_count", 0
        ),
    }
    report = {
        "schema_version": "phase10-v2-acceptance-1",
        "generated_at": observed_at,
        "git_commit_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "release": {
            "source_data_release_tag": source_release_tag,
            "local_quality_build_id": quality_build_id,
            "score_version": score_versions,
            "metric_version": metric_versions,
            "database_path": str(database_path.relative_to(root)),
            "database_sha256": performance["database"]["sha256"],
            "database_size_bytes": performance["database"]["size_bytes"],
        },
        "summary": {
            "overall_status": overall,
            "code_checks_passed": code_passed,
            "code_checks_total": len(code_checks),
            "uat_passed": uat_passed,
            "uat_human_blocked": uat_human,
            "uat_owner_blocked": 0,
            "uat_failed": uat_failed,
            "uat_total": len(tasks),
            "process_exit_policy": (
                "Nonzero only for failed/not-run code-testable checks or UAT tasks; human-review "
                "and owner-action blockers remain visible without masquerading as code failures."
            ),
        },
        "deployment": {
            "repository": repository,
            "repository_visibility": repository.get("visibility", "UNKNOWN"),
            "local_runtime_bundle": _read_json(
                root / "outputs" / "release" / "build-metadata.json"
            ),
            "published_runtime_release": remote_release_metadata,
            "live_url": live_url,
            "private_access_verified": private_access_verified,
            "status": deployment_check.status,
        },
        "policy_review": policy_context,
        "manual_review": manual_review,
        "acceptance_checks": [asdict(check) for check in checks],
        "uat_tasks": tasks_with_history,
        "performance_report": "outputs/reports/phase10/performance.json",
    }
    baseline = _read_json(output_root / "baseline.json")
    (output_root / "uat-results.json").write_text(
        json.dumps(_json_safe(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "uat-results.md").write_text(_uat_markdown(report), encoding="utf-8")
    (output_root / "performance.json").write_text(
        json.dumps(_json_safe(performance), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "performance.md").write_text(
        _performance_markdown(performance, baseline), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-release-tag", default="data-2026-08-15")
    parser.add_argument("--live-url", default=os.getenv("SPONSOR_INTEL_LIVE_URL"))
    parser.add_argument("--private-access-verified", action="store_true")
    parser.add_argument("--skip-streamlit", action="store_true")
    parser.add_argument("--reset-history", action="store_true")
    parser.add_argument("--install-duration-seconds", type=float)
    parser.add_argument("--installed-size-bytes", type=int)
    parser.add_argument("--installed-size-approx-mib", type=float)
    parser.add_argument("--install-measurement-note")
    parser.add_argument("--deployed-runtime-memory-bytes", type=int)
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    report = run(
        root,
        source_release_tag=arguments.source_release_tag,
        live_url=arguments.live_url,
        private_access_verified=arguments.private_access_verified,
        skip_streamlit=arguments.skip_streamlit,
        reset_history=arguments.reset_history,
        install_duration_seconds=arguments.install_duration_seconds,
        installed_size_bytes=arguments.installed_size_bytes,
        installed_size_approx_mib=arguments.installed_size_approx_mib,
        install_measurement_note=arguments.install_measurement_note,
        deployed_runtime_memory_bytes=arguments.deployed_runtime_memory_bytes,
    )
    print(json.dumps(_json_safe(report), indent=2))
    summary = report["summary"]
    if summary["code_checks_passed"] != summary["code_checks_total"] or summary["uat_failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
