"""Tests for typed configuration loading and redaction."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from sponsor_intel.config import LogLevel, load_settings


def test_load_settings_uses_safe_defaults_when_yaml_is_missing(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.yaml", environ={})

    assert settings.data_dir == Path("data")
    assert settings.db_path == Path("db/immigration.duckdb")
    assert settings.log_level is LogLevel.INFO
    assert settings.policy_candidate_limit == 200


def test_precedence_is_cli_then_environment_then_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        "data_dir: yaml-data\nlog_level: WARNING\npolicy_candidate_limit: 175\n",
        encoding="utf-8",
    )

    settings = load_settings(
        config_path,
        environ={
            "SPONSOR_INTEL_DATA_DIR": "environment-data",
            "POLICY_CANDIDATE_LIMIT": "190",
        },
        overrides={"policy_candidate_limit": 225},
    )

    assert settings.data_dir == Path("environment-data")
    assert settings.log_level is LogLevel.WARNING
    assert settings.policy_candidate_limit == 225


def test_policy_candidate_limit_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_settings(
            tmp_path / "missing.yaml",
            environ={"POLICY_CANDIDATE_LIMIT": "251"},
        )


def test_yaml_api_key_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("openai_api_key: do-not-store-secrets-here\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be supplied through the environment"):
        load_settings(config_path, environ={})


def test_safe_summary_never_contains_secret_values(tmp_path: Path) -> None:
    secret_value = "do-not-display-this-key"
    settings = load_settings(
        tmp_path / "missing.yaml",
        environ={"OPENAI_API_KEY": secret_value, "OPENAI_POLICY_MODEL": "configured-model"},
    )

    rendered = str(settings.safe_summary())
    assert secret_value not in rendered
    assert settings.safe_summary()["openai_api_key_configured"] is True
