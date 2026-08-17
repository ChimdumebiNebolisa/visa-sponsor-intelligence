"""End-to-end Phase 7 policy evidence orchestration and review persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
from openai import OpenAI

from sponsor_intel.evidence.io import write_parquet_atomic
from sponsor_intel.policy.discovery import (
    OpenAIPolicyDiscoverer,
    PolicySeedRegistry,
    ResponsesClient,
    discover_from_sitemaps,
)
from sponsor_intel.policy.extraction import OpenAIPolicyExtractor, policy_facts_from_extraction
from sponsor_intel.policy.fetcher import PolicyDocumentFetcher
from sponsor_intel.policy.models import (
    DiscoveredPolicyDocument,
    FactType,
    FactValue,
    ParseStatus,
    PolicyBuildSummary,
    PolicyCandidate,
    PolicyDocument,
    PolicyFact,
    ReviewStatus,
)
from sponsor_intel.policy.ranking import build_policy_candidates

FAILURE_BACKOFF_HOURS = 24


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as target:
            json.dump(payload, target, indent=2, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _load_recent_source_failures(path: Path) -> set[tuple[str, str]]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(payload["generated_at"])
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        if generated_at < datetime.now(UTC) - timedelta(hours=FAILURE_BACKOFF_HOURS):
            return set()
        errors = payload["errors"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(errors, list):
        return set()
    return {
        (str(error["institution_id"]), str(error["source_url"]))
        for error in errors
        if isinstance(error, dict)
        and isinstance(error.get("institution_id"), str)
        and isinstance(error.get("source_url"), str)
    }


def _frame_from_models(values: list[Any], *, schema: dict[str, Any]) -> pl.DataFrame:
    if not values:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame([value.model_dump(mode="json") for value in values])


DOCUMENT_SCHEMA: dict[str, Any] = {
    "policy_document_id": pl.String,
    "institution_id": pl.String,
    "document_type": pl.String,
    "title": pl.String,
    "url": pl.String,
    "official_domain": pl.String,
    "retrieved_at": pl.String,
    "http_status": pl.Int64,
    "content_type": pl.String,
    "content_sha256": pl.String,
    "text_sha256": pl.String,
    "published_or_updated_date": pl.String,
    "raw_path": pl.String,
    "parsed_text_path": pl.String,
    "is_current": pl.Boolean,
    "parse_status": pl.String,
    "discovery_method": pl.String,
    "suspicious_text": pl.Boolean,
    "cache_hit": pl.Boolean,
}

FACT_SCHEMA: dict[str, Any] = {
    "policy_fact_id": pl.String,
    "institution_id": pl.String,
    "policy_document_id": pl.String,
    "fact_type": pl.String,
    "fact_value": pl.String,
    "qualifier": pl.String,
    "supporting_excerpt": pl.String,
    "section_or_page": pl.String,
    "source_url": pl.String,
    "retrieved_at": pl.String,
    "extractor_version": pl.String,
    "model_name": pl.String,
    "model_response_id": pl.String,
    "confidence": pl.Float64,
    "exact_excerpt_verified": pl.Boolean,
    "human_review_status": pl.String,
    "reviewer_note": pl.String,
    "contradiction_group_id": pl.String,
    "valid_from": pl.String,
    "valid_to": pl.String,
    "is_current": pl.Boolean,
}


def _merge_document_history(
    current: pl.DataFrame,
    existing: pl.DataFrame | None,
) -> pl.DataFrame:
    if existing is None or existing.is_empty():
        return current
    current_ids = current["policy_document_id"].to_list()
    retained = existing.filter(~pl.col("policy_document_id").is_in(current_ids))
    return pl.concat([retained, current], how="diagonal_relaxed").sort(
        ["institution_id", "retrieved_at", "policy_document_id"]
    )


def _merge_fact_history(
    current: pl.DataFrame,
    existing: pl.DataFrame | None,
    *,
    superseded_at: str,
) -> pl.DataFrame:
    if existing is None or existing.is_empty():
        return current
    current_ids = current["policy_fact_id"].to_list()
    retained = existing.filter(~pl.col("policy_fact_id").is_in(current_ids))
    if not current.is_empty():
        replacement_keys = (
            current.select("institution_id", "fact_type")
            .unique()
            .with_columns(pl.lit(True).alias("_has_replacement"))
        )
        retained = (
            retained.join(replacement_keys, on=["institution_id", "fact_type"], how="left")
            .with_columns(
                pl.when(pl.col("_has_replacement").fill_null(False))
                .then(pl.coalesce("valid_to", pl.lit(superseded_at)))
                .otherwise(pl.col("valid_to"))
                .alias("valid_to"),
                pl.when(pl.col("_has_replacement").fill_null(False))
                .then(pl.lit(False))
                .otherwise(pl.col("is_current"))
                .alias("is_current"),
            )
            .drop("_has_replacement")
        )
    return pl.concat([retained, current], how="diagonal_relaxed").sort(
        ["institution_id", "fact_type", "valid_from", "policy_fact_id"]
    )


def _load_review_decisions(path: Path) -> pl.DataFrame | None:
    if not path.is_file():
        return None
    decisions = pl.read_parquet(path)
    required = {
        "policy_fact_id",
        "human_review_status",
        "reviewer_note",
        "reviewed_at",
        "reviewer_id",
    }
    if missing := required - set(decisions.columns):
        raise ValueError(f"Policy review decisions are missing columns: {sorted(missing)}")
    if decisions["policy_fact_id"].n_unique() != decisions.height:
        raise ValueError("Policy review decisions must be unique by policy_fact_id")
    return decisions


def apply_review_decisions(facts: pl.DataFrame, decisions: pl.DataFrame | None) -> pl.DataFrame:
    """Overlay auditable human decisions without changing extracted evidence."""

    if decisions is None or decisions.is_empty() or facts.is_empty():
        return facts
    base = facts.drop(
        [column for column in ("reviewed_at", "reviewer_id") if column in facts.columns]
    )
    decision_columns = decisions.select(
        "policy_fact_id",
        pl.col("human_review_status").alias("_review_status"),
        pl.col("reviewer_note").alias("_review_note"),
        pl.col("current_confirmed").alias("_current_confirmed")
        if "current_confirmed" in decisions.columns
        else pl.lit(False).alias("_current_confirmed"),
        "reviewed_at",
        pl.col("reviewer_id").alias("reviewer_id")
        if "reviewer_id" in decisions.columns
        else pl.lit(None, dtype=pl.String).alias("reviewer_id"),
    )
    updated = (
        base.join(decision_columns, on="policy_fact_id", how="left", validate="1:1")
        .with_columns(
            pl.coalesce("_review_status", "human_review_status").alias("human_review_status"),
            pl.coalesce("_review_note", "reviewer_note").alias("reviewer_note"),
            pl.when(pl.col("_current_confirmed").fill_null(False) & pl.col("valid_to").is_null())
            .then(pl.lit(True))
            .otherwise(pl.col("is_current"))
            .alias("is_current"),
        )
        .drop("_review_status", "_review_note", "_current_confirmed")
    )
    missing_reviewer_provenance = pl.col("reviewer_id").cast(pl.String, strict=False).fill_null(
        ""
    ).str.strip_chars().eq("") | pl.col("reviewed_at").cast(pl.String, strict=False).fill_null(
        ""
    ).str.strip_chars().eq("")
    invalid_accepted = updated.filter(
        (pl.col("human_review_status") == ReviewStatus.REVIEWED_ACCEPTED.value)
        & pl.col("valid_to").is_null()
        & (
            ~pl.col("exact_excerpt_verified")
            | ~pl.col("source_url").str.starts_with("https://")
            | pl.col("supporting_excerpt").str.strip_chars().eq("")
            | ~pl.col("is_current")
            | missing_reviewer_provenance
        )
    )
    if not invalid_accepted.is_empty():
        raise ValueError(
            "Accepted policy decisions require current HTTPS evidence, an exact excerpt, and "
            "reviewer provenance: "
            f"{invalid_accepted['policy_fact_id'].head(10).to_list()}"
        )
    invalid_not_stated = updated.filter(
        (pl.col("human_review_status") == ReviewStatus.REVIEWED_NOT_STATED.value)
        & (
            (pl.col("fact_value") != FactValue.NOT_STATED.value)
            | ~pl.col("source_url").str.starts_with("https://")
            | ~pl.col("is_current")
            | pl.col("valid_to").is_not_null()
            | missing_reviewer_provenance
        )
    )
    if not invalid_not_stated.is_empty():
        raise ValueError(
            "Reviewed-not-stated decisions require a current NOT_STATED fact from an HTTPS "
            "source and reviewer provenance: "
            f"{invalid_not_stated['policy_fact_id'].head(10).to_list()}"
        )
    return updated


def _persist_review_decisions(
    *,
    data_root: Path,
    facts: pl.DataFrame,
    decisions: pl.DataFrame,
) -> pl.DataFrame:
    path = data_root / "review" / "policy_review_decisions.parquet"
    existing = _load_review_decisions(path)
    if existing is not None:
        decisions = (
            pl.concat([existing, decisions], how="diagonal_relaxed")
            .sort("reviewed_at")
            .unique(subset=["policy_fact_id"], keep="last", maintain_order=True)
        )
    updated = apply_review_decisions(facts, decisions)
    write_parquet_atomic(decisions, path)
    write_parquet_atomic(updated, data_root / "processed" / "policy_facts.parquet")
    write_parquet_atomic(
        updated.filter(
            (pl.col("human_review_status") == ReviewStatus.NEEDS_REVIEW.value)
            & pl.col("valid_to").is_null()
        ),
        data_root / "processed" / "policy_review_queue.parquet",
    )
    return decisions


def create_exact_fact_review_decisions(
    *,
    data_root: Path = Path("data"),
    reviewer_id: str,
    reviewer_note: str,
    fact_ids: set[str],
) -> pl.DataFrame:
    """Record an explicit review of exact, substantive, non-contradictory facts."""

    if not fact_ids:
        raise ValueError("At least one explicitly reviewed policy fact ID is required")
    facts_path = data_root / "processed" / "policy_facts.parquet"
    if not facts_path.is_file():
        raise ValueError(f"Policy facts are unavailable: {facts_path}")
    facts = pl.read_parquet(facts_path)
    eligible = facts.filter(
        pl.col("exact_excerpt_verified")
        & pl.col("fact_value").is_in(
            [FactValue.YES.value, FactValue.NO.value, FactValue.LIMITED.value]
        )
        & pl.col("contradiction_group_id").is_null()
        & pl.col("valid_to").is_null()
        & pl.col("supporting_excerpt").str.strip_chars().ne("")
        & pl.col("source_url").str.starts_with("https://")
        & ~pl.col("fact_type").is_in(
            [
                FactType.PR_GENERAL_STAFF_ELIGIBLE.value,
                FactType.CAP_EXEMPTION_EXPLICITLY_STATED.value,
            ]
        )
    ).sort(["institution_id", "confidence", "fact_type"], descending=[False, True, False])
    available_ids = set(eligible["policy_fact_id"].to_list())
    if unavailable_ids := fact_ids - available_ids:
        raise ValueError(
            f"Reviewed fact IDs are unavailable or ineligible: {sorted(unavailable_ids)[:10]}"
        )
    eligible = eligible.filter(pl.col("policy_fact_id").is_in(sorted(fact_ids)))
    if eligible.is_empty():
        raise ValueError("No exact substantive facts are available for operator review")
    reviewed_at = datetime.now(UTC).isoformat()
    decisions = eligible.select("policy_fact_id").with_columns(
        pl.lit(ReviewStatus.REVIEWED_ACCEPTED.value).alias("human_review_status"),
        pl.lit(reviewer_note).alias("reviewer_note"),
        pl.lit(reviewed_at).alias("reviewed_at"),
        pl.lit(reviewer_id).alias("reviewer_id"),
        pl.lit(True).alias("current_confirmed"),
    )
    return _persist_review_decisions(data_root=data_root, facts=facts, decisions=decisions)


def create_not_stated_review_decisions(
    *,
    data_root: Path = Path("data"),
    reviewer_id: str,
    reviewer_note: str,
    fact_ids: set[str],
) -> pl.DataFrame:
    """Record explicit review completion where an official document does not state a fact."""

    if not fact_ids:
        raise ValueError("At least one explicitly reviewed NOT_STATED fact ID is required")
    facts_path = data_root / "processed" / "policy_facts.parquet"
    if not facts_path.is_file():
        raise ValueError(f"Policy facts are unavailable: {facts_path}")
    facts = pl.read_parquet(facts_path)
    eligible = facts.filter(
        (pl.col("fact_value") == FactValue.NOT_STATED.value)
        & pl.col("contradiction_group_id").is_null()
        & pl.col("valid_to").is_null()
        & pl.col("source_url").str.starts_with("https://")
    )
    available_ids = set(eligible["policy_fact_id"].to_list())
    if unavailable_ids := fact_ids - available_ids:
        raise ValueError(
            "Reviewed NOT_STATED fact IDs are unavailable or ineligible: "
            f"{sorted(unavailable_ids)[:10]}"
        )
    reviewed_at = datetime.now(UTC).isoformat()
    decisions = (
        eligible.filter(pl.col("policy_fact_id").is_in(sorted(fact_ids)))
        .select("policy_fact_id")
        .with_columns(
            pl.lit(ReviewStatus.REVIEWED_NOT_STATED.value).alias("human_review_status"),
            pl.lit(reviewer_note).alias("reviewer_note"),
            pl.lit(reviewed_at).alias("reviewed_at"),
            pl.lit(reviewer_id).alias("reviewer_id"),
            pl.lit(True).alias("current_confirmed"),
        )
    )
    return _persist_review_decisions(data_root=data_root, facts=facts, decisions=decisions)


class PolicyPipeline:
    """Rank, discover, fetch, extract, validate, and publish Phase 7 evidence."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        seed_registry_path: Path = Path("configs/policy_sources.yaml"),
        client: ResponsesClient | None = None,
    ) -> None:
        shared_client = (
            client if client is not None else cast(ResponsesClient, OpenAI(api_key=api_key))
        )
        self.data_root = data_root
        self.output_root = output_root
        self.seeds = PolicySeedRegistry.from_yaml(seed_registry_path)
        self.discoverer = OpenAIPolicyDiscoverer(
            model=model,
            api_key=api_key,
            data_root=data_root,
            client=shared_client,
        )
        self.extractor = OpenAIPolicyExtractor(
            model=model,
            api_key=api_key,
            data_root=data_root,
            client=shared_client,
        )
        self.fetcher = PolicyDocumentFetcher(data_root=data_root)
        self.recent_failed_sources = _load_recent_source_failures(
            output_root / "reports" / "policy" / "errors.json"
        )

    def _discover(self, candidate: PolicyCandidate) -> tuple[list[DiscoveredPolicyDocument], str]:
        recent = [
            source
            for source in self.fetcher.recent_sources(candidate)
            if (candidate.institution_id, source.url) not in self.recent_failed_sources
        ]
        if recent:
            return recent, "RECENT_FETCH_CACHE"
        if any(
            institution_id == candidate.institution_id
            for institution_id, _ in self.recent_failed_sources
        ):
            return [], "RECENT_FAILURE_BACKOFF"
        seeded = list(self.seeds.get(candidate.institution_id))
        if seeded:
            return seeded, "REVIEWED_SEED"
        sitemap = discover_from_sitemaps(candidate)
        if sitemap:
            return sitemap, "OFFICIAL_SITEMAP"
        return self.discoverer.discover(candidate), "OPENAI_DOMAIN_FILTERED_WEB_SEARCH"

    def build(
        self,
        *,
        candidate_limit: int = 200,
        enrichment_limit: int = 200,
        documents_per_institution: int = 1,
        progress: Callable[[str], None] | None = None,
    ) -> PolicyBuildSummary:
        candidates = build_policy_candidates(
            data_root=self.data_root,
            limit=candidate_limit,
            manual_priorities=self.seeds.manual_priorities,
        )
        selected = candidates.head(max(1, min(enrichment_limit, candidate_limit)))
        documents: list[PolicyDocument] = []
        facts: list[PolicyFact] = []
        errors: list[dict[str, object]] = []
        extracted_documents = 0
        extraction_cache_hits = 0
        for row in selected.iter_rows(named=True):
            candidate = PolicyCandidate.model_validate(row)
            try:
                discovered, method = self._discover(candidate)
            except Exception as error:
                errors.append(
                    {
                        "institution_id": candidate.institution_id,
                        "official_name": candidate.official_name,
                        "stage": "DISCOVERY",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
                if progress is not None:
                    progress(
                        f"policy {candidate.candidate_rank}/{selected.height} "
                        f"{candidate.official_name}: discovery failed"
                    )
                continue
            if method == "RECENT_FAILURE_BACKOFF":
                errors.append(
                    {
                        "institution_id": candidate.institution_id,
                        "official_name": candidate.official_name,
                        "stage": method,
                        "error_type": "RecentInstitutionSourceFailure",
                        "error": (
                            f"Institution source retries deferred for {FAILURE_BACKOFF_HOURS} "
                            "hours after the last recorded failure."
                        ),
                    }
                )
                if progress is not None:
                    progress(
                        f"policy {candidate.candidate_rank}/{selected.height} "
                        f"{candidate.official_name}: recent failures deferred"
                    )
                continue
            accepted_urls: set[str] = set()
            for source in discovered:
                if len(accepted_urls) >= max(1, documents_per_institution):
                    break
                if source.url in accepted_urls:
                    continue
                if (candidate.institution_id, source.url) in self.recent_failed_sources:
                    errors.append(
                        {
                            "institution_id": candidate.institution_id,
                            "official_name": candidate.official_name,
                            "stage": "RECENT_FAILURE_BACKOFF",
                            "source_url": source.url,
                            "error_type": "RecentSourceFailure",
                            "error": (
                                f"Source retry deferred for {FAILURE_BACKOFF_HOURS} hours after "
                                "the last recorded failure."
                            ),
                        }
                    )
                    continue
                try:
                    document = self.fetcher.fetch(
                        candidate,
                        source,
                        discovery_method=method,
                        official_domains=self.seeds.domains_for(candidate),
                    )
                    documents.append(document)
                    accepted_urls.add(source.url)
                    if document.parse_status is not ParseStatus.PARSED:
                        errors.append(
                            {
                                "institution_id": candidate.institution_id,
                                "official_name": candidate.official_name,
                                "stage": "PARSE",
                                "source_url": source.url,
                                "error_type": document.parse_status.value,
                                "error": "Document was not eligible for extraction.",
                            }
                        )
                        continue
                    cached, cache_hit = self.extractor.extract(
                        institution_name=candidate.official_name,
                        document=document,
                    )
                    extracted_documents += 1
                    extraction_cache_hits += int(cache_hit)
                    facts.extend(policy_facts_from_extraction(document=document, cached=cached))
                except Exception as error:
                    errors.append(
                        {
                            "institution_id": candidate.institution_id,
                            "official_name": candidate.official_name,
                            "stage": "FETCH_OR_EXTRACT",
                            "source_url": source.url,
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )
            if progress is not None:
                progress(
                    f"policy {candidate.candidate_rank}/{selected.height} "
                    f"{candidate.official_name}: documents={len(documents)} "
                    f"facts={len(facts)} errors={len(errors)} "
                    f"api_calls={self.discoverer.api_call_count + self.extractor.api_call_count}"
                )

        self.fetcher.close()
        processed = self.data_root / "processed"
        documents_path = processed / "policy_documents.parquet"
        facts_path = processed / "policy_facts.parquet"
        review_queue_path = processed / "policy_review_queue.parquet"
        existing_documents = pl.read_parquet(documents_path) if documents_path.is_file() else None
        existing_facts = pl.read_parquet(facts_path) if facts_path.is_file() else None
        documents_frame = _merge_document_history(
            _frame_from_models(documents, schema=DOCUMENT_SCHEMA),
            existing_documents,
        )
        facts_frame = _merge_fact_history(
            _frame_from_models(facts, schema=FACT_SCHEMA),
            existing_facts,
            superseded_at=datetime.now(UTC).isoformat(),
        )
        decisions_path = self.data_root / "review" / "policy_review_decisions.parquet"
        facts_frame = apply_review_decisions(facts_frame, _load_review_decisions(decisions_path))
        write_parquet_atomic(documents_frame, documents_path)
        write_parquet_atomic(facts_frame, facts_path)
        review_queue = facts_frame.filter(
            (pl.col("human_review_status") == ReviewStatus.NEEDS_REVIEW.value)
            & pl.col("valid_to").is_null()
        )
        write_parquet_atomic(review_queue, review_queue_path)
        errors_path = self.output_root / "reports" / "policy" / "errors.json"
        _atomic_json(
            errors_path,
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "error_count": len(errors),
                "errors": errors,
            },
        )
        accepted = facts_frame.filter(
            (pl.col("human_review_status") == ReviewStatus.REVIEWED_ACCEPTED.value)
            & pl.col("is_current")
            & pl.col("valid_to").is_null()
        )
        reviewed_institutions = accepted["institution_id"].n_unique() if accepted.height else 0
        summary_path = self.output_root / "reports" / "policy" / "summary.json"
        summary = PolicyBuildSummary(
            candidate_count=candidates.height,
            discovered_document_count=len(documents),
            parsed_document_count=sum(
                document.parse_status is ParseStatus.PARSED for document in documents
            ),
            extracted_document_count=extracted_documents,
            extraction_cache_hit_count=extraction_cache_hits,
            api_call_count=self.discoverer.api_call_count + self.extractor.api_call_count,
            fact_count=facts_frame.height,
            accepted_fact_count=accepted.height,
            reviewed_institution_count=reviewed_institutions,
            error_count=len(errors),
            candidates_path=processed / "policy_candidates.parquet",
            documents_path=documents_path,
            facts_path=facts_path,
            review_queue_path=review_queue_path,
            errors_path=errors_path,
            summary_path=summary_path,
        )
        _atomic_json(summary_path, summary.model_dump(mode="json"))
        return summary
