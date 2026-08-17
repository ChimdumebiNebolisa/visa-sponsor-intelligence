"""Generate the pre-Phase-10 real-release baseline report."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from streamlit.testing.v1 import AppTest

from sponsor_intel.services import EmployerFilters, InstitutionFilters
from sponsor_intel.services.explorer import DuckDBExplorerService

CORE_POLICY_FACTS = (
    "h1b_research_staff_eligible",
    "pr_research_staff_eligible",
    "perm_supported",
    "eb1b_supported",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "runs_ms": [round(value, 2) for value in durations],
        "result_rows_or_bytes": result_size,
    }


def _coverage(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> dict[str, Any]:
    count, total = connection.execute(
        f"SELECT count(*) FILTER (WHERE {column} IS NOT NULL), count(*) FROM {table}"
    ).fetchone()
    return {
        "count": count,
        "total": total,
        "percentage": round((count / total * 100) if total else 0.0, 4),
    }


def _streamlit_apptest_timings(root: Path) -> dict[str, Any]:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    app = AppTest.from_file(str(root / "app" / "Home.py"), default_timeout=30)
    started = time.perf_counter()
    app.run()
    cold_ms = (time.perf_counter() - started) * 1000
    cold_errors = [str(item.value) for item in app.exception]
    started = time.perf_counter()
    app.run()
    warm_ms = (time.perf_counter() - started) * 1000
    warm_errors = [str(item.value) for item in app.exception]
    return {
        "method": "streamlit.testing.v1.AppTest Home.py execution",
        "cold_start_ms": round(cold_ms, 2),
        "warm_rerun_ms": round(warm_ms, 2),
        "cold_errors": cold_errors,
        "warm_errors": warm_errors,
    }


def _markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    score = report["score_coverage"]
    policy = report["policy_coverage"]
    quality = report["quality"]
    latency = report["latency_ms"]
    lines = [
        "# Phase 10 baseline",
        "",
        "> Captured before any Phase 10 scoring or ranking changes against the latest "
        "checksum-verified quality-approved release.",
        "",
        "## Release",
        "",
        f"- Git commit: `{report['git_commit_sha']}`",
        f"- Release tag: `{report['release']['tag']}`",
        f"- Build ID: `{report['release']['build_id']}`",
        f"- Score version: `{report['release']['score_version']}`",
        f"- Metric version: `{report['release']['metric_version']}`",
        "- Release checksum validation: "
        f"**{'PASS' if report['release']['checksums_valid'] else 'FAIL'}**",
        "",
        "## Counts",
        "",
        f"- Employers: {counts['employers']:,}",
        f"- Legal entities: {counts['legal_entities']:,}",
        f"- Parent organizations: {counts['parent_organizations']:,}",
        f"- Institutions: {counts['institutions']:,}",
        f"- Relevant LCA records: {counts['relevant_lca_records']:,}",
        f"- Relevant certified PERM records: {counts['relevant_certified_perm_records']:,}",
        f"- Entity review queue: {counts['entity_review_queue']:,}",
        f"- Ambiguous role classifications: {counts['ambiguous_role_classifications']:,}",
        "",
        "## Score coverage",
        "",
        "| Score | Rows | Denominator | Coverage |",
        "|---|---:|---:|---:|",
    ]
    for name, values in score.items():
        lines.append(
            f"| {name} | {values['count']:,} | {values['total']:,} | {values['percentage']:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Evidence and policy coverage",
            "",
            "- E-Verify lookup coverage: "
            f"{report['evidence_coverage']['everify_lookup']['count']:,} "
            f"of {report['evidence_coverage']['everify_lookup']['total']:,} "
            f"({report['evidence_coverage']['everify_lookup']['percentage']:.4f}%)",
            "- Confirmed E-Verify coverage: "
            f"{report['evidence_coverage']['everify_confirmed']['count']:,} "
            f"of {report['evidence_coverage']['everify_confirmed']['total']:,} "
            f"({report['evidence_coverage']['everify_confirmed']['percentage']:.4f}%)",
            f"- Positive OPT coverage: {report['evidence_coverage']['positive_opt']['count']:,} "
            f"of {report['evidence_coverage']['positive_opt']['total']:,} "
            f"({report['evidence_coverage']['positive_opt']['percentage']:.4f}%)",
            f"- Policy candidates attempted: {policy['institutions_attempted']:,}",
            "- Institutions with any accepted fact: "
            f"{policy['institutions_with_any_accepted_fact']:,}",
            f"- Institutions with all four accepted core facts: "
            f"{policy['institutions_with_complete_core_profile']:,}",
            "",
            "## Existing default ordering",
            "",
            f"- All Employers: `{report['default_ordering']['all_employers']}`",
            f"- Research Institutions: `{report['default_ordering']['research_institutions']}`",
            f"- Organization search: `{report['default_ordering']['organization_search']}`",
            "",
            "## Representative local query latency",
            "",
            "| Operation | Median | Runs | Result rows/bytes |",
            "|---|---:|---|---:|",
        ]
    )
    for name, values in latency.items():
        result_size = (
            values["result_rows_or_bytes"] if values["result_rows_or_bytes"] is not None else "n/a"
        )
        lines.append(
            f"| {name} | {values['median_ms']:.2f} ms | "
            f"{', '.join(f'{run:.2f}' for run in values['runs_ms'])} | "
            f"{result_size} |"
        )
    app = report["streamlit_measurement"]
    lines.extend(
        [
            "",
            "## Streamlit measurement",
            "",
            f"- Method: {app['method']}",
            f"- Cold Home execution: {app['cold_start_ms']:.2f} ms",
            f"- Warm Home rerun: {app['warm_rerun_ms']:.2f} ms",
            f"- Errors: {len(app['cold_errors']) + len(app['warm_errors'])}",
            "",
            "## Baseline quality result",
            "",
            f"- Passed: **{quality['passed']}**",
            f"- Critical failures: {quality['critical_failure_count']}",
            f"- Warnings: {quality['warning_count']}",
        ]
    )
    for warning in quality["warnings"]:
        lines.append(f"  - `{warning['check_id']}`: {warning['details']}")
    return "\n".join(lines) + "\n"


def generate(root: Path, *, release_tag: str) -> dict[str, Any]:
    database_path = root / "db" / "immigration.duckdb"
    release_root = root / "outputs" / "release"
    metadata = json.loads((release_root / "build-metadata.json").read_text(encoding="utf-8"))
    quality = json.loads(
        (root / "outputs" / "reports" / "quality" / "data_quality.json").read_text(encoding="utf-8")
    )
    expected_checksums: dict[str, str] = {}
    for line in (release_root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        checksum, name = line.split(maxsplit=1)
        expected_checksums[name] = checksum
    actual_checksums = {name: _sha256(release_root / name) for name in expected_checksums}

    connection = duckdb.connect(str(database_path), read_only=True)
    service = DuckDBExplorerService(database_path)
    try:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM employer_metrics),
                (SELECT count(*) FROM legal_entities),
                (SELECT count(*) FROM parent_organizations),
                (SELECT count(*) FROM institutions),
                (SELECT coalesce(sum(relevant_lca_count), 0) FROM employer_metrics),
                (SELECT coalesce(sum(relevant_certified_perm_count), 0) FROM employer_metrics),
                (SELECT count(*) FROM vw_entity_review_queue),
                (SELECT count(*) FROM lca_cases_resolved WHERE role_family = 'ambiguous') +
                (SELECT count(*) FROM perm_cases_resolved WHERE role_family = 'ambiguous')
            """
        ).fetchone()
        count_names = (
            "employers",
            "legal_entities",
            "parent_organizations",
            "institutions",
            "relevant_lca_records",
            "relevant_certified_perm_records",
            "entity_review_queue",
            "ambiguous_role_classifications",
        )

        policy_facts = pl.read_parquet(root / "data" / "processed" / "policy_facts.parquet")
        accepted = policy_facts.filter(
            (pl.col("human_review_status") == "REVIEWED_ACCEPTED")
            & pl.col("exact_excerpt_verified")
            & pl.col("is_current")
            & pl.col("valid_to").is_null()
            & pl.col("source_url").str.starts_with("https://")
        )
        complete_core = (
            accepted.filter(pl.col("fact_type").is_in(CORE_POLICY_FACTS))
            .group_by("institution_id")
            .agg(pl.col("fact_type").n_unique().alias("core_count"))
            .filter(pl.col("core_count") == len(CORE_POLICY_FACTS))
            .height
        )
        candidate_path = root / "data" / "processed" / "policy_candidates.parquet"
        candidates = pl.read_parquet(candidate_path) if candidate_path.is_file() else pl.DataFrame()

        top_employers = service.list_employers(limit=5)
        top_institutions = service.list_institutions(limit=5)
        organization_ids = tuple(top_employers["organization_id"].head(3).to_list())
        detail_id = organization_ids[0]
        latency = {
            "employer_search": _timed(
                lambda: service.list_employers(EmployerFilters(search="UNIVERSITY"), limit=500)
            ),
            "institution_search": _timed(
                lambda: service.list_institutions(
                    InstitutionFilters(search="UNIVERSITY"), limit=500
                )
            ),
            "organization_detail": _timed(lambda: service.get_organization_detail(detail_id)),
            "comparison": _timed(lambda: service.compare_organizations(organization_ids)),
            "full_filtered_employer_csv_export": _timed(
                lambda: service.export_employers(EmployerFilters(), "csv"), repeats=1
            ),
            "full_filtered_institution_csv_export": _timed(
                lambda: service.export_institutions(InstitutionFilters(), "csv"), repeats=1
            ),
        }

        report: dict[str, Any] = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "release": {
                "tag": release_tag,
                "build_id": metadata["build_id"],
                "generated_at": metadata["generated_at"],
                "score_version": metadata["score_version"],
                "metric_version": metadata["metric_version"],
                "asset_sizes_bytes": {
                    path.name: path.stat().st_size
                    for path in sorted(release_root.iterdir())
                    if path.is_file()
                },
                "expected_checksums": expected_checksums,
                "actual_checksums": actual_checksums,
                "checksums_valid": expected_checksums == actual_checksums,
            },
            "counts": dict(zip(count_names, counts, strict=True)),
            "score_coverage": {
                "employer.stem_opt_readiness_score": _coverage(
                    connection, "employer_metrics", "stem_opt_readiness_score"
                ),
                "employer.h1b_history_score": _coverage(
                    connection, "employer_metrics", "h1b_history_score"
                ),
                "employer.green_card_history_score": _coverage(
                    connection, "employer_metrics", "green_card_history_score"
                ),
                "employer.immigration_evidence_score": _coverage(
                    connection, "employer_metrics", "immigration_evidence_score"
                ),
                "institution.research_strength_score": _coverage(
                    connection, "institution_metrics", "research_strength_score"
                ),
                "institution.policy_support_score": _coverage(
                    connection, "institution_metrics", "policy_support_score"
                ),
                "institution.research_pathway_score": _coverage(
                    connection, "institution_metrics", "research_pathway_score"
                ),
            },
            "evidence_coverage": {
                "everify_lookup": {
                    "count": connection.execute(
                        "SELECT count(*) FROM employer_metrics "
                        "WHERE everify_lookup_status <> 'NOT_CHECKED'"
                    ).fetchone()[0],
                    "total": counts[0],
                },
                "everify_confirmed": {
                    "count": connection.execute(
                        "SELECT count(*) FROM employer_metrics WHERE everify_status IN "
                        "('CONFIRMED_ACTIVE', 'CONFIRMED_INACTIVE')"
                    ).fetchone()[0],
                    "total": counts[0],
                },
                "positive_opt": {
                    "count": connection.execute(
                        "SELECT count(*) FROM employer_metrics "
                        "WHERE known_opt_observation = 'OBSERVED_POSITIVE'"
                    ).fetchone()[0],
                    "total": counts[0],
                },
            },
            "policy_coverage": {
                "institutions_attempted": candidates["institution_id"].n_unique()
                if "institution_id" in candidates.columns
                else 0,
                "institutions_with_any_accepted_fact": accepted["institution_id"].n_unique(),
                "institutions_with_complete_core_profile": complete_core,
            },
            "default_ordering": {
                "all_employers": (
                    "relevant_lca_count DESC, initial_approvals DESC, organization_name"
                ),
                "research_institutions": ("total_rd DESC, relevant_lca_count DESC, official_name"),
                "organization_search": "relevant_lca_count DESC, organization_name",
                "baseline_top_employers": top_employers["organization_name"].to_list(),
                "baseline_top_institutions": top_institutions["official_name"].to_list(),
            },
            "latency_ms": latency,
            "streamlit_measurement": _streamlit_apptest_timings(root),
            "quality": {
                "passed": quality["passed"],
                "critical_failure_count": quality["critical_failure_count"],
                "warning_count": sum(check["status"] == "WARN" for check in quality["checks"]),
                "warnings": [check for check in quality["checks"] if check["status"] == "WARN"],
            },
        }
        for values in report["evidence_coverage"].values():
            values["percentage"] = round(values["count"] / values["total"] * 100, 4)
        return report
    finally:
        service.close()
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--release-tag", default="data-2026-08-15")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    report = generate(root, release_tag=arguments.release_tag)
    output_root = root / "outputs" / "reports" / "phase10"
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "baseline.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_root / "baseline.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
