"""Typed, non-secret application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_CONFIG_PATH = Path("configs/settings.yaml")


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseModel):
    """Validated settings after applying YAML, environment, and CLI precedence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path = Path("data")
    db_path: Path = Path("db/immigration.duckdb")
    log_level: LogLevel = LogLevel.INFO
    policy_candidate_limit: int = Field(default=200, ge=150, le=250)
    openai_policy_model: str | None = None
    openai_api_key: SecretStr | None = None

    @field_validator("openai_policy_model", "openai_api_key", mode="before")
    @classmethod
    def blank_optional_values_are_unset(cls, value: object) -> object:
        """Treat blank optional environment values as absent."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return settings that are safe to display or log."""

        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "log_level": self.log_level.value,
            "policy_candidate_limit": self.policy_candidate_limit,
            "openai_policy_model_configured": self.openai_policy_model is not None,
            "openai_api_key_configured": self.openai_api_key is not None,
        }


_ENVIRONMENT_FIELDS = {
    "SPONSOR_INTEL_DATA_DIR": "data_dir",
    "SPONSOR_INTEL_DB_PATH": "db_path",
    "SPONSOR_INTEL_LOG_LEVEL": "log_level",
    "POLICY_CANDIDATE_LIMIT": "policy_candidate_limit",
    "OPENAI_POLICY_MODEL": "openai_policy_model",
    "OPENAI_API_KEY": "openai_api_key",
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
    yaml_api_key = values.pop("openai_api_key", None)
    if yaml_api_key not in (None, ""):
        raise ValueError("OPENAI_API_KEY must be supplied through the environment, not YAML")
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
