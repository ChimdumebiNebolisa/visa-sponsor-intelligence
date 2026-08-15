"""Manual benchmark evaluation for reviewed policy facts."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from sponsor_intel.policy.extraction import normalize_evidence
from sponsor_intel.policy.models import PolicyBenchmarkAnnotation, PolicyEvaluationResult
from sponsor_intel.sources.manifests import write_json_atomic


def _load_benchmark(path: Path) -> list[PolicyBenchmarkAnnotation]:
    if not path.is_file():
        raise ValueError(f"Policy benchmark is unavailable: {path}")
    annotations: list[PolicyBenchmarkAnnotation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            annotations.append(PolicyBenchmarkAnnotation.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"Invalid policy benchmark row {line_number}: {error}") from error
    if len({item.institution_id for item in annotations}) < 30:
        raise ValueError("Policy benchmark must contain at least 30 unique institutions")
    if len(
        {(item.institution_id, item.source_url, item.fact_type.value) for item in annotations}
    ) != len(annotations):
        raise ValueError("Policy benchmark annotations must have unique evidence keys")
    return annotations


def evaluate_policy_benchmark(
    *,
    facts_path: Path,
    documents_path: Path,
    benchmark_path: Path,
    report_path: Path = Path("outputs/reports/policy/evaluation.json"),
) -> PolicyEvaluationResult:
    """Measure reviewed-fact precision, coverage, and citation invariants."""

    annotations = _load_benchmark(benchmark_path)
    if not facts_path.is_file():
        raise ValueError(f"Policy facts are unavailable: {facts_path}")
    if not documents_path.is_file():
        raise ValueError(f"Policy documents are unavailable: {documents_path}")
    facts = pl.read_parquet(facts_path)
    documents = pl.read_parquet(documents_path).select(
        "policy_document_id",
        "institution_id",
        pl.col("official_domain").alias("document_official_domain"),
    )
    accepted = facts.filter(
        (pl.col("human_review_status") == "REVIEWED_ACCEPTED")
        & pl.col("is_current")
        & pl.col("valid_to").is_null()
    ).join(
        documents,
        on=["policy_document_id", "institution_id"],
        how="left",
        validate="m:1",
    )
    accepted_count = accepted.height
    official_flags: list[bool] = []
    for row in accepted.select("source_url", "document_official_domain").iter_rows(named=True):
        parsed = urlparse(str(row["source_url"]))
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        domain = str(row["document_official_domain"] or "").casefold().rstrip(".")
        official_flags.append(
            parsed.scheme == "https"
            and bool(domain)
            and (hostname == domain or hostname.endswith(f".{domain}"))
        )
    accepted = accepted.with_columns(
        pl.Series("_official_source_url", official_flags, dtype=pl.Boolean)
    )
    official_url_count = accepted.filter(pl.col("_official_source_url")).height
    excerpt_count = (
        accepted.filter(pl.col("supporting_excerpt").str.strip_chars().ne("")).height
        if accepted_count
        else 0
    )
    unsupported = (
        accepted.filter(
            ~pl.col("exact_excerpt_verified")
            | ~pl.col("_official_source_url")
            | pl.col("supporting_excerpt").str.strip_chars().eq("")
        ).height
        if accepted_count
        else 0
    )
    by_key = {
        (row["institution_id"], row["source_url"], row["fact_type"]): row
        for row in accepted.iter_rows(named=True)
    }
    evaluated = 0
    correct = 0
    covered = 0
    for annotation in annotations:
        row = by_key.get(
            (
                annotation.institution_id,
                annotation.source_url,
                annotation.fact_type.value,
            )
        )
        if row is None:
            continue
        covered += 1
        evaluated += 1
        value_matches = row["fact_value"] == annotation.expected_value.value
        normalized_annotation = normalize_evidence(annotation.supporting_excerpt)
        normalized_prediction = normalize_evidence(str(row["supporting_excerpt"]))
        excerpt_matches = (
            bool(normalized_annotation) and normalized_annotation in normalized_prediction
        )
        if value_matches and excerpt_matches and bool(row["exact_excerpt_verified"]):
            correct += 1
    precision = correct / evaluated if evaluated else 0.0
    coverage = covered / len(annotations) if annotations else 0.0
    url_rate = official_url_count / accepted_count if accepted_count else 0.0
    excerpt_rate = excerpt_count / accepted_count if accepted_count else 0.0
    passed = (
        len({item.institution_id for item in annotations}) >= 30
        and precision >= 0.95
        and coverage == 1.0
        and url_rate == 1.0
        and excerpt_rate == 1.0
        and unsupported == 0
    )
    result = PolicyEvaluationResult(
        benchmark_institution_count=len({item.institution_id for item in annotations}),
        annotation_count=len(annotations),
        evaluated_prediction_count=evaluated,
        correct_prediction_count=correct,
        factual_precision=precision,
        benchmark_coverage=coverage,
        accepted_official_url_rate=url_rate,
        accepted_excerpt_rate=excerpt_rate,
        unsupported_accepted_fact_count=unsupported,
        passed=passed,
        report_path=report_path,
    )
    write_json_atomic(report_path, result.model_dump(mode="json"))
    return result
