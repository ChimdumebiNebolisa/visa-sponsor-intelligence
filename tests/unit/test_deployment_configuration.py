"""Deployment configuration and dependency-boundary coverage."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
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
    assert '"metric_version": "scored_metrics_v2"' in workflow
    assert '"score_version": "evidence_scores_v2_2026_08"' in workflow
    assert "Required runtime release assets are missing" in workflow


@pytest.mark.parametrize(
    "workflow_name",
    ["refresh_government_data.yml", "refresh_policies.yml"],
)
def test_refresh_workflows_block_public_release_artifact_upload(workflow_name: str) -> None:
    workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")

    guard = "Refuse release artifact upload from a public repository"
    upload = "Upload quality-approved release input"
    assert guard in workflow
    assert "--json visibility --jq .visibility" in workflow
    assert workflow.index(guard) < workflow.index(upload)
