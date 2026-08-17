"""Secure release bootstrap, cache, and fail-closed deployment coverage."""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from sponsor_intel.config import DeploymentMode, load_settings
from sponsor_intel.deployment import (
    ReleaseBootstrap,
    ReleaseBootstrapError,
    ReleaseNetworkError,
    ReleaseValidationError,
)

REPOSITORY = "example-owner/example-repository"
TAG = "data-2026-08-16"
TOKEN = "test-token-never-render-this-secret"


def _database_bytes(
    root: Path,
    *,
    product_a_schema: bool = True,
    missing_required_view: bool = False,
) -> bytes:
    path = root / f"fixture-{time.time_ns()}.duckdb"
    research_explanation_column = (
        ", 'Fixture HERD explanation.' AS research_scale_explanation" if product_a_schema else ""
    )
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE VIEW vw_employer_explorer AS
            SELECT
                'org-1' AS organization_id,
                'product_a_metrics_v1' AS metric_version,
                'product_a_scores_v1' AS score_version,
                'COMPLETE_ENTITY_COVERAGE' AS entity_coverage_state,
                'COMPLETE_ENTITY_COVERAGE' AS h1b_entity_coverage_state,
                'COMPLETE_ENTITY_COVERAGE' AS perm_entity_coverage_state,
                80.0 AS h1b_history_score,
                'RATED' AS h1b_history_status,
                1.0 AS h1b_history_coverage,
                5 AS h1b_history_star_rating,
                '★★★★★' AS h1b_history_stars,
                '5 out of 5 stars' AS h1b_history_star_label,
                'Fixture H-1B explanation.' AS h1b_history_explanation,
                75.0 AS green_card_history_score,
                'RATED' AS green_card_history_status,
                1.0 AS green_card_history_coverage,
                4 AS green_card_history_star_rating,
                '★★★★☆' AS green_card_history_stars,
                '4 out of 5 stars' AS green_card_history_star_label,
                'Fixture green-card explanation.' AS green_card_history_explanation,
                77.0 AS overall_sponsorship_score,
                'RATED' AS overall_sponsorship_status,
                1.0 AS overall_sponsorship_coverage,
                4 AS overall_sponsorship_star_rating,
                '★★★★☆' AS overall_sponsorship_stars,
                '4 out of 5 stars' AS overall_sponsorship_star_label,
                'Fixture overall explanation.' AS overall_sponsorship_explanation
            """
        )
        connection.execute(
            f"""
            CREATE VIEW vw_institution_explorer AS
            SELECT
                'ipeds:1' AS institution_id,
                metric_version,
                score_version,
                entity_coverage_state,
                h1b_entity_coverage_state,
                perm_entity_coverage_state,
                h1b_history_score,
                h1b_history_status,
                h1b_history_coverage,
                h1b_history_star_rating,
                h1b_history_stars,
                h1b_history_star_label,
                h1b_history_explanation,
                green_card_history_score,
                green_card_history_status,
                green_card_history_coverage,
                green_card_history_star_rating,
                green_card_history_stars,
                green_card_history_star_label,
                green_card_history_explanation,
                overall_sponsorship_score,
                overall_sponsorship_status,
                overall_sponsorship_coverage,
                overall_sponsorship_star_rating,
                overall_sponsorship_stars,
                overall_sponsorship_star_label,
                overall_sponsorship_explanation,
                72.0 AS research_scale_score,
                'RATED' AS research_scale_status,
                4 AS research_scale_star_rating,
                '★★★★☆' AS research_scale_stars,
                '4 out of 5 stars' AS research_scale_star_label
                {research_explanation_column}
            FROM vw_employer_explorer
            """
        )
        connection.execute(
            "CREATE VIEW vw_organization_detail AS SELECT organization_id FROM vw_employer_explorer"
        )
        for view_name in (
            "vw_h1b_trends",
            "vw_perm_trends",
            "vw_relevant_titles",
            "vw_everify_evidence",
            "vw_opt_evidence",
            "vw_entity_review_queue",
            "vw_everify_review_queue",
            "vw_source_artifacts",
        ):
            if missing_required_view and view_name == "vw_entity_review_queue":
                continue
            connection.execute(f"CREATE VIEW {view_name} AS SELECT 'fixture' AS value")
        connection.execute("CREATE VIEW vw_data_health AS SELECT 'dol_lca' AS source_id")
        connection.execute("CREATE VIEW vw_quality_checks AS SELECT 'PASS' AS status")
    return path.read_bytes()


def _release_payload(
    tmp_path: Path,
    *,
    quality_passed: bool = True,
    checksum_matches: bool = True,
    build_id_matches: bool = True,
    tag_matches_date: bool = True,
    product_a_schema: bool = True,
    missing_required_view: bool = False,
) -> tuple[dict[str, Any], dict[int, bytes]]:
    build_id = "product-a-0123456789abcdef"
    database = _database_bytes(
        tmp_path,
        product_a_schema=product_a_schema,
        missing_required_view=missing_required_view,
    )
    generated_at = "2026-08-16T12:30:00+00:00" if tag_matches_date else "2026-08-15T12:30:00+00:00"
    manifest_sha256 = "a" * 64
    quality = json.dumps(
        {
            "build_id": build_id,
            "generated_at": generated_at,
            "passed": quality_passed,
            "critical_failure_count": 0 if quality_passed else 1,
            "manifest_sha256": manifest_sha256,
            "metric_version": "product_a_metrics_v1",
            "score_version": "product_a_scores_v1",
            "checks": [
                {
                    "check_id": "fixture",
                    "critical": True,
                    "status": "PASS" if quality_passed else "FAIL",
                }
            ],
        },
        sort_keys=True,
    ).encode()
    metadata = json.dumps(
        {
            "build_id": (build_id if build_id_matches else "product-a-fedcba9876543210"),
            "generated_at": generated_at,
            "manifest_sha256": manifest_sha256,
            "metric_version": "product_a_metrics_v1",
            "score_version": "product_a_scores_v1",
            "quality_passed": quality_passed,
            "employer_count": 1,
            "institution_count": 1,
        },
        sort_keys=True,
    ).encode()
    database_checksum = hashlib.sha256(database).hexdigest()
    if not checksum_matches:
        database_checksum = "0" * 64
    checksums = (
        f"{database_checksum}  immigration.duckdb\n"
        f"{hashlib.sha256(quality).hexdigest()}  data-quality.json\n"
        f"{hashlib.sha256(metadata).hexdigest()}  build-metadata.json\n"
    ).encode()
    content_by_name = {
        "immigration.duckdb": database,
        "data-quality.json": quality,
        "build-metadata.json": metadata,
        "checksums.sha256": checksums,
    }
    content_by_id: dict[int, bytes] = {}
    assets: list[dict[str, object]] = []
    for asset_id, (name, content) in enumerate(content_by_name.items(), start=101):
        content_by_id[asset_id] = content
        assets.append(
            {
                "id": asset_id,
                "name": name,
                "size": len(content),
                "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "url": (f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset_id}"),
            }
        )
    assets.extend(
        [
            {
                "id": 201,
                "name": "processed-parquet.zip",
                "size": 8,
                "digest": f"sha256:{hashlib.sha256(b'parquet').hexdigest()}",
                "url": f"https://api.github.com/repos/{REPOSITORY}/releases/assets/201",
            },
            {
                "id": 202,
                "name": "build-state.zip",
                "size": 5,
                "digest": f"sha256:{hashlib.sha256(b'state').hexdigest()}",
                "url": f"https://api.github.com/repos/{REPOSITORY}/releases/assets/202",
            },
        ]
    )
    return {"id": 55, "tag_name": TAG, "assets": assets}, content_by_id


def _transport(
    payload: dict[str, Any],
    content_by_id: dict[int, bytes],
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=payload, request=request)
        asset_id = int(request.url.path.rsplit("/", maxsplit=1)[-1])
        return httpx.Response(200, content=content_by_id[asset_id], request=request)

    return httpx.MockTransport(handler)


def _bootstrap(
    cache_dir: Path,
    transport: httpx.BaseTransport,
    *,
    retry_attempts: int = 2,
) -> ReleaseBootstrap:
    return ReleaseBootstrap(
        repository=REPOSITORY,
        release_tag="latest",
        token=TOKEN,
        cache_dir=cache_dir,
        transport=transport,
        retry_attempts=retry_attempts,
        retry_wait_seconds=0,
        lock_timeout_seconds=5,
    )


def test_bootstrap_accepts_policy_absent_product_a_database_and_downloads_runtime_assets(
    tmp_path: Path,
) -> None:
    payload, content = _release_payload(tmp_path)
    requests: list[httpx.Request] = []

    with _bootstrap(tmp_path / "cache", _transport(payload, content, requests)) as bootstrap:
        runtime = bootstrap.ensure()

    assert runtime.release_tag == TAG
    assert runtime.build_id == "product-a-0123456789abcdef"
    assert runtime.cache_hit is False
    assert runtime.database_path.is_file()
    requested_asset_ids = {
        int(request.url.path.rsplit("/", maxsplit=1)[-1])
        for request in requests
        if "/releases/assets/" in request.url.path
    }
    assert requested_asset_ids == set(content)
    assert all(request.headers.get("authorization") == f"Bearer {TOKEN}" for request in requests)


@pytest.mark.parametrize(
    ("release_options", "message"),
    [
        ({"checksum_matches": False}, "checksum mismatch"),
        ({"quality_passed": False}, "quality or Product A build metadata"),
        ({"build_id_matches": False}, "quality or Product A build metadata"),
        ({"tag_matches_date": False}, "tag does not match"),
        ({"product_a_schema": False}, "missing Product A columns"),
        ({"missing_required_view": True}, "missing Product A views"),
    ],
)
def test_invalid_release_fails_closed_without_promoting_cache(
    tmp_path: Path,
    release_options: dict[str, bool],
    message: str,
) -> None:
    payload, content = _release_payload(tmp_path, **release_options)
    cache_dir = tmp_path / "cache"

    with (
        _bootstrap(cache_dir, _transport(payload, content)) as bootstrap,
        pytest.raises(ReleaseValidationError, match=message),
    ):
        bootstrap.ensure()

    repository_cache = cache_dir / "example-owner--example-repository"
    assert not (repository_cache / "current.json").exists()
    assert not list(repository_cache.glob(".staging-*"))


def test_verified_cache_is_used_only_when_github_is_unavailable(tmp_path: Path) -> None:
    payload, content = _release_payload(tmp_path)
    cache_dir = tmp_path / "cache"
    with _bootstrap(cache_dir, _transport(payload, content)) as bootstrap:
        first = bootstrap.ensure()
    assert first.cache_hit is False

    attempts = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError(f"transport included {TOKEN}", request=request)

    with _bootstrap(cache_dir, httpx.MockTransport(unavailable)) as bootstrap:
        cached = bootstrap.ensure()

    assert cached.cache_hit is True
    assert cached.build_id == first.build_id
    assert attempts == 2


def test_rate_limit_403_uses_verified_cache_but_authorization_403_does_not(
    tmp_path: Path,
) -> None:
    payload, content = _release_payload(tmp_path)
    cache_dir = tmp_path / "cache"
    with _bootstrap(cache_dir, _transport(payload, content)) as bootstrap:
        bootstrap.ensure()

    attempts = 0

    def rate_limited(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0"},
            request=request,
        )

    with _bootstrap(cache_dir, httpx.MockTransport(rate_limited)) as bootstrap:
        cached = bootstrap.ensure()

    assert cached.cache_hit is True
    assert attempts == 2

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    with (
        _bootstrap(cache_dir, httpx.MockTransport(forbidden)) as bootstrap,
        pytest.raises(ReleaseBootstrapError, match="HTTP 403"),
    ):
        bootstrap.ensure()


def test_network_failure_without_valid_cache_redacts_token(tmp_path: Path) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"transport included {TOKEN}", request=request)

    with (
        _bootstrap(tmp_path / "empty-cache", httpx.MockTransport(unavailable)) as bootstrap,
        pytest.raises(ReleaseNetworkError) as captured,
    ):
        bootstrap.ensure()

    rendered = "".join(traceback.format_exception(captured.value))
    assert TOKEN not in rendered
    assert "no verified cached release" in str(captured.value)


def test_file_lock_prevents_duplicate_concurrent_asset_downloads(tmp_path: Path) -> None:
    payload, content = _release_payload(tmp_path)
    cache_dir = tmp_path / "cache"
    asset_requests: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=payload, request=request)
        asset_id = int(request.url.path.rsplit("/", maxsplit=1)[-1])
        asset_requests.append(asset_id)
        time.sleep(0.01)
        return httpx.Response(200, content=content[asset_id], request=request)

    transport = httpx.MockTransport(handler)

    def resolve() -> bool:
        with _bootstrap(cache_dir, transport) as bootstrap:
            return bootstrap.ensure().cache_hit

    with ThreadPoolExecutor(max_workers=2) as executor:
        cache_hits = list(executor.map(lambda _: resolve(), range(2)))

    assert sorted(cache_hits) == [False, True]
    assert sorted(asset_requests) == sorted(content)


def test_cross_origin_redirect_never_forwards_authorization(tmp_path: Path) -> None:
    payload, content = _release_payload(tmp_path)
    redirected_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=payload, request=request)
        if request.url.host == "api.github.com":
            asset_id = request.url.path.rsplit("/", maxsplit=1)[-1]
            return httpx.Response(
                302,
                headers={
                    "location": f"https://release-assets.githubusercontent.com/download/{asset_id}"
                },
                request=request,
            )
        redirected_headers.append(request.headers.get("authorization"))
        asset_id = int(request.url.path.rsplit("/", maxsplit=1)[-1])
        return httpx.Response(200, content=content[asset_id], request=request)

    with _bootstrap(tmp_path / "cache", httpx.MockTransport(handler)) as bootstrap:
        bootstrap.ensure()

    assert redirected_headers == [None] * 4


def test_release_settings_are_typed_and_secret_safe(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path / "missing.yaml",
        environ={
            "SPONSOR_INTEL_DEPLOYMENT_MODE": "release",
            "SPONSOR_INTEL_REQUIRE_DATA": "true",
            "SPONSOR_INTEL_GITHUB_REPOSITORY": REPOSITORY,
            "SPONSOR_INTEL_RELEASE_TAG": TAG,
            "SPONSOR_INTEL_RELEASE_CACHE_DIR": str(tmp_path / "cache"),
            "GITHUB_RELEASE_READ_TOKEN": TOKEN,
        },
    )

    assert settings.deployment_mode is DeploymentMode.RELEASE
    assert settings.require_data is True
    assert settings.release_tag == TAG
    assert TOKEN not in str(settings.safe_summary())
    assert settings.safe_summary()["github_release_read_token_configured"] is True
