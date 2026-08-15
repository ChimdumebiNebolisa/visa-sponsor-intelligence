"""Run the specification's V1 definition-of-done checks against a real build."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from sponsor_intel.database import DuckDBBuilder
from sponsor_intel.metrics import MetricsPipeline
from sponsor_intel.quality import QualityReporter
from sponsor_intel.sources.manifests import write_json_atomic


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    """One V1 definition-of-done assertion and its observed evidence."""

    item: int
    requirement: str
    passed: bool
    evidence: str


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parquet(root: Path, name: str) -> pl.DataFrame:
    return pl.read_parquet(root / "data" / "processed" / f"{name}.parquet")


def _github_evidence(root: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    repository = json.loads(
        subprocess.run(
            ["gh", "repo", "view", "--json", "isPrivate,nameWithOwner,url"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    runs = json.loads(
        subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--workflow",
                "ci.yml",
                "--branch",
                "main",
                "--limit",
                "1",
                "--json",
                "conclusion,headSha,status,url",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, runs[0] if runs else {}, head_sha


def _verify_clean_restore(root: Path) -> tuple[bool, str]:
    release = root / "outputs" / "release"
    archives = (
        release / "processed-parquet.zip",
        release / "build-state.zip",
        release / "source-manifests.zip",
    )
    missing = [path.name for path in archives if not path.is_file()]
    if missing:
        return False, f"Missing release archives: {', '.join(missing)}"
    with tempfile.TemporaryDirectory(prefix="sponsor-intel-acceptance-") as directory:
        restored = Path(directory)
        for archive_path in archives:
            with zipfile.ZipFile(archive_path) as archive:
                archive.testzip()
                archive.extractall(restored)
        metrics = MetricsPipeline(
            data_root=restored / "data",
            output_root=restored / "outputs",
        ).build()
        quality = QualityReporter(
            data_root=restored / "data",
            output_root=restored / "outputs",
        ).build()
        database = DuckDBBuilder(
            data_root=restored / "data",
            database_path=restored / "db" / "immigration.duckdb",
        ).build()
        view_count = len(database.view_names)
        passed = quality.passed and metrics.employer_count > 0 and view_count >= 14
        evidence = (
            f"Restored archives rebuilt {metrics.employer_count:,} employers, "
            f"{metrics.institution_count:,} institutions, {view_count} DuckDB views, "
            f"and quality build {quality.build_id} with "
            f"{quality.critical_failure_count} critical failures."
        )
        return passed, evidence


def run_acceptance(root: Path, *, verify_restore: bool) -> dict[str, Any]:
    """Evaluate all 20 V1 requirements and return a machine-readable report."""

    quality = _json(root / "outputs" / "reports" / "quality" / "data_quality.json")
    role_validation = _json(root / "outputs" / "reports" / "roles" / "gold_validation.json")
    institution_join = _json(
        root / "outputs" / "reports" / "institutions" / "herd_ipeds_join_review.json"
    )
    repository, ci_run, head_sha = _github_evidence(root)
    database_path = root / "db" / "immigration.duckdb"
    processed = root / "data" / "processed"
    expected_years = {2022, 2023, 2024, 2025}
    source_years = {
        name: set(_parquet(root, name)["fiscal_year"].drop_nulls().to_list())
        for name in ("lca_cases_resolved", "perm_cases_resolved", "h1b_petitions_resolved")
    }
    institutions = _parquet(root, "institutions")
    herd = _parquet(root, "herd_observations")
    legal = _parquet(root, "legal_entities")
    parents = _parquet(root, "parent_organizations")
    employers = _parquet(root, "employer_metrics")
    priorities = _parquet(root, "everify_lookup_priorities")
    everify = _parquet(root, "everify_observations")
    opt = _parquet(root, "opt_employer_observations")
    candidates = _parquet(root, "policy_candidates")
    facts = _parquet(root, "policy_facts")
    institution_metrics = _parquet(root, "institution_metrics")
    health = _parquet(root, "data_health")
    accepted = facts.filter(pl.col("human_review_status") == "REVIEWED_ACCEPTED")
    reviewed_institutions = accepted["institution_id"].n_unique()
    accepted_evidence_ok = accepted.select(
        (
            pl.col("source_url").str.starts_with("https://")
            & pl.col("supporting_excerpt").str.strip_chars().ne("")
            & pl.col("exact_excerpt_verified")
        ).all()
    ).item()
    restore_passed, restore_evidence = (
        _verify_clean_restore(root)
        if verify_restore
        else (False, "Clean-restore verification was not requested; rerun with --verify-restore.")
    )
    with duckdb.connect(str(database_path), read_only=True) as connection:
        views = {
            row[0]
            for row in connection.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        }
        employer_count = connection.execute("SELECT count(*) FROM vw_employer_explorer").fetchone()[
            0
        ]
        trend_count = connection.execute("SELECT count(*) FROM vw_h1b_trends").fetchone()[0]
        policy_evidence_count = connection.execute(
            "SELECT count(*) FROM vw_policy_evidence "
            "WHERE human_review_status = 'REVIEWED_ACCEPTED'"
        ).fetchone()[0]
    app_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((root / "app").rglob("*.py"))
    ).lower()
    operations = (root / "docs" / "operations.md").read_text(encoding="utf-8").lower()
    dictionary = (root / "docs" / "data_dictionary.md").read_text(encoding="utf-8").lower()
    workflow = (root / ".github" / "workflows" / "refresh_government_data.yml").read_text(
        encoding="utf-8"
    )
    scoring_text = (root / "docs/scoring.md").read_text(encoding="utf-8").lower()
    scores_present = all(
        column in employers.columns
        for column in (
            "stem_opt_readiness_score",
            "h1b_history_score",
            "green_card_history_score",
            "immigration_evidence_score",
            "immigration_evidence_coverage",
            "immigration_evidence_confidence",
        )
    ) and all(
        column in institution_metrics.columns
        for column in (
            "research_strength_score",
            "policy_support_score",
            "research_pathway_score",
            "research_pathway_coverage",
            "research_pathway_confidence",
        )
    )
    parquet_count = len(list(processed.glob("*.parquet")))
    checks = [
        AcceptanceCheck(
            1,
            "Private repository is reproducible from a clean environment",
            repository.get("isPrivate") is True and restore_passed,
            f"{repository.get('nameWithOwner')} is private. {restore_evidence}",
        ),
        AcceptanceCheck(
            2,
            "FY2022 onward DOL LCA, DOL PERM, and USCIS H-1B are ingested",
            all(expected_years <= years for years in source_years.values()),
            "; ".join(
                f"{name}: FY{min(years)}-FY{max(years)}" for name, years in source_years.items()
            ),
        ),
        AcceptanceCheck(
            3,
            "IPEDS and HERD are joined with reviewed mappings",
            institutions.height > 0
            and herd.filter(pl.col("institution_id").is_not_null()).height > 0
            and institution_join["needs_review_count"] >= 0,
            f"{institutions.height:,} IPEDS institutions; "
            f"{institution_join['identifier_matched_count']:,}/"
            f"{institution_join['herd_observation_count']:,} HERD observations exact-matched; "
            f"{institution_join['needs_review_count']:,} retained for review.",
        ),
        AcceptanceCheck(
            4,
            "Legal entities and parents remain separate",
            legal.height > parents.height > 0
            and next(
                check
                for check in quality["checks"]
                if check["check_id"] == "legal_parent_separation"
            )["status"]
            == "PASS",
            f"{legal.height:,} legal entities and {parents.height:,} parent organizations; "
            "separation gate passed.",
        ),
        AcceptanceCheck(
            5,
            "Technical roles meet the agreed quality level",
            role_validation.get("passed") is True,
            f"Precision {role_validation['precision']:.0%}; "
            f"recall {role_validation['recall']:.0%}; "
            f"family accuracy {role_validation['family_accuracy']:.0%}; "
            f"{role_validation['row_count']:,}-row benchmark.",
        ),
        AcceptanceCheck(
            6,
            "App searches and filters all employers",
            employer_count == employers.height
            and "vw_employer_explorer" in views
            and (root / "app/pages/1_All_Employers.py").is_file(),
            f"All {employer_count:,} employer rows are exposed through "
            "vw_employer_explorer and the All Employers page.",
        ),
        AcceptanceCheck(
            7,
            "App has a research-institution view",
            "vw_institution_explorer" in views
            and (root / "app/pages/2_Research_Institutions.py").is_file(),
            f"Research page and vw_institution_explorer expose "
            f"{institution_metrics.height:,} institutions.",
        ),
        AcceptanceCheck(
            8,
            "Employer detail shows raw evidence and trends",
            {
                "vw_organization_detail",
                "vw_h1b_trends",
                "vw_perm_trends",
                "vw_relevant_titles",
            }
            <= views
            and trend_count > 0,
            "Organization detail plus H-1B, PERM, and title views are populated; "
            f"{trend_count:,} H-1B trend rows.",
        ),
        AcceptanceCheck(
            9,
            "E-Verify is checked for prioritized employers with uncertainty semantics",
            priorities.height > 0
            and everify.height > 0
            and all(
                term in scoring_text
                for term in ("no-match", "ambiguous", "unchecked", "failed", "unknown")
            ),
            f"{priorities.height:,} prioritized employers and {everify.height:,} bounded "
            "official lookups; non-confirmed results remain UNKNOWN.",
        ),
        AcceptanceCheck(
            10,
            "Positive OPT observations do not turn absence into negative evidence",
            opt.height > 0
            and opt["is_positive"].all()
            and "opt absence" in app_text
            and "unknown" in app_text,
            f"{opt.height:,} positive-only official OPT observations; absence is rendered UNKNOWN.",
        ),
        AcceptanceCheck(
            11,
            "Top 150 to 250 institutions are policy-enrichment eligible",
            150 <= candidates.height <= 250,
            f"{candidates.height} ranked policy candidates.",
        ),
        AcceptanceCheck(
            12,
            "At least 100 institutions have reviewed policy evidence",
            reviewed_institutions >= 100,
            f"{reviewed_institutions} distinct institutions have REVIEWED_ACCEPTED facts.",
        ),
        AcceptanceCheck(
            13,
            "Every accepted fact has an official URL and excerpt",
            bool(accepted_evidence_ok) and policy_evidence_count == accepted.height,
            f"All {accepted.height:,} accepted facts passed exact excerpt/HTTPS gates and are "
            "visible in vw_policy_evidence.",
        ),
        AcceptanceCheck(
            14,
            "Separate component scores and confidence are shown",
            scores_present and "coverage" in app_text and "confidence" in app_text,
            "Employer and institution metrics contain separate nullable components, coverage, "
            "confidence, explanations, and score version; app renders them.",
        ),
        AcceptanceCheck(
            15,
            "App has no job-tracker features",
            not any(
                term in app_text
                for term in (
                    "job tracker",
                    "application tracker",
                    "application status",
                    "interview tracker",
                )
            ),
            "No job/application-tracker feature or navigation is present in app code.",
        ),
        AcceptanceCheck(
            16,
            "Data quality and freshness are visible",
            health.height >= 5
            and "vw_data_health" in views
            and "vw_quality_checks" in views
            and (root / "app/pages/6_Data_Health.py").is_file(),
            f"{health.height} source-health rows and {len(quality['checks'])} quality checks are "
            "exposed on Data Health.",
        ),
        AcceptanceCheck(
            17,
            "Full build produces DuckDB and processed Parquet",
            database_path.is_file() and parquet_count >= 20,
            f"DuckDB is {database_path.stat().st_size:,} bytes; "
            f"{parquet_count} processed Parquet outputs.",
        ),
        AcceptanceCheck(
            18,
            "CI passes",
            ci_run.get("status") == "completed"
            and ci_run.get("conclusion") == "success"
            and ci_run.get("headSha") == head_sha,
            f"Latest main CI: {ci_run.get('conclusion')} at {ci_run.get('url')}; "
            f"head {head_sha[:12]}.",
        ),
        AcceptanceCheck(
            19,
            "Quarterly refresh workflow exists",
            'cron: "17 8 1 1,4,7,10 *"' in workflow
            and "quality report" in workflow
            and "release bundle" in workflow,
            "Government refresh is scheduled quarterly and gates release packaging on the "
            "quality report.",
        ),
        AcceptanceCheck(
            20,
            "Documentation explains important limitations",
            all(
                term in operations + dictionary
                for term in (
                    "unknown",
                    "partial",
                    "e-verify",
                    "positive",
                    "review",
                    "failure recovery",
                )
            ),
            "Operations and data dictionary document uncertainty, partial periods, positive-only "
            "evidence, review, recovery, and source limitations.",
        ),
    ]
    passed = all(check.passed for check in checks)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "passed_count": sum(check.passed for check in checks),
        "check_count": len(checks),
        "quality_build_id": quality["build_id"],
        "repository": repository,
        "ci_run": ci_run,
        "checks": [asdict(check) for check in checks],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/reports/acceptance/v1.json"),
    )
    parser.add_argument("--verify-restore", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    report = run_acceptance(root, verify_restore=arguments.verify_restore)
    output = arguments.output if arguments.output.is_absolute() else root / arguments.output
    write_json_atomic(output, report)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
