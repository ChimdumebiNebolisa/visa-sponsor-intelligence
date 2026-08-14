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
