"""Typer command-line interface for local project workflows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from sponsor_intel import __version__
from sponsor_intel.config import load_settings
from sponsor_intel.logging import configure_logging

app = typer.Typer(
    name="sponsor-intel",
    help="Private, evidence-first sponsorship intelligence explorer.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


@app.command()
def version() -> None:
    """Show the installed package version."""

    typer.echo(__version__)


@app.command("config")
def config_command(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional YAML configuration path."),
    ] = None,
) -> None:
    """Show the effective non-secret configuration."""

    settings = load_settings(config_file)
    typer.echo(json.dumps(settings.safe_summary(), indent=2, sort_keys=True))


@app.command("app")
def app_command(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional YAML configuration path."),
    ] = None,
) -> None:
    """Start the local Streamlit application."""

    settings = load_settings(config_file)
    logger = configure_logging(settings.log_level)
    project_root = Path(__file__).resolve().parents[2]
    streamlit_entrypoint = project_root / "app" / "Home.py"
    if not streamlit_entrypoint.is_file():
        raise typer.BadParameter(f"Streamlit entrypoint is missing: {streamlit_entrypoint}")

    child_environment = os.environ.copy()
    child_environment["SPONSOR_INTEL_DATA_DIR"] = str(settings.data_dir)
    child_environment["SPONSOR_INTEL_DB_PATH"] = str(settings.db_path)
    child_environment["SPONSOR_INTEL_LOG_LEVEL"] = settings.log_level.value
    logger.info(
        "Starting Streamlit application",
        extra={"build_id": "foundation", "stage": "app_startup", "status": "starting"},
    )
    completed = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(streamlit_entrypoint)],
        check=False,
        env=child_environment,
    )
    raise typer.Exit(completed.returncode)


def main() -> None:
    """Run the Typer application."""

    app()
