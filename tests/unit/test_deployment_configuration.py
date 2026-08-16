"""Deployment configuration and dependency-boundary coverage."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sponsor_intel.config import load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_release_mode_requires_data_and_a_read_token(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="SPONSOR_INTEL_REQUIRE_DATA"):
        load_settings(
            tmp_path / "missing.yaml",
            environ={"SPONSOR_INTEL_DEPLOYMENT_MODE": "release"},
        )

    with pytest.raises(ValidationError, match="GITHUB_RELEASE_READ_TOKEN"):
        load_settings(
            tmp_path / "missing.yaml",
            environ={
                "SPONSOR_INTEL_DEPLOYMENT_MODE": "release",
                "SPONSOR_INTEL_REQUIRE_DATA": "true",
            },
        )


def test_release_settings_validation_redacts_token_input(tmp_path: Path) -> None:
    token = "test-token-validation-errors-must-not-render-this-suffix"

    with pytest.raises(ValidationError) as captured:
        load_settings(
            tmp_path / "missing.yaml",
            environ={
                "SPONSOR_INTEL_DEPLOYMENT_MODE": "release",
                "SPONSOR_INTEL_REQUIRE_DATA": "false",
                "GITHUB_RELEASE_READ_TOKEN": token,
            },
        )

    rendered = str(captured.value)
    assert token not in rendered
    assert token[-20:] not in rendered


def test_github_token_is_rejected_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        "github_release_read_token: never-store-this-here\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be supplied through the environment"):
        load_settings(config_path, environ={})


def test_streamlit_production_config_preserves_security_controls() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

    assert config["server"]["headless"] is True
    assert config["server"]["enableCORS"] is True
    assert config["server"]["enableXsrfProtection"] is True
    assert config["server"]["maxUploadSize"] <= 1
    assert config["client"]["showErrorDetails"] == "none"
    assert config["client"]["showErrorLinks"] is False
    assert config["client"]["toolbarMode"] in {"minimal", "viewer"}
    assert config["browser"]["gatherUsageStats"] is False


def test_deployment_dependency_file_excludes_ingestion_packages() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = "\n".join(project["project"]["dependencies"]).casefold()
    ingestion = "\n".join(project["dependency-groups"]["ingestion"]).casefold()
    requirements = (ROOT / "app" / "requirements.txt").read_text(encoding="utf-8")

    assert "-e ." in requirements
    for package in ("openai", "playwright", "pdfplumber", "fastexcel", "selectolax", "xlsx2csv"):
        assert package not in runtime
        assert package in ingestion
    assert set(project["tool"]["uv"]["default-groups"]) == {"dev", "ingestion"}

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "from sponsor_intel.deployment import ReleaseBootstrap" in workflow
    assert "from components.explorer import explorer_service" in workflow


def test_real_streamlit_secrets_remain_ignored_but_examples_are_tracked() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".streamlit/*" in ignore
    assert "!.streamlit/config.toml" in ignore
    assert "!.streamlit/secrets.example.toml" in ignore
    assert "GITHUB_RELEASE_READ_TOKEN" in (ROOT / ".streamlit" / "secrets.example.toml").read_text(
        encoding="utf-8"
    )


def test_release_workflow_blocks_public_repository_publication() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish_data_release.yml").read_text(
        encoding="utf-8"
    )

    assert "Refuse publication from a public repository" in workflow
    assert "--json visibility" in workflow
    assert '!= "PRIVATE"' in workflow
    assert '"metric_version": "product_a_metrics_v1"' in workflow
    assert '"score_version": "product_a_scores_v1"' in workflow
    assert "product-a-[0-9a-f]{16}" in workflow
    assert "Required runtime release assets are missing" in workflow


def test_government_refresh_blocks_public_release_artifact_upload() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh_government_data.yml").read_text(
        encoding="utf-8"
    )
    parsed = yaml.safe_load(workflow)

    guard = "Refuse release artifact upload from a public repository"
    upload = "Upload quality-approved release input"
    acceptance = "Require Product A real-data acceptance"
    bundle = "Build private release assets"
    assert guard in workflow
    assert "--json visibility --jq .visibility" in workflow
    assert workflow.index(guard) < workflow.index(upload)
    steps = parsed["jobs"]["refresh"]["steps"]
    step_names = [step["name"] for step in steps]
    assert step_names.index("Confirm publication quality gates") < step_names.index(acceptance)
    assert step_names.index(acceptance) < step_names.index(bundle)
    acceptance_step = next(step for step in steps if step["name"] == acceptance)
    assert "scripts/run_product_a_acceptance.py" in acceptance_step["run"]
    assert "--data-root data" in acceptance_step["run"]
    assert "--database db/immigration.duckdb" in acceptance_step["run"]
    assert "--output-root outputs/reports/product-a" in acceptance_step["run"]


def test_policy_refresh_is_manual_and_government_refresh_excludes_policy_state() -> None:
    government = (ROOT / ".github" / "workflows" / "refresh_government_data.yml").read_text(
        encoding="utf-8"
    )
    policies = (ROOT / ".github" / "workflows" / "refresh_policies.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in policies
    assert "schedule:" not in policies
    assert "uv run sponsor-intel refresh policies" in policies
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in policies
    assert "OPENAI_POLICY_MODEL: gpt-5.6-luna" in policies
    assert "quality report" not in policies
    assert "release bundle" not in policies
    assert "outputs/release" not in policies
    assert "--json visibility" not in policies
    assert "publish_data_release.yml" not in policies

    policy_workflow = yaml.safe_load(policies)
    assert set(policy_workflow["jobs"]) == {"refresh"}
    upload = next(
        step
        for step in policy_workflow["jobs"]["refresh"]["steps"]
        if step["name"] == "Upload supplemental manual policy review artifacts"
    )
    assert upload["with"]["name"] == "supplemental-manual-policy-review"
    assert upload["with"]["if-no-files-found"] == "warn"
    assert upload["with"]["retention-days"] == 7
    assert set(upload["with"]["path"].splitlines()) == {
        "data/processed/policy_documents.parquet",
        "data/processed/policy_facts.parquet",
        "data/processed/policy_review_queue.parquet",
        "data/review/policy_review_decisions.parquet",
        "outputs/reports/policy/summary.json",
        "outputs/reports/policy/errors.json",
        "outputs/reports/policy/evaluation.json",
    }

    assert "data/processed/policy_*" in government
    assert "data/cache/policy_discovery/*" in government
    assert "data/cache/policy_extraction/*" in government
    assert "OPENAI_API_KEY" not in government
