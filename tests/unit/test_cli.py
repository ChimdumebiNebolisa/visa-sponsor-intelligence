"""Tests for command-line startup and safe output."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sponsor_intel import __version__
from sponsor_intel.cli import app

runner = CliRunner()


def test_cli_help_starts() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "evidence-first sponsorship intelligence explorer" in result.stdout
    assert "entities" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_config_command_redacts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "never-print-me"
    monkeypatch.setenv("OPENAI_API_KEY", secret_value)

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert secret_value not in result.stdout
    assert '"openai_api_key_configured": true' in result.stdout


def test_entity_gold_validation_command_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "gold-validation.json"

    result = runner.invoke(
        app,
        ["entities", "validate-gold", "--report", str(report_path)],
    )

    assert result.exit_code == 0
    assert '"passed": true' in result.stdout
    assert report_path.is_file()


def test_role_gold_validation_command_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "role-gold-validation.json"

    result = runner.invoke(
        app,
        ["roles", "validate-gold", "--report", str(report_path)],
    )

    assert result.exit_code == 0
    assert '"precision": 1.0' in result.stdout
    assert report_path.is_file()


def test_metrics_scores_and_database_command_groups_are_available() -> None:
    metrics = runner.invoke(app, ["metrics", "--help"])
    scores = runner.invoke(app, ["scores", "--help"])
    database = runner.invoke(app, ["db", "--help"])

    assert metrics.exit_code == 0
    assert "build" in metrics.stdout
    assert scores.exit_code == 0
    assert "build" in scores.stdout
    assert database.exit_code == 0
    assert "build" in database.stdout


def test_app_command_launches_streamlit_through_python_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], *, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, check=check, env=env)
        return subprocess.CompletedProcess(command, returncode=0)

    monkeypatch.setattr("sponsor_intel.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["app"])

    assert result.exit_code == 0
    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:4] == ["-m", "streamlit", "run"]
    assert Path(command[4]).name == "Home.py"
    assert captured["check"] is False
