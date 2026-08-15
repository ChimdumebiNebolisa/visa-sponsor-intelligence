"""Typer command-line interface for local project workflows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from sponsor_intel import __version__
from sponsor_intel.config import load_settings
from sponsor_intel.database.builder import DuckDBBuilder
from sponsor_intel.entity_resolution.models import EntityResolutionConfig
from sponsor_intel.entity_resolution.pipeline import EntityResolutionPipeline
from sponsor_intel.entity_resolution.validation import validate_gold_dataset
from sponsor_intel.evidence.pipeline import Phase6Pipeline
from sponsor_intel.logging import configure_logging
from sponsor_intel.metrics.pipeline import MetricsPipeline
from sponsor_intel.policy.discovery import PolicySeedRegistry
from sponsor_intel.policy.evaluation import evaluate_policy_benchmark
from sponsor_intel.policy.pipeline import PolicyPipeline, create_exact_fact_review_decisions
from sponsor_intel.policy.ranking import build_policy_candidates
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
scores_app = typer.Typer(help="Build versioned evidence-strength scores and coverage.")
database_app = typer.Typer(help="Build the DuckDB presentation database.")
evidence_app = typer.Typer(help="Build positive OPT and prioritized E-Verify evidence.")
policy_app = typer.Typer(help="Rank, extract, review, and evaluate institution policy evidence.")
app.add_typer(sources_app, name="sources")
app.add_typer(entities_app, name="entities")
app.add_typer(roles_app, name="roles")
app.add_typer(metrics_app, name="metrics")
app.add_typer(scores_app, name="scores")
app.add_typer(database_app, name="db")
app.add_typer(evidence_app, name="evidence")
app.add_typer(policy_app, name="policy")


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
    """Build employer and institution metrics plus configured evidence scores."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    summary = MetricsPipeline(data_root=settings.data_dir).build()
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@scores_app.command("build")
def scores_build(
    scoring_config: Annotated[
        Path,
        typer.Option("--scoring-config", help="Versioned evidence-score formula YAML."),
    ] = Path("configs/scoring.yaml"),
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Rebuild deterministic nullable scores and their source metrics."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    summary = MetricsPipeline(
        data_root=settings.data_dir,
        scoring_config_path=scoring_config,
    ).build()
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


@evidence_app.command("build")
def evidence_build(
    everify_limit: Annotated[
        int,
        typer.Option(
            "--everify-limit",
            min=0,
            help="Maximum prioritized E-Verify lookups; zero performs no live lookups.",
        ),
    ] = 0,
    force_opt_download: Annotated[
        bool,
        typer.Option(
            "--force-opt-download",
            help="Re-fetch the current official ICE report instead of using its immutable cache.",
        ),
    ] = False,
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Build Phase 6 evidence, enriched metrics, and the presentation database."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    summary = Phase6Pipeline(
        data_root=settings.data_dir,
        database_path=settings.db_path,
    ).build(
        everify_limit=everify_limit,
        force_opt_download=force_opt_download,
    )
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@policy_app.command("candidates")
def policy_candidates(
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Generate the configured 150-250 policy-enrichment candidate ranking."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    registry = PolicySeedRegistry.from_yaml(Path("configs/policy_sources.yaml"))
    candidates = build_policy_candidates(
        data_root=settings.data_dir,
        limit=settings.policy_candidate_limit,
        manual_priorities=registry.manual_priorities,
    )
    typer.echo(
        json.dumps(
            {
                "candidate_count": candidates.height,
                "path": str(settings.data_dir / "processed" / "policy_candidates.parquet"),
            },
            indent=2,
            sort_keys=True,
        )
    )


@policy_app.command("build")
def policy_build(
    enrichment_limit: Annotated[
        int,
        typer.Option(
            "--enrichment-limit",
            min=1,
            max=250,
            help="Maximum ranked candidates to enrich in this run.",
        ),
    ] = 200,
    documents_per_institution: Annotated[
        int,
        typer.Option(
            "--documents-per-institution",
            min=1,
            max=5,
            help="Maximum official documents fetched and extracted for each institution.",
        ),
    ] = 1,
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Run domain-restricted discovery and schema-constrained policy extraction."""

    settings = load_settings(config_file)
    configure_logging(settings.log_level)
    if settings.openai_policy_model is None:
        raise typer.BadParameter("OPENAI_POLICY_MODEL is required")
    if settings.openai_api_key is None:
        raise typer.BadParameter("OPENAI_API_KEY is required")
    summary = PolicyPipeline(
        model=settings.openai_policy_model,
        api_key=settings.openai_api_key.get_secret_value(),
        data_root=settings.data_dir,
    ).build(
        candidate_limit=settings.policy_candidate_limit,
        enrichment_limit=enrichment_limit,
        documents_per_institution=documents_per_institution,
        progress=typer.echo,
    )
    typer.echo(json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True))


@policy_app.command("review-exact")
def policy_review_exact(
    reviewer_id: Annotated[
        str,
        typer.Option("--reviewer-id", help="Auditable identifier for the reviewing operator."),
    ],
    reviewer_note: Annotated[
        str,
        typer.Option("--note", help="Review note describing the evidence checks performed."),
    ],
    fact_ids_path: Annotated[
        Path,
        typer.Option(
            "--fact-ids",
            help="Newline-delimited IDs for facts the operator explicitly reviewed.",
        ),
    ],
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Accept exact affirmative facts after an explicit operator evidence review."""

    settings = load_settings(config_file)
    if not fact_ids_path.is_file():
        raise typer.BadParameter(f"Reviewed fact ID file is unavailable: {fact_ids_path}")
    fact_ids = {
        line.strip()
        for line in fact_ids_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    decisions = create_exact_fact_review_decisions(
        data_root=settings.data_dir,
        reviewer_id=reviewer_id,
        reviewer_note=reviewer_note,
        fact_ids=fact_ids,
    )
    typer.echo(
        json.dumps(
            {
                "reviewed_fact_count": decisions.height,
                "reviewed_institution_count": pl.read_parquet(
                    settings.data_dir / "processed" / "policy_facts.parquet"
                )
                .filter(pl.col("human_review_status") == "REVIEWED_ACCEPTED")["institution_id"]
                .n_unique(),
            },
            indent=2,
            sort_keys=True,
        )
    )


@policy_app.command("evaluate")
def policy_evaluate(
    benchmark_path: Annotated[
        Path,
        typer.Option("--benchmark", help="Manually reviewed JSONL benchmark."),
    ] = Path("tests/fixtures/policy_extraction_benchmark.jsonl"),
    report_path: Annotated[
        Path,
        typer.Option("--report", help="Machine-readable evaluation report path."),
    ] = Path("outputs/reports/policy/evaluation.json"),
    config_file: Annotated[
        Path | None,
        typer.Option("--config", help="Optional application configuration path."),
    ] = None,
) -> None:
    """Measure reviewed extraction precision and citation acceptance gates."""

    settings = load_settings(config_file)
    result = evaluate_policy_benchmark(
        facts_path=settings.data_dir / "processed" / "policy_facts.parquet",
        documents_path=settings.data_dir / "processed" / "policy_documents.parquet",
        benchmark_path=benchmark_path,
        report_path=report_path,
    )
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    if not result.passed:
        raise typer.Exit(1)


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
