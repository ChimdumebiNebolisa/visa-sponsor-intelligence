"""Opt-in live OpenAI contract for Phase 7 Structured Outputs."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sponsor_intel.config import load_settings
from sponsor_intel.policy.extraction import OpenAIPolicyExtractor
from sponsor_intel.policy.models import FactType, FactValue, ParseStatus, PolicyDocument


@pytest.mark.network
def test_live_openai_policy_extraction_contract(tmp_path: Path) -> None:
    if os.environ.get("RUN_LIVE_OPENAI_POLICY_TEST") != "1":
        pytest.skip("Set RUN_LIVE_OPENAI_POLICY_TEST=1 for the explicitly charged live contract")
    settings = load_settings()
    if settings.openai_api_key is None or settings.openai_policy_model is None:
        pytest.skip("OpenAI policy credentials are not configured")
    text = "The university sponsors research staff for H-1B status."
    parsed_path = tmp_path / "policy.txt"
    parsed_path.write_text(text, encoding="utf-8")
    raw_path = tmp_path / "policy.html"
    raw_path.write_text(f"<p>{text}</p>", encoding="utf-8")
    document = PolicyDocument(
        policy_document_id="live-contract",
        institution_id="ipeds:test",
        document_type="h1b_sponsorship_policy",
        title="H-1B policy",
        url="https://example.edu/policy",
        official_domain="example.edu",
        retrieved_at=datetime.now(UTC),
        http_status=200,
        content_type="text/html",
        content_sha256="a" * 64,
        text_sha256="b" * 64,
        published_or_updated_date="2026-08-01",
        raw_path=raw_path,
        parsed_text_path=parsed_path,
        is_current=True,
        parse_status=ParseStatus.PARSED,
        discovery_method="LIVE_CONTRACT_FIXTURE",
        suspicious_text=False,
        cache_hit=False,
    )
    extractor = OpenAIPolicyExtractor(
        model=settings.openai_policy_model,
        api_key=settings.openai_api_key.get_secret_value(),
        data_root=tmp_path,
    )
    cached, cache_hit = extractor.extract(institution_name="Example University", document=document)
    replayed, replay_cache_hit = extractor.extract(
        institution_name="Example University", document=document
    )

    fact = next(
        value
        for value in cached.extraction.facts
        if value.fact_type is FactType.H1B_RESEARCH_STAFF_ELIGIBLE
    )
    assert cache_hit is False
    assert replay_cache_hit is True
    assert replayed == cached
    assert extractor.api_call_count == 1
    assert fact.value is FactValue.YES
    assert fact.source_url == document.url
    assert fact.supporting_excerpt in text
