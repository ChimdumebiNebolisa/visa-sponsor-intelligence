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
from sponsor_intel.database.builder import DuckDBBuilder
from sponsor_intel.entity_resolution.models import EntityResolutionConfig
from sponsor_intel.entity_resolution.pipeline import EntityResolutionPipeline
from sponsor_intel.entity_resolution.validation import validate_gold_dataset
from sponsor_intel.logging import configure_logging
from sponsor_intel.metrics.pipeline import MetricsPipeline
from sponsor_intel.role_classification.models import RoleTaxonomyConfig
from sponsor_intel.role_classification.pipeline import RoleClassificationPipeline
from sponsor_intel.role_classification.validation import validate_role_gold
from sponsor_intel.sources.pipeline import IngestionPipeline
from sponsor_intel.sources.registry import DEFAULT_SOURCE_REGISTRY_PATH, SourceRegistry

app = typer.Typer(
    name="sponsor-intel",
    help="Private, evidence-first sponsorship intelligence explorer.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
sources_app = typer.Typer(help="Inspect and discover authoritative source artifacts.")
entities_app = typer.Typer(help="Build and validate legal-entity resolution tables.")
roles_app = typer.Typer(help="Build and validate deterministic role classifications.")
metrics_app = typer.Typer(help="Build processed employer and institution metrics.")
database_app = typer.Typer(help="Build the DuckDB presentation database.")
app.add_typer(sources_app, name="sources")
app.add_typer(entities_app, name="entities")
app.add_typer(roles_app, name="roles")
app.add_typer(metrics_app, name="metrics")
app.add_typer(database_app, name="db")


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


@sources_app.command("list")
def sources_list(
    registry_path: Annotated[
        Path,
        typer.Option("--registry", help="Typed source-registry YAML path."),
    ] = DEFAULT_SOURCE_REGISTRY_PATH,
) -> None:
    """List configured authoritative sources."""

    registry = SourceRegistry.from_yaml(registry_path)
    payload = [
        {
            "id": source.id,
            "authority": source.authority,
            "landing_page": source.landing_page,
            "minimum_fiscal_year": source.minimum_fiscal_year,
            "refresh_cadence": source.refresh_cadence,
        }
        for source in registry.list()
    ]
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@sources_app.command("discover")
def sources_discover(
    source: Annotated[str, typer.Option("--source", help="Configured source ID.")],
    from_fy: Annotated[int, typer.Option("--from-fy", min=2022)] = 2022,
    registry_path: Annotated[
        Path,
        typer.Option("--registry", help="Typed source-registry YAML path."),
    ] = DEFAULT_SOURCE_REGISTRY_PATH,
) -> None:
    """Discover canonical official artifacts without downloading them."""

    registry = SourceRegistry.from_yaml(registry_path)
    pipeline = IngestionPipeline(registry)
    report_path, report = pipeline.discover(source, from_fiscal_year=from_fy)
    payload = report.model_dump(mode="json")
    payload["report_path"] = str(report_path)
    payload["selected_candidates"] = [
        candidate.model_dump(mode="json") | {"candidate_id": candidate.candidate_id}
        for candidate in report.selected
    ]
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command("ingest")
def ingest_command(
    source: Annotated[str, typer.Option("--source", help="Configured source ID.")],
    from_fy: Annotated[int, typer.Option("--from-fy", min=2022)] = 2022,
    force_download: Annotated[
        bool,
        typer.Option("--force-download", help="Re-fetch even when a validated artifact exists."),
    ] = False,
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional YAML configuration path."),
    ] = None,
    registry_path: Annotated[
        Path,
        typer.Option("--registry", help="Typed source-registry YAML path."),
    ] = DEFAULT_SOURCE_REGISTRY_PATH,
) -> None:
    """Ingest one authoritative source from the requested fiscal year onward."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    registry = SourceRegistry.from_yaml(registry_path)
    pipeline = IngestionPipeline(registry, data_root=settings.data_dir)
    summary = pipeline.ingest(
        source,
        from_fiscal_year=from_fy,
        force_download=force_download,
    )
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@entities_app.command("build")
def entities_build(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
    registry_path: Annotated[
        Path,
        typer.Option("--registry", help="Typed source-registry YAML path."),
    ] = DEFAULT_SOURCE_REGISTRY_PATH,
    resolution_config: Annotated[
        Path,
        typer.Option("--resolution-config", help="Entity-resolution thresholds YAML."),
    ] = Path("configs/entity_resolution.yaml"),
    overrides: Annotated[
        Path,
        typer.Option("--overrides", help="Audited entity decisions YAML."),
    ] = Path("configs/entity_overrides.yaml"),
) -> None:
    """Build legal entities, parents, aliases, and the review queue."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    registry = SourceRegistry.from_yaml(registry_path)
    pipeline = EntityResolutionPipeline(
        registry,
        data_root=settings.data_dir,
        config_path=resolution_config,
        overrides_path=overrides,
    )
    summary = pipeline.build()
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@entities_app.command("validate-gold")
def entities_validate_gold(
    gold_path: Annotated[
        Path,
        typer.Option("--gold", help="Reviewed entity-pair gold CSV."),
    ] = Path("tests/fixtures/entity_resolution_gold.csv"),
    resolution_config: Annotated[
        Path,
        typer.Option("--resolution-config", help="Entity-resolution thresholds YAML."),
    ] = Path("configs/entity_resolution.yaml"),
    report_path: Annotated[
        Path,
        typer.Option("--report", help="Machine-readable validation report path."),
    ] = Path("outputs/reports/entities/gold_validation.json"),
) -> None:
    """Validate auto-match precision and parent/legal separation."""

    config = EntityResolutionConfig.from_yaml(resolution_config)
    result = validate_gold_dataset(gold_path, config, report_path=report_path)
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if not result.passed:
        raise typer.Exit(1)


@roles_app.command("build")
def roles_build(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
    taxonomy_path: Annotated[
        Path,
        typer.Option("--taxonomy", help="Versioned role-taxonomy YAML path."),
    ] = Path("configs/role_taxonomy.yaml"),
) -> None:
    """Classify every resolved DOL record and build the review queue."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    pipeline = RoleClassificationPipeline(
        data_root=settings.data_dir,
        taxonomy_path=taxonomy_path,
    )
    summary = pipeline.build()
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@roles_app.command("validate-gold")
def roles_validate_gold(
    gold_path: Annotated[
        Path,
        typer.Option("--gold", help="Manually labeled role-classification CSV."),
    ] = Path("tests/fixtures/role_classification_gold.csv"),
    taxonomy_path: Annotated[
        Path,
        typer.Option("--taxonomy", help="Versioned role-taxonomy YAML path."),
    ] = Path("configs/role_taxonomy.yaml"),
    report_path: Annotated[
        Path,
        typer.Option("--report", help="Machine-readable validation report path."),
    ] = Path("outputs/reports/roles/gold_validation.json"),
) -> None:
    """Measure role precision, recall, family accuracy, and review routing."""

    config = RoleTaxonomyConfig.from_yaml(taxonomy_path)
    result = validate_role_gold(gold_path, config, report_path=report_path)
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if not result.passed:
        raise typer.Exit(1)


@metrics_app.command("build")
def metrics_build(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Build raw employer and institution metrics from resolved evidence."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    summary = MetricsPipeline(data_root=settings.data_dir).build()
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@database_app.command("build")
def database_build(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Materialize processed tables and presentation views in DuckDB."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    summary = DuckDBBuilder(
        data_root=settings.data_dir,
        database_path=settings.db_path,
    ).build()
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


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
