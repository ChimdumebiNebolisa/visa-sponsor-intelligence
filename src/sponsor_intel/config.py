"""Typed, non-secret application configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("configs/settings.yaml")


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class DeploymentMode(StrEnum):
    """Supported application data-loading modes."""

    LOCAL = "local"
    RELEASE = "release"


class Settings(BaseModel):
    """Validated settings after applying YAML, environment, and CLI precedence."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    data_dir: Path = Path("data")
    db_path: Path = Path("db/immigration.duckdb")
    log_level: LogLevel = LogLevel.INFO
    policy_candidate_limit: int = Field(default=200, ge=150, le=250)
    openai_policy_model: str | None = None
    openai_api_key: SecretStr | None = None
    deployment_mode: DeploymentMode = DeploymentMode.LOCAL
    require_data: bool = False
    github_repository: str = "ChimdumebiNebolisa/visa-sponsor-intelligence"
    release_tag: str = "latest"
    release_cache_dir: Path = Path("data/deployment-cache")
    github_release_read_token: SecretStr | None = None

    @field_validator(
        "openai_policy_model",
        "openai_api_key",
        "github_release_read_token",
        mode="before",
    )
    @classmethod
    def blank_optional_values_are_unset(cls, value: object) -> object:
        """Treat blank optional environment values as absent."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("github_repository")
    @classmethod
    def repository_is_an_owner_name_pair(cls, value: str) -> str:
        """Keep the GitHub API target fixed to one syntactically safe repository."""

        normalized = value.strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", normalized) is None:
            raise ValueError("GitHub repository must use the owner/name format")
        return normalized

    @field_validator("release_tag")
    @classmethod
    def release_tag_is_safe(cls, value: str) -> str:
        """Reject tags that could escape cache paths or alter API routing."""

        normalized = value.strip()
        if normalized != "latest" and re.fullmatch(r"[A-Za-z0-9._-]+", normalized) is None:
            raise ValueError("Release tag contains unsupported characters")
        return normalized

    @model_validator(mode="after")
    def release_mode_fails_closed(self) -> Settings:
        """Require an authenticated, non-empty data path in hosted release mode."""

        if self.deployment_mode is DeploymentMode.RELEASE:
            if not self.require_data:
                raise ValueError("Release deployment mode requires SPONSOR_INTEL_REQUIRE_DATA=true")
            if self.github_release_read_token is None:
                raise ValueError("Release deployment mode requires GITHUB_RELEASE_READ_TOKEN")
        return self

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return settings that are safe to display or log."""

        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "log_level": self.log_level.value,
            "policy_candidate_limit": self.policy_candidate_limit,
            "openai_policy_model_configured": self.openai_policy_model is not None,
            "openai_api_key_configured": self.openai_api_key is not None,
            "deployment_mode": self.deployment_mode.value,
            "require_data": self.require_data,
            "github_repository": self.github_repository,
            "release_tag": self.release_tag,
            "release_cache_dir": str(self.release_cache_dir),
            "github_release_read_token_configured": (self.github_release_read_token is not None),
        }


_ENVIRONMENT_FIELDS = {
    "SPONSOR_INTEL_DATA_DIR": "data_dir",
    "SPONSOR_INTEL_DB_PATH": "db_path",
    "SPONSOR_INTEL_LOG_LEVEL": "log_level",
    "POLICY_CANDIDATE_LIMIT": "policy_candidate_limit",
    "OPENAI_POLICY_MODEL": "openai_policy_model",
    "OPENAI_API_KEY": "openai_api_key",
    "SPONSOR_INTEL_DEPLOYMENT_MODE": "deployment_mode",
    "SPONSOR_INTEL_REQUIRE_DATA": "require_data",
    "SPONSOR_INTEL_GITHUB_REPOSITORY": "github_repository",
    "SPONSOR_INTEL_RELEASE_TAG": "release_tag",
    "SPONSOR_INTEL_RELEASE_CACHE_DIR": "release_cache_dir",
    "GITHUB_RELEASE_READ_TOKEN": "github_release_read_token",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"Configuration path is not a file: {path}")

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise ValueError(f"Configuration must be a string-keyed YAML mapping: {path}")

    values = dict(loaded)
    for field_name, environment_name in (
        ("openai_api_key", "OPENAI_API_KEY"),
        ("github_release_read_token", "GITHUB_RELEASE_READ_TOKEN"),
    ):
        yaml_secret = values.pop(field_name, None)
        if yaml_secret not in (None, ""):
            raise ValueError(
                f"{environment_name} must be supplied through the environment, not YAML"
            )
    return values


def load_settings(
    config_path: Path | None = None,
    *,
    overrides: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings with CLI overrides > environment > YAML > safe defaults."""

    selected_path = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    values = _read_yaml(selected_path)

    if environ is None:
        dotenv_environment: dict[str, str] = {}
        for dotenv_path in (Path(".env"), Path(".env.local")):
            if dotenv_path.is_file():
                dotenv_environment.update(
                    {
                        key: value
                        for key, value in dotenv_values(dotenv_path).items()
                        if isinstance(value, str)
                    }
                )
        source_environment: Mapping[str, str] = dotenv_environment | dict(os.environ)
    else:
        source_environment = environ
    normalized_environment = {key.upper(): value for key, value in source_environment.items()}
    for environment_name, field_name in _ENVIRONMENT_FIELDS.items():
        if environment_name in normalized_environment:
            values[field_name] = normalized_environment[environment_name]

    if overrides is not None:
        values.update({key: value for key, value in overrides.items() if value is not None})

    return Settings.model_validate(values)
