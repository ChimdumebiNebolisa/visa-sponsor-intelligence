"""Product A quality-gate and private release packaging coverage."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import polars as pl
import pytest

from sponsor_intel.database.builder import REQUIRED_VIEWS
from sponsor_intel.quality import QualityReporter
from sponsor_intel.releases import build_release_bundle


def _rating_columns(prefix: str, *, score: float, stars: int) -> dict[str, list[object]]:
    return {
        f"{prefix}_score": [score],
        f"{prefix}_status": ["RATED"],
        f"{prefix}_coverage": [1.0],
        f"{prefix}_star_rating": [stars],
        f"{prefix}_stars": ["★" * stars + "☆" * (5 - stars)],
        f"{prefix}_star_label": [f"{stars} out of 5 stars"],
        f"{prefix}_explanation": ["Fixture official historical evidence."],
    }


def _write_quality_fixture(root: Path) -> tuple[Path, Path]:
    data_root = root / "data"
    output_root = root / "outputs"
    processed = data_root / "processed"
    processed.mkdir(parents=True)

    employer_columns: dict[str, list[object]] = {
        "organization_id": ["org-1"],
        "metric_version": ["product_a_metrics_v1"],
        "score_version": ["product_a_scores_v1"],
        "entity_coverage_state": ["COMPLETE_ENTITY_COVERAGE"],
        "h1b_entity_coverage_state": ["COMPLETE_ENTITY_COVERAGE"],
        "perm_entity_coverage_state": ["COMPLETE_ENTITY_COVERAGE"],
    }
    employer_columns.update(_rating_columns("h1b_history", score=80.0, stars=5))
    employer_columns.update(_rating_columns("green_card_history", score=70.0, stars=4))
    employer_columns.update(_rating_columns("overall_sponsorship", score=74.0, stars=4))
    pl.DataFrame(employer_columns).write_parquet(processed / "employer_metrics.parquet")

    institution_columns = dict(employer_columns)
    institution_columns.pop("organization_id")
    institution_columns["institution_id"] = ["ipeds:000001"]
    institution_columns.update(
        {
            "research_scale_score": [72.0],
            "research_scale_status": ["RATED"],
            "research_scale_star_rating": [4],
            "research_scale_stars": ["★★★★☆"],
            "research_scale_star_label": ["4 out of 5 stars"],
            "research_scale_explanation": ["Latest-year official HERD evidence."],
        }
    )
    pl.DataFrame(institution_columns).write_parquet(processed / "institution_metrics.parquet")

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
            "row_count": [1, 1, 1, 1, 1],
            "freshness_warning": [""] * 5,
        }
    ).write_parquet(processed / "data_health.parquet")
    pl.DataFrame(
        {"legal_entity_id": ["legal-1"], "parent_organization_id": ["parent-1"]}
    ).write_parquet(processed / "legal_entities.parquet")
    pl.DataFrame({"parent_organization_id": ["parent-1"]}).write_parquet(
        processed / "parent_organizations.parquet"
    )
    pl.DataFrame({"institution_id": ["ipeds:000001"]}).write_parquet(
        processed / "institutions.parquet"
    )
    pl.DataFrame({"institution_id": ["ipeds:000001"], "survey_year": [2024]}).write_parquet(
        processed / "herd_observations.parquet"
    )
    artifact_rows = [
        {
            "source_artifact_id": "lca-artifact",
            "source_id": "dol_lca",
            "fiscal_year": 2022,
            "schema_version": "fixture_v1",
            "parser_version": "1.0.0",
            "sha256": "a" * 64,
            "validation_status": "PASSED",
        },
        {
            "source_artifact_id": "perm-artifact",
            "source_id": "dol_perm",
            "fiscal_year": 2022,
            "schema_version": "fixture_v1",
            "parser_version": "1.0.0",
            "sha256": "b" * 64,
            "validation_status": "PASSED",
        },
        {
            "source_artifact_id": "uscis-artifact",
            "source_id": "uscis_h1b",
            "fiscal_year": 2022,
            "schema_version": "fixture_v1",
            "parser_version": "1.0.0",
            "sha256": "c" * 64,
            "validation_status": "PASSED",
        },
    ]
    pl.DataFrame(artifact_rows).write_parquet(processed / "source_artifacts.parquet")

    manifests = output_root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "source_artifacts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in artifact_rows) + "\n",
        encoding="utf-8",
    )
    schema_root = output_root / "reports" / "schema"
    for row in artifact_rows:
        report_path = schema_root / str(row["source_id"]) / f"{row['source_artifact_id']}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"missing_required_columns": []}), encoding="utf-8")
    return data_root, output_root


def _write_release_database(root: Path, data_root: Path) -> Path:
    """Create a real, minimal DuckDB implementing the Product A release contract."""

    database_path = root / "db" / "immigration.duckdb"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    processed = data_root / "processed"
    table_names = (
        "data_health",
        "employer_metrics",
        "h1b_petitions_resolved",
        "herd_observations",
        "institution_metrics",
        "institutions",
        "lca_cases_resolved",
        "legal_entities",
        "parent_organizations",
        "perm_cases_resolved",
        "quality_checks",
        "source_artifacts",
    )
    with duckdb.connect(str(database_path)) as connection:
        for table_name in table_names:
            connection.execute(
                f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet(?)",
                [(processed / f"{table_name}.parquet").as_posix()],
            )
        connection.execute("CREATE TABLE entity_aliases AS SELECT 'alias-1' AS alias_id")
        connection.execute(
            "CREATE TABLE everify_observations AS "
            "SELECT CAST(NULL AS VARCHAR) AS organization_id WHERE false"
        )
        connection.execute(
            "CREATE TABLE opt_employer_observations AS "
            "SELECT CAST(NULL AS VARCHAR) AS organization_id WHERE false"
        )

        view_sources = {
            "vw_data_health": "data_health",
            "vw_employer_explorer": "employer_metrics",
            "vw_everify_evidence": "everify_observations",
            "vw_institution_explorer": "institution_metrics",
            "vw_opt_evidence": "opt_employer_observations",
            "vw_organization_detail": "employer_metrics",
            "vw_quality_checks": "quality_checks",
            "vw_source_artifacts": "source_artifacts",
        }
        for view_name in set(REQUIRED_VIEWS) - {
            "vw_policy_evidence",
            "vw_policy_review_queue",
        }:
            source = view_sources.get(view_name)
            select = f"SELECT * FROM {source}" if source else "SELECT 'fixture' AS value"
            connection.execute(f"CREATE VIEW {view_name} AS {select}")
    return database_path


def test_quality_report_passes_without_policy_artifacts(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)

    report = QualityReporter(data_root=data_root, output_root=output_root).build()

    assert report.passed
    assert report.critical_failure_count == 0
    assert report.build_id.startswith("product-a-")
    assert report.metric_version == "product_a_metrics_v1"
    assert report.score_version == "product_a_scores_v1"
    assert all(check.status == "PASS" for check in report.checks)
    assert not any(check.category == "policy" for check in report.checks)
    assert not (data_root / "processed" / "policy_facts.parquet").exists()
    quality_json = json.loads(report.report_path.read_text(encoding="utf-8"))
    metadata_json = json.loads(report.metadata_path.read_text(encoding="utf-8"))
    assert metadata_json["generated_at"] == quality_json["generated_at"]
    assert set(metadata_json["output_fingerprints"]) == {
        "employer_metrics.parquet",
        "institution_metrics.parquet",
    }


def test_quality_report_ignores_inactive_append_only_manifest_records(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    reporter = QualityReporter(data_root=data_root, output_root=output_root)
    original = reporter.build()

    manifest_path = output_root / "manifests" / "source_artifacts.jsonl"
    with manifest_path.open("a", encoding="utf-8") as manifest:
        manifest.write(
            json.dumps(
                {
                    "source_artifact_id": "stale-artifact",
                    "source_id": "dol_lca",
                    "schema_version": "legacy_v0",
                    "parser_version": "0.1.0",
                    "sha256": "d" * 64,
                    "validation_status": "FAILED",
                }
            )
            + "\n"
        )
    stale_schema = output_root / "reports" / "schema" / "dol_lca" / "stale-artifact.json"
    stale_schema.write_text(json.dumps({"missing_required_columns": ["case_id"]}), encoding="utf-8")

    rebuilt = reporter.build()

    assert rebuilt.passed
    assert rebuilt.build_id == original.build_id
    source_check = next(check for check in rebuilt.checks if check.check_id == "source_manifest")
    assert source_check.value == 3
    assert "legacy_v0" not in source_check.details


def test_quality_build_id_ignores_optional_policy_evidence(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    reporter = QualityReporter(data_root=data_root, output_root=output_root)
    original = reporter.build()
    pl.DataFrame(
        {
            "policy_fact_id": ["fact-1"],
            "institution_id": ["ipeds:000001"],
            "fact_value": ["YES"],
            "human_review_status": ["REVIEWED_ACCEPTED"],
        }
    ).write_parquet(data_root / "processed" / "policy_facts.parquet")
    institution_metrics_path = data_root / "processed" / "institution_metrics.parquet"
    pl.read_parquet(institution_metrics_path).with_columns(
        pl.lit("YES").alias("research_staff_h1b_policy")
    ).write_parquet(institution_metrics_path)

    with_policy = reporter.build()

    assert with_policy.passed
    assert with_policy.build_id == original.build_id
    assert not any(check.category == "policy" for check in with_policy.checks)


@pytest.mark.parametrize(
    ("column", "stale_value"),
    [
        ("metric_version", "scored_metrics_v2"),
        ("score_version", "evidence_scores_v2_2026_08"),
    ],
)
def test_quality_report_rejects_stale_product_a_contract(
    tmp_path: Path,
    column: str,
    stale_value: str,
) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    metrics_path = data_root / "processed" / "institution_metrics.parquet"
    pl.read_parquet(metrics_path).with_columns(pl.lit(stale_value).alias(column)).write_parquet(
        metrics_path
    )

    report = QualityReporter(data_root=data_root, output_root=output_root).build()

    assert not report.passed
    assert next(check for check in report.checks if check.check_id == "score_contract").status == (
        "FAIL"
    )


def test_quality_report_rejects_star_on_resolved_zero(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    metrics_path = data_root / "processed" / "employer_metrics.parquet"
    pl.read_parquet(metrics_path).with_columns(
        pl.lit(0.0).alias("h1b_history_score"),
        pl.lit(1).alias("h1b_history_star_rating"),
    ).write_parquet(metrics_path)

    report = QualityReporter(data_root=data_root, output_root=output_root).build()

    assert not report.passed
    assert next(check for check in report.checks if check.check_id == "score_contract").status == (
        "FAIL"
    )


def test_release_bundle_passes_without_policy_files_caches_or_openai_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_POLICY_MODEL", raising=False)
    data_root, output_root = _write_quality_fixture(tmp_path)
    reporter = QualityReporter(data_root=data_root, output_root=output_root)
    assert reporter.build().passed
    _write_release_database(tmp_path, data_root)
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
        assert not any(name.startswith("data/processed/policy_") for name in archive.namelist())
    with zipfile.ZipFile(bundle.release_root / "build-state.zip") as archive:
        assert not any("/cache/policy_" in name for name in archive.namelist())

    metrics_path = data_root / "processed" / "employer_metrics.parquet"
    pl.read_parquet(metrics_path).with_columns(
        pl.lit("stale_product_b_score").alias("score_version")
    ).write_parquet(metrics_path)
    assert not reporter.build().passed
    with pytest.raises(ValueError, match="publication is blocked"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_excludes_inactive_scores_and_supplemental_policy(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = _write_release_database(tmp_path, data_root)
    with duckdb.connect(str(database_path)) as connection:
        for table_name in ("policy_documents", "policy_facts", "policy_review_queue"):
            connection.execute(f"CREATE TABLE {table_name} AS SELECT 'supplemental' AS value")
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")

    processed = data_root / "processed"
    inactive_sidecars = {
        "employer_scores_v1.parquet",
        "employer_scores_v2.parquet",
        "institution_scores_v1.parquet",
    }
    for name in inactive_sidecars:
        pl.DataFrame({"legacy_score": [1]}).write_parquet(processed / name)
    for name in ("policy_documents.parquet", "policy_facts.parquet", "policy_review_queue.parquet"):
        pl.DataFrame({"supplemental_id": [name]}).write_parquet(processed / name)
    for directory in (
        data_root / "cache" / "policy_discovery",
        data_root / "cache" / "policy_extraction",
    ):
        directory.mkdir(parents=True)
        (directory / "supplemental.json").write_text("{}", encoding="utf-8")

    bundle = build_release_bundle(
        repository_root=tmp_path,
        data_root=Path("data"),
        database_path=Path("db/immigration.duckdb"),
        output_root=Path("outputs"),
    )

    with zipfile.ZipFile(bundle.release_root / "processed-parquet.zip") as archive:
        archived_names = set(archive.namelist())
    inactive_paths = {f"data/processed/{name}" for name in inactive_sidecars}
    assert archived_names.isdisjoint(inactive_paths)
    assert not any(name.startswith("data/processed/policy_") for name in archived_names)
    assert {
        "data/processed/employer_metrics.parquet",
        "data/processed/source_artifacts.parquet",
    } <= archived_names
    with zipfile.ZipFile(bundle.release_root / "build-state.zip") as archive:
        assert not any("/cache/policy_" in name for name in archive.namelist())
    with duckdb.connect(
        str(bundle.release_root / "immigration.duckdb"), read_only=True
    ) as connection:
        for table_name in ("policy_documents", "policy_facts", "policy_review_queue"):
            assert connection.execute(f"SELECT count(*) FROM {table_name}").fetchone() == (0,)


def test_release_bundle_rejects_arbitrary_database_bytes(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = tmp_path / "db" / "immigration.duckdb"
    database_path.parent.mkdir(parents=True)
    database_path.write_bytes(b"not a DuckDB database")
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")

    with pytest.raises(ValueError, match="failed read-only Product A validation"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


@pytest.mark.parametrize(
    ("relation_kind", "relation_name", "message"),
    [
        ("TABLE", "herd_observations", "required Product A tables"),
        ("VIEW", "vw_entity_review_queue", "required Product A views"),
    ],
)
def test_release_bundle_rejects_missing_product_a_relations(
    tmp_path: Path,
    relation_kind: str,
    relation_name: str,
    message: str,
) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = _write_release_database(tmp_path, data_root)
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(f"DROP {relation_kind} {relation_name}")

    with pytest.raises(ValueError, match=message):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_rejects_wrong_database_versions(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = _write_release_database(tmp_path, data_root)
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("UPDATE employer_metrics SET score_version = 'stale_product_b_score'")

    with pytest.raises(ValueError, match="outside the Product A version contract"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_rejects_stale_database_row_counts(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = _write_release_database(tmp_path, data_root)
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("INSERT INTO employer_metrics SELECT * FROM employer_metrics")

    with pytest.raises(ValueError, match="row counts disagree for employer_metrics"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_rejects_database_from_another_build(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    database_path = _write_release_database(tmp_path, data_root)
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("UPDATE quality_checks SET build_id = 'product-a-ffffffffffffffff'")

    with pytest.raises(ValueError, match="do not match the approved Product A build"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_rejects_build_metadata_count_mismatch(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    _write_release_database(tmp_path, data_root)
    for directory in (data_root / "resolved", data_root / "classified"):
        directory.mkdir()
        (directory / "fixture.txt").write_text("fixture", encoding="utf-8")
    metadata_path = output_root / "reports" / "quality" / "build_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["employer_count"] = 2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="row counts disagree for employer_metrics"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_policy_caches_cannot_substitute_for_core_build_state(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    _write_release_database(tmp_path, data_root)
    policy_cache = data_root / "cache" / "policy_extraction"
    policy_cache.mkdir(parents=True)
    (policy_cache / "fixture.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Resolved/classified build state"):
        build_release_bundle(
            repository_root=tmp_path,
            data_root=Path("data"),
            database_path=Path("db/immigration.duckdb"),
            output_root=Path("outputs"),
        )


def test_release_bundle_includes_only_active_source_state_artifacts(tmp_path: Path) -> None:
    data_root, output_root = _write_quality_fixture(tmp_path)
    assert QualityReporter(data_root=data_root, output_root=output_root).build().passed
    _write_release_database(tmp_path, data_root)

    active_names: set[str] = set()
    stale_names: set[str] = set()
    core_names: set[str] = set()
    for layer in ("resolved", "classified"):
        layer_root = data_root / layer
        core = layer_root / "core-state.parquet"
        core.parent.mkdir(parents=True, exist_ok=True)
        core.write_bytes(b"core")
        core_names.add(core.relative_to(tmp_path).as_posix())

        source_root = layer_root / "sources" / "dol_lca" / "fy=2022"
        source_root.mkdir(parents=True)
        active = source_root / "lca-artifact.parquet"
        active.write_bytes(b"active")
        active_names.add(active.relative_to(tmp_path).as_posix())
        stale = source_root / "stale-lca-artifact.parquet"
        stale.write_bytes(b"stale")
        stale_names.add(stale.relative_to(tmp_path).as_posix())

    bundle = build_release_bundle(
        repository_root=tmp_path,
        data_root=Path("data"),
        database_path=Path("db/immigration.duckdb"),
        output_root=Path("outputs"),
    )

    with zipfile.ZipFile(bundle.release_root / "build-state.zip") as archive:
        archived_names = set(archive.namelist())
    assert active_names <= archived_names
    assert core_names <= archived_names
    assert archived_names.isdisjoint(stale_names)


def test_workflows_make_policy_refresh_manual_and_non_blocking() -> None:
    root = Path(".github/workflows")
    government = (root / "refresh_government_data.yml").read_text(encoding="utf-8")
    policies = (root / "refresh_policies.yml").read_text(encoding="utf-8")
    publication = (root / "publish_data_release.yml").read_text(encoding="utf-8")

    assert "schedule:" in government and "quality report" in government
    assert "workflow_dispatch:" in policies and "schedule:" not in policies
    acceptance = "scripts/run_product_a_acceptance.py"
    assert government.index("quality report") < government.index(acceptance)
    assert government.index(acceptance) < government.index("release bundle")
    assert "--database db/immigration.duckdb" in government
    assert "--output-root outputs/reports/product-a" in government
    assert "uv run sponsor-intel refresh policies" in policies
    assert "name: supplemental-manual-policy-review" in policies
    assert "data/processed/policy_documents.parquet" in policies
    assert "data/processed/policy_facts.parquet" in policies
    assert "data/processed/policy_review_queue.parquet" in policies
    assert "outputs/reports/policy/evaluation.json" in policies
    assert "if-no-files-found: warn" in policies
    assert "retention-days: 7" in policies
    assert "quality report" not in policies
    assert "release bundle" not in policies
    assert "outputs/release" not in policies
    assert "publish_data_release.yml" not in policies
    assert "Restore reusable non-policy build inputs" in government
    assert "data/processed/policy_*" in government
    assert "OPENAI_API_KEY" not in government
    assert "workflow_call:" in publication
    assert '"metric_version": "product_a_metrics_v1"' in publication
    assert '"score_version": "product_a_scores_v1"' in publication
    assert "Refuse publication from a public repository" in publication
