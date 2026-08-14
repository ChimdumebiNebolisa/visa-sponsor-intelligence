from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from sponsor_intel.entity_resolution.models import EntityResolutionConfig
from sponsor_intel.evidence.everify import (
    EVerifyEvidenceBuilder,
    EVerifySearchResponse,
    PlaywrightEVerifyClient,
    _evaluate_matches,
)
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.models import SourceContext
from sponsor_intel.sources.registry import SourceRegistry
from sponsor_intel.sources.sevp_opt import SevpOptAdapter, parse_opt_table_rows


def _priority() -> dict[str, object]:
    return {
        "lookup_id": "everify_test",
        "priority_rank": 1,
        "queried_name": "Acme Labs LLC",
        "legal_entity_id": "legal_acme",
        "parent_organization_id": "parent_acme",
        "organization_id": "parent_acme",
        "state": "CA",
    }


def _response(*rows: dict[str, str]) -> EVerifySearchResponse:
    return EVerifySearchResponse(rows=rows, retrieved_at=datetime(2026, 8, 14, tzinfo=UTC))


def test_opt_parser_preserves_positive_only_missing_program_semantics() -> None:
    frame = parse_opt_table_rows(
        [
            ["Top 200 Employer Names", "Total", "OPT", "STEM"],
            ["Acme Labs", "1,234", "1,234", ""],
            ["State University", "250", "200", "75"],
        ],
        report_year=2024,
        source_artifact_id="artifact",
        source_url="https://www.ice.gov/report.pdf",
        landing_page_url="https://www.ice.gov/sevis/whats-new",
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        source_sha256="a" * 64,
    )

    assert frame.height == 5
    assert frame.filter(frame["employer_name_raw"] == "Acme Labs").height == 2
    assert frame["is_positive"].all()
    assert set(frame["program_type"]) == {"OPT_OR_STEM_OPT", "OPT", "STEM_OPT"}


def test_everify_exact_active_match_requires_full_name_and_state() -> None:
    config = EntityResolutionConfig.from_yaml(Path("configs/entity_resolution.yaml"))
    result = _evaluate_matches(
        _priority(),
        _response(
            {
                "Employer": "ACME LABS, LLC",
                "Doing Business As": "",
                "Account Status": "Open",
                "Date Enrolled": "01/02/2020",
                "Date Terminated": "",
                "Workforce Size": "100 to 499",
                "Hiring Site Locations": "CA,TX",
                "SUM(Number of Hiring Sites)": "3",
            }
        ),
        config,
    )

    assert result["enrollment_status"] == "CONFIRMED_ACTIVE"
    assert result["match_confidence"] == 1.0
    assert result["hiring_site_count"] == 3
    assert result["review_status"] == "NOT_REQUIRED"


def test_everify_no_match_is_not_a_negative_enrollment_status() -> None:
    config = EntityResolutionConfig.from_yaml(Path("configs/entity_resolution.yaml"))
    result = _evaluate_matches(_priority(), _response(), config)

    assert result["enrollment_status"] == "NO_MATCH"
    assert "not evidence" in str(result["review_reason"])


def test_everify_state_conflict_routes_exact_name_to_review() -> None:
    config = EntityResolutionConfig.from_yaml(Path("configs/entity_resolution.yaml"))
    result = _evaluate_matches(
        _priority(),
        _response(
            {
                "Employer": "Acme Labs LLC",
                "Doing Business As": "",
                "Account Status": "Open",
                "Date Enrolled": "01/02/2020",
                "Date Terminated": "",
                "Workforce Size": "20 to 99",
                "Hiring Site Locations": "NY",
                "SUM(Number of Hiring Sites)": "1",
            }
        ),
        config,
    )

    assert result["enrollment_status"] == "AMBIGUOUS"
    assert result["review_status"] == "NEEDS_REVIEW"


def test_everify_builder_reuses_fresh_cache(tmp_path: Path) -> None:
    priorities = pl.DataFrame(
        {
            "priority_rank": [1],
            "lookup_id": ["everify_test"],
            "organization_id": ["parent_acme"],
            "parent_organization_id": ["parent_acme"],
            "legal_entity_id": ["legal_acme"],
            "organization_name": ["Acme"],
            "queried_name": ["Acme Labs LLC"],
            "state": ["CA"],
            "priority_tier": [1],
            "priority_reason": ["SPONSORSHIP_ACTIVITY"],
            "priority_score": [10.0],
            "query_is_safe": [True],
            "lookup_status": ["NOT_CHECKED"],
        }
    )

    class Provider:
        def search(self, queried_name: str) -> EVerifySearchResponse:
            assert queried_name == "Acme Labs LLC"
            return _response(
                {
                    "Employer": "Acme Labs LLC",
                    "Doing Business As": "",
                    "Account Status": "Open",
                    "Date Enrolled": "01/02/2020",
                    "Date Terminated": "",
                    "Workforce Size": "100 to 499",
                    "Hiring Site Locations": "CA",
                    "SUM(Number of Hiring Sites)": "1",
                }
            )

    builder = EVerifyEvidenceBuilder(data_root=tmp_path / "data", output_root=tmp_path / "outputs")
    first = builder.run(priorities, limit=1, provider=Provider())
    second = builder.run(priorities, limit=1, provider=None)

    assert first.cache_hit_count == 0
    assert second.cache_hit_count == 1
    assert second.confirmed_active_count == 1
    assert pl.read_parquet(second.observations_path)["enrollment_status"].item() == (
        "CONFIRMED_ACTIVE"
    )


def test_result_sync_rejects_unfiltered_table_and_accepts_committed_rows() -> None:
    columns = ["Employer", "Doing Business As"]
    assert not PlaywrightEVerifyClient._results_match_query(
        {"columns": columns, "rows": [["Unrelated Inc", ""]], "totalRowCount": 1},
        "Acme Labs LLC",
    )
    assert PlaywrightEVerifyClient._results_match_query(
        {"columns": columns, "rows": [["ACME LABS, LLC", ""]], "totalRowCount": 1},
        "Acme Labs LLC",
    )
    assert PlaywrightEVerifyClient._is_empty_result(
        {"columns": columns, "rows": [], "totalRowCount": 0}
    )


def test_sevp_discovery_uses_reviewed_official_fallback_when_ice_blocks(
    tmp_path: Path,
) -> None:
    def blocked(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    config = SourceRegistry.from_yaml().get("sevp_opt")
    with OfficialHttpClient(
        config.official_domains, transport=httpx.MockTransport(blocked)
    ) as client:
        adapter = SevpOptAdapter(config, client, tmp_path / "data", tmp_path / "outputs")
        candidates = adapter.discover(SourceContext(from_fiscal_year=2022))

        assert len(candidates) == 1
        assert candidates[0].fiscal_year == 2024
        assert candidates[0].download_url.startswith("https://www.ice.gov/")
        assert adapter.last_discovery_report is not None
        assert "reviewed official" in adapter.last_discovery_report.warnings[0]
