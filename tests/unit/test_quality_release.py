"""Quality-gate and private release packaging coverage."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from sponsor_intel.quality import QualityReporter
from sponsor_intel.releases import build_release_bundle


def _write_quality_fixture(root: Path) -> tuple[Path, Path]:
    data_root = root / "data"
    output_root = root / "outputs"
    processed = data_root / "processed"
    processed.mkdir(parents=True)
    institution_ids = [f"ipeds:{index:06d}" for index in range(100)]

    pl.DataFrame(
        {
            "organization_id": ["org-1"],
            "metric_version": ["scored_metrics_v1"],
            "score_version": ["evidence_scores_v1"],
            "immigration_evidence_coverage": [0.75],
        }
    ).write_parquet(processed / "employer_metrics.parquet")
    pl.DataFrame(
        {
            "institution_id": institution_ids,
            "metric_version": ["scored_metrics_v1"] * 100,
            "score_version": ["evidence_scores_v1"] * 100,
            "research_pathway_coverage": [0.65] * 100,
        }
    ).write_parquet(processed / "institution_metrics.parquet")
    for name, case_id in (("lca", "lca-1"), ("perm", "perm-1")):
        pl.DataFrame(
            {
                "source_artifact_id": [f"{name}-artifact"],
                "case_id": [case_id],
                "organization_id": ["org-1"],
                "technical_role": [True],
            }
        ).write_parquet(processed / f"{name}_cases_resolved.parquet")
    pl.DataFrame(
        {
            "source_artifact_id": ["uscis-artifact"],
            "source_row_number": [1],
            "organization_id": ["org-1"],
        }
    ).write_parquet(processed / "h1b_petitions_resolved.parquet")
    pl.DataFrame(
        {
            "source_id": ["dol_lca", "dol_perm", "uscis_h1b", "ipeds", "herd"],
            "row_count": [1, 1, 1, 100, 100],
            "freshness_warning": [""] * 5,
        }
    ).write_parquet(processed / "data_health.parquet")
    pl.DataFrame(
        {"legal_entity_id": ["legal-1"], "parent_organization_id": ["parent-1"]}
    ).write_parquet(processed / "legal_entities.parquet")
    pl.DataFrame({"parent_organization_id": ["parent-1"]}).write_parquet(
        processed / "parent_organizations.parquet"
    )
    pl.DataFrame({"institution_id": institution_ids}).write_parquet(
        processed / "institutions.parquet"
    )
    pl.DataFrame(
        {
            "institution_id": institution_ids,
            "human_review_status": ["REVIEWED_ACCEPTED"] * 100,
            "exact_excerpt_verified": [True] * 100,
            "source_url": ["https://example.edu/policy"] * 100,
            "supporting_excerpt": ["The institution supports this reviewed fact."] * 100,
        }
    ).write_parquet(processed / "policy_facts.parquet")

    manifests = output_root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "source_artifacts.jsonl").write_text(
        json.dumps(
            {
                "source_id": "dol_lca",
                "schema_version": "fixture_v1",
                "parser_version": "1.0.0",
                "validation_status": "PASSED",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    schema_root = output_root / "reports" / "schema" / "dol_lca"
    schema_root.mkdir(parents=True)
    (schema_root / "fixture.json").write_text(
        json.dumps({"missing_required_columns": []}), encoding="utf-8"
    )
    return data_root, output_root


def test_quality_report_passes_complete_fixture_and_blocks_regression(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    reporter = QualityReporter(data_root=data_root, output_root=output_root)

    report = reporter.build()

    assert report.passed
    assert report.critical_failure_count == 0
    assert report.checks_path.is_file()
    assert report.report_path.is_file()
    assert all(check.status == "PASS" for check in report.checks)

    facts_path = data_root / "processed" / "policy_facts.parquet"
    pl.read_parquet(facts_path).with_columns(
        pl.lit(False).alias("exact_excerpt_verified")
    ).write_parquet(facts_path)
    failed = reporter.build()

    assert not failed.passed
    assert failed.critical_failure_count == 1
    assert (
        next(
            check for check in failed.checks if check.check_id == "accepted_policy_evidence"
        ).status
        == "FAIL"
    )


def test_release_bundle_requires_passing_quality_and_writes_checksums(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    reporter = QualityReporter(data_root=data_root, output_root=output_root)
    assert reporter.build().passed
    database_path = tmp_path / "db" / "immigration.duckdb"
    database_path.parent.mkdir()
    database_path.write_bytes(b"fixture database")
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")

    bundle = build_release_bundle(
        repository_root=tmp_path,
        data_root=Path("data"),
        database_path=Path("db/immigration.duckdb"),
        output_root=Path("outputs"),
    )

    assert len(bundle.assets) == 6
    assert all(path.is_file() for path in bundle.assets)
    assert len(bundle.checksums_path.read_text(encoding="utf-8").splitlines()) == 6
    with zipfile.ZipFile(bundle.release_root / "processed-parquet.zip") as archive:
        assert "data/processed/employer_metrics.parquet" in archive.namelist()

    facts_path = data_root / "processed" / "policy_facts.parquet"
    pl.read_parquet(facts_path).with_columns(
        pl.lit(False).alias("exact_excerpt_verified")
    ).write_parquet(facts_path)
    assert not reporter.build().passed
    with pytest.raises(ValueError, match="publication is blocked"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_scheduled_workflows_gate_release_publication() -> None:
    root = Path(".github/workflows")
    government = (root / "refresh_government_data.yml").read_text(encoding="utf-8")
    policies = (root / "refresh_policies.yml").read_text(encoding="utf-8")
    publication = (root / "publish_data_release.yml").read_text(encoding="utf-8")

    assert "schedule:" in government and "quality report" in government
    assert "schedule:" in policies and "quality report" in policies
    assert government.index("quality report") < government.index("release bundle")
    assert policies.index("quality report") < policies.index("release bundle")
    assert "workflow_call:" in publication
    assert "uses: ./.github/workflows/publish_data_release.yml" in government
    assert "uses: ./.github/workflows/publish_data_release.yml" in policies
    assert "critical_failure_count" in publication
