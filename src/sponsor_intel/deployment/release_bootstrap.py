"""Download and cache one checksum-verified, quality-approved Product A data release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, TypeVar
from urllib.parse import quote, urljoin, urlparse

import duckdb
import httpx
from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sponsor_intel.config import DeploymentMode, Settings
from sponsor_intel.database.builder import REQUIRED_VIEWS as PRESENTATION_REQUIRED_VIEWS

RUNTIME_ASSET_NAMES = (
    "immigration.duckdb",
    "data-quality.json",
    "build-metadata.json",
    "checksums.sha256",
)
EXPECTED_METRIC_VERSION = "product_a_metrics_v1"
EXPECTED_SCORE_VERSION = "product_a_scores_v1"
_ASSET_LIMITS = {
    "immigration.duckdb": 1024 * 1024 * 1024,
    "data-quality.json": 2 * 1024 * 1024,
    "build-metadata.json": 2 * 1024 * 1024,
    "checksums.sha256": 64 * 1024,
}
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_RELEASE_METADATA_BYTES = 2 * 1024 * 1024
_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$")
_REQUIRED_VIEWS = set(PRESENTATION_REQUIRED_VIEWS) - {
    "vw_policy_evidence",
    "vw_policy_review_queue",
}
_REQUIRED_EMPLOYER_COLUMNS = {
    "organization_id",
    "metric_version",
    "score_version",
    "entity_coverage_state",
    "h1b_entity_coverage_state",
    "perm_entity_coverage_state",
    "h1b_history_score",
    "h1b_history_status",
    "h1b_history_coverage",
    "h1b_history_star_rating",
    "h1b_history_stars",
    "h1b_history_star_label",
    "h1b_history_explanation",
    "green_card_history_score",
    "green_card_history_status",
    "green_card_history_coverage",
    "green_card_history_star_rating",
    "green_card_history_stars",
    "green_card_history_star_label",
    "green_card_history_explanation",
    "overall_sponsorship_score",
    "overall_sponsorship_status",
    "overall_sponsorship_coverage",
    "overall_sponsorship_star_rating",
    "overall_sponsorship_stars",
    "overall_sponsorship_star_label",
    "overall_sponsorship_explanation",
}
_REQUIRED_INSTITUTION_COLUMNS = {
    "institution_id",
    "metric_version",
    "score_version",
    "entity_coverage_state",
    "h1b_entity_coverage_state",
    "perm_entity_coverage_state",
    "h1b_history_score",
    "h1b_history_status",
    "h1b_history_coverage",
    "h1b_history_star_rating",
    "h1b_history_stars",
    "h1b_history_star_label",
    "h1b_history_explanation",
    "green_card_history_score",
    "green_card_history_status",
    "green_card_history_coverage",
    "green_card_history_star_rating",
    "green_card_history_stars",
    "green_card_history_star_label",
    "green_card_history_explanation",
    "overall_sponsorship_score",
    "overall_sponsorship_status",
    "overall_sponsorship_coverage",
    "overall_sponsorship_star_rating",
    "overall_sponsorship_stars",
    "overall_sponsorship_star_label",
    "overall_sponsorship_explanation",
    "research_scale_score",
    "research_scale_status",
    "research_scale_star_rating",
    "research_scale_stars",
    "research_scale_star_label",
    "research_scale_explanation",
}
_T = TypeVar("_T")


class ReleaseBootstrapError(RuntimeError):
    """User-safe hosted data bootstrap failure."""


class ReleaseNetworkError(ReleaseBootstrapError):
    """Temporary GitHub network or service failure."""


class ReleaseValidationError(ReleaseBootstrapError):
    """Downloaded or cached release failed a trust check."""


class _RetryableGitHubError(RuntimeError):
    """Internal marker for a retryable GET response."""


def _is_retryable_response(response: httpx.Response) -> bool:
    """Distinguish GitHub rate-limit 403s from fail-closed authorization errors."""

    if response.status_code in _RETRYABLE_STATUSES:
        return True
    return response.status_code == 403 and (
        "retry-after" in response.headers or response.headers.get("x-ratelimit-remaining") == "0"
    )


@dataclass(frozen=True, slots=True)
class ReleaseRuntime:
    """Verified files and release identity for the read-only application."""

    database_path: Path
    quality_path: Path
    metadata_path: Path
    checksums_path: Path
    release_tag: str
    build_id: str
    generated_at: str
    release_fingerprint: str
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class _ReleaseAsset:
    name: str
    api_url: str
    asset_id: int
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ReleaseDescriptor:
    tag: str
    release_id: int
    assets: tuple[_ReleaseAsset, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _ValidatedRelease:
    tag: str
    build_id: str
    generated_at: str
    fingerprint: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Write bootstrap cache metadata without importing the ingestion package boundary."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as destination:
            json.dump(payload, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _safe_repository(repository: str) -> str:
    normalized = repository.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", normalized) is None:
        raise ReleaseBootstrapError("GitHub repository must use the owner/name format.")
    return normalized


def _safe_tag(tag: str) -> str:
    normalized = tag.strip()
    if normalized != "latest" and re.fullmatch(r"[A-Za-z0-9._-]+", normalized) is None:
        raise ReleaseBootstrapError("GitHub release tag contains unsupported characters.")
    return normalized


def _is_allowed_redirect(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    hostname = parsed.hostname.casefold().rstrip(".")
    return (
        hostname in {"api.github.com", "github.com"}
        or hostname.endswith(".github.com")
        or hostname.endswith(".githubusercontent.com")
    )


def _read_json(path: Path, *, maximum_bytes: int) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size > maximum_bytes:
        raise ReleaseValidationError(f"Release metadata file is missing or oversized: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseValidationError(f"Release metadata is invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"Release metadata must be an object: {path.name}")
    return value


class ReleaseBootstrap:
    """Resolve exactly four hosted assets into an atomically promoted local cache."""

    def __init__(
        self,
        *,
        repository: str,
        release_tag: str,
        token: str,
        cache_dir: Path,
        transport: httpx.BaseTransport | None = None,
        retry_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        lock_timeout_seconds: float = 600,
    ) -> None:
        self.repository = _safe_repository(repository)
        self.requested_tag = _safe_tag(release_tag)
        if not token.strip():
            raise ReleaseBootstrapError("A GitHub release read token is required.")
        self._token = token
        self.cache_dir = cache_dir
        self.retry_attempts = min(max(1, retry_attempts), 5)
        self.retry_wait_seconds = min(max(0.0, retry_wait_seconds), 5.0)
        self.lock_timeout_seconds = min(max(0.0, lock_timeout_seconds), 600.0)
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10, read=120, write=30, pool=30),
            transport=transport,
            follow_redirects=False,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "SponsorIntel/0.1 release-bootstrap",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def __enter__(self) -> ReleaseBootstrap:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the bounded HTTP client."""

        self._client.close()

    @property
    def _repository_cache(self) -> Path:
        owner, name = self.repository.split("/", maxsplit=1)
        return self.cache_dir / f"{owner}--{name}"

    def _with_retries(self, operation: Callable[[], _T]) -> _T:
        retrying = Retrying(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=self.retry_wait_seconds, max=4),
            retry=retry_if_exception_type((httpx.TransportError, _RetryableGitHubError)),
            reraise=True,
        )
        try:
            return retrying(operation)
        except (httpx.TransportError, _RetryableGitHubError):
            raise ReleaseNetworkError("GitHub release data is temporarily unavailable.") from None

    def _stream_once(
        self,
        url: str,
        *,
        maximum_bytes: int,
        destination: BinaryIO | None,
        accept: str,
    ) -> tuple[bytes, str, int]:
        current_url = url
        collected = bytearray()
        digest = hashlib.sha256()
        byte_count = 0
        for _ in range(6):
            if not _is_allowed_redirect(current_url):
                raise ReleaseValidationError("GitHub returned an unsafe download URL.")
            hostname = (urlparse(current_url).hostname or "").casefold()
            headers = {"Accept": accept}
            if hostname == "api.github.com":
                headers["Authorization"] = f"Bearer {self._token}"
            request = self._client.build_request("GET", current_url, headers=headers)
            response = self._client.send(request, stream=True)
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise ReleaseValidationError("GitHub redirect omitted its destination.")
                    current_url = urljoin(current_url, location)
                    continue
                if _is_retryable_response(response):
                    raise _RetryableGitHubError(f"Retryable GitHub status {response.status_code}")
                if response.status_code < 200 or response.status_code >= 300:
                    raise ReleaseBootstrapError(
                        f"GitHub release request failed with HTTP {response.status_code}."
                    )
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > maximum_bytes:
                            raise ReleaseValidationError("GitHub response exceeded its size limit.")
                    except ValueError as error:
                        raise ReleaseValidationError(
                            "GitHub returned an invalid content length."
                        ) from error
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    byte_count += len(chunk)
                    if byte_count > maximum_bytes:
                        raise ReleaseValidationError("GitHub response exceeded its size limit.")
                    digest.update(chunk)
                    if destination is None:
                        collected.extend(chunk)
                    else:
                        destination.write(chunk)
                if byte_count == 0:
                    raise ReleaseValidationError("GitHub returned an empty release asset.")
                return bytes(collected), digest.hexdigest(), byte_count
            finally:
                response.close()
        raise ReleaseValidationError("GitHub returned too many redirects.")

    def _get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        def request_once() -> bytes:
            content, _, _ = self._stream_once(
                url,
                maximum_bytes=maximum_bytes,
                destination=None,
                accept="application/vnd.github+json",
            )
            return content

        return self._with_retries(request_once)

    def _release_endpoint(self) -> str:
        base = f"https://api.github.com/repos/{self.repository}/releases"
        if self.requested_tag == "latest":
            return f"{base}/latest"
        return f"{base}/tags/{quote(self.requested_tag, safe='')}"

    def _release_descriptor(self) -> _ReleaseDescriptor:
        try:
            payload = json.loads(
                self._get_bytes(
                    self._release_endpoint(), maximum_bytes=_MAX_RELEASE_METADATA_BYTES
                ).decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ReleaseValidationError("GitHub release metadata is invalid.") from error
        if not isinstance(payload, dict):
            raise ReleaseValidationError("GitHub release metadata must be an object.")
        tag = payload.get("tag_name")
        release_id = payload.get("id")
        raw_assets = payload.get("assets")
        if not isinstance(tag, str) or re.fullmatch(r"data-\d{4}-\d{2}-\d{2}", tag) is None:
            raise ReleaseValidationError("GitHub release tag is not a dated data release.")
        if self.requested_tag != "latest" and tag != self.requested_tag:
            raise ReleaseValidationError("GitHub returned a different release tag than requested.")
        if not isinstance(release_id, int) or not isinstance(raw_assets, list):
            raise ReleaseValidationError("GitHub release identity or assets are invalid.")

        selected: list[_ReleaseAsset] = []
        for required_name in RUNTIME_ASSET_NAMES:
            matches = [
                asset
                for asset in raw_assets
                if isinstance(asset, dict) and asset.get("name") == required_name
            ]
            if len(matches) != 1:
                raise ReleaseValidationError(
                    f"Release must contain exactly one {required_name} asset."
                )
            raw = matches[0]
            asset_id = raw.get("id")
            api_url = raw.get("url")
            byte_size = raw.get("size")
            raw_digest = raw.get("digest")
            if (
                not isinstance(asset_id, int)
                or not isinstance(api_url, str)
                or not isinstance(byte_size, int)
                or isinstance(byte_size, bool)
                or byte_size <= 0
                or byte_size > _ASSET_LIMITS[required_name]
                or not isinstance(raw_digest, str)
                or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", raw_digest) is None
            ):
                raise ReleaseValidationError(f"Release asset metadata is invalid: {required_name}")
            parsed_url = urlparse(api_url)
            expected_path = f"/repos/{self.repository}/releases/assets/{asset_id}"
            if (
                parsed_url.scheme != "https"
                or (parsed_url.hostname or "").casefold() != "api.github.com"
                or parsed_url.path != expected_path
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ReleaseValidationError(f"Release asset API URL is invalid: {required_name}")
            selected.append(
                _ReleaseAsset(
                    name=required_name,
                    api_url=api_url,
                    asset_id=asset_id,
                    byte_size=byte_size,
                    sha256=raw_digest.removeprefix("sha256:").casefold(),
                )
            )

        fingerprint_material = json.dumps(
            {
                "release_id": release_id,
                "tag": tag,
                "assets": [
                    {
                        "id": asset.asset_id,
                        "name": asset.name,
                        "size": asset.byte_size,
                        "sha256": asset.sha256,
                    }
                    for asset in selected
                ],
            },
            sort_keys=True,
        ).encode()
        return _ReleaseDescriptor(
            tag=tag,
            release_id=release_id,
            assets=tuple(selected),
            fingerprint=hashlib.sha256(fingerprint_material).hexdigest(),
        )

    def _download_asset(self, asset: _ReleaseAsset, target: Path) -> None:
        def request_once() -> tuple[str, int]:
            with target.open("wb") as destination:
                _, digest, byte_count = self._stream_once(
                    asset.api_url,
                    maximum_bytes=_ASSET_LIMITS[asset.name],
                    destination=destination,
                    accept="application/octet-stream",
                )
                destination.flush()
                os.fsync(destination.fileno())
                return digest, byte_count

        digest, byte_count = self._with_retries(request_once)
        if byte_count != asset.byte_size or digest != asset.sha256:
            raise ReleaseValidationError(f"GitHub digest or size mismatch: {asset.name}")

    @staticmethod
    def _parse_checksums(path: Path) -> dict[str, str]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise ReleaseValidationError("Release checksum manifest is unreadable.") from error
        checksums: dict[str, str] = {}
        for line in lines:
            match = _CHECKSUM_LINE.fullmatch(line)
            if match is None or match.group(2) in checksums:
                raise ReleaseValidationError("Release checksum manifest is invalid.")
            checksums[match.group(2)] = match.group(1).casefold()
        return checksums

    @staticmethod
    def _database_schema_is_product_a(database_path: Path) -> tuple[int, int]:
        try:
            with duckdb.connect(str(database_path), read_only=True) as connection:
                views = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
                    ).fetchall()
                }
                missing_views = _REQUIRED_VIEWS - views
                if missing_views:
                    raise ReleaseValidationError(
                        f"Presentation database is missing Product A views: {sorted(missing_views)}"
                    )
                employer_columns = {
                    str(row[0])
                    for row in connection.execute(
                        "DESCRIBE SELECT * FROM vw_employer_explorer"
                    ).fetchall()
                }
                institution_columns = {
                    str(row[0])
                    for row in connection.execute(
                        "DESCRIBE SELECT * FROM vw_institution_explorer"
                    ).fetchall()
                }
                if missing := _REQUIRED_EMPLOYER_COLUMNS - employer_columns:
                    raise ReleaseValidationError(
                        f"Employer explorer is missing Product A columns: {sorted(missing)}"
                    )
                if missing := _REQUIRED_INSTITUTION_COLUMNS - institution_columns:
                    raise ReleaseValidationError(
                        f"Institution explorer is missing Product A columns: {sorted(missing)}"
                    )
                employer_count_row = connection.execute(
                    "SELECT count(*) FROM vw_employer_explorer"
                ).fetchone()
                institution_count_row = connection.execute(
                    "SELECT count(*) FROM vw_institution_explorer"
                ).fetchone()
                if employer_count_row is None or institution_count_row is None:
                    raise ReleaseValidationError(
                        "Presentation database row counts are unavailable."
                    )
                employer_count = int(employer_count_row[0])
                institution_count = int(institution_count_row[0])
                if employer_count <= 0 or institution_count <= 0:
                    raise ReleaseValidationError("Presentation database contains no explorer data.")
                for view_name in ("vw_employer_explorer", "vw_institution_explorer"):
                    invalid_version_row = connection.execute(
                        f"""
                        SELECT count(*)
                        FROM {view_name}
                        WHERE metric_version IS DISTINCT FROM ?
                           OR score_version IS DISTINCT FROM ?
                        """,
                        [EXPECTED_METRIC_VERSION, EXPECTED_SCORE_VERSION],
                    ).fetchone()
                    if invalid_version_row is None or int(invalid_version_row[0]) != 0:
                        raise ReleaseValidationError(
                            f"Presentation database contains non-Product A rows: {view_name}"
                        )
                return employer_count, institution_count
        except ReleaseValidationError:
            raise
        except Exception:
            raise ReleaseValidationError(
                "Presentation database failed read-only validation."
            ) from None

    def _validate_generation(
        self,
        directory: Path,
        *,
        expected_tag: str | None = None,
        expected_fingerprint: str | None = None,
    ) -> _ValidatedRelease:
        paths = {name: directory / name for name in RUNTIME_ASSET_NAMES}
        descriptor = _read_json(directory / ".release.json", maximum_bytes=256 * 1024)
        tag = descriptor.get("release_tag")
        fingerprint = descriptor.get("release_fingerprint")
        asset_digests = descriptor.get("asset_sha256")
        if (
            not isinstance(tag, str)
            or re.fullmatch(r"data-\d{4}-\d{2}-\d{2}", tag) is None
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or not isinstance(asset_digests, dict)
            or (expected_tag is not None and tag != expected_tag)
            or (expected_fingerprint is not None and fingerprint != expected_fingerprint)
        ):
            raise ReleaseValidationError("Cached release identity is invalid.")
        for name, path in paths.items():
            expected_digest = asset_digests.get(name)
            if (
                not isinstance(expected_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
                or not path.is_file()
                or path.stat().st_size > _ASSET_LIMITS[name]
                or _sha256(path) != expected_digest
            ):
                raise ReleaseValidationError(f"Cached release asset failed validation: {name}")

        checksums = self._parse_checksums(paths["checksums.sha256"])
        for name in ("immigration.duckdb", "data-quality.json", "build-metadata.json"):
            if checksums.get(name) != _sha256(paths[name]):
                raise ReleaseValidationError(f"Release checksum mismatch: {name}")

        quality = _read_json(
            paths["data-quality.json"], maximum_bytes=_ASSET_LIMITS["data-quality.json"]
        )
        metadata = _read_json(
            paths["build-metadata.json"], maximum_bytes=_ASSET_LIMITS["build-metadata.json"]
        )
        build_id = quality.get("build_id")
        generated_at = metadata.get("generated_at")
        quality_generated_at = quality.get("generated_at")
        manifest_sha256 = quality.get("manifest_sha256")
        employer_count = metadata.get("employer_count")
        institution_count = metadata.get("institution_count")
        if (
            quality.get("passed") is not True
            or quality.get("critical_failure_count") != 0
            or metadata.get("quality_passed") is not True
            or not isinstance(build_id, str)
            or re.fullmatch(r"product-a-[0-9a-f]{16}", build_id) is None
            or metadata.get("build_id") != build_id
            or not isinstance(manifest_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
            or metadata.get("manifest_sha256") != manifest_sha256
            or quality.get("metric_version") != EXPECTED_METRIC_VERSION
            or metadata.get("metric_version") != EXPECTED_METRIC_VERSION
            or quality.get("score_version") != EXPECTED_SCORE_VERSION
            or metadata.get("score_version") != EXPECTED_SCORE_VERSION
            or not isinstance(generated_at, str)
            or quality_generated_at != generated_at
            or not isinstance(employer_count, int)
            or isinstance(employer_count, bool)
            or employer_count <= 0
            or not isinstance(institution_count, int)
            or isinstance(institution_count, bool)
            or institution_count <= 0
        ):
            raise ReleaseValidationError(
                "Release quality or Product A build metadata is inconsistent."
            )
        checks = quality.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or any(
                not isinstance(check, Mapping)
                or not isinstance(check.get("check_id"), str)
                or not isinstance(check.get("critical"), bool)
                or check.get("status") not in {"PASS", "WARN", "FAIL"}
                for check in checks
            )
            or any(
                check.get("critical") is True and check.get("status") == "FAIL" for check in checks
            )
        ):
            raise ReleaseValidationError("Release quality checks contain a critical failure.")
        try:
            build_timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ReleaseValidationError("Release build timestamp is invalid.") from error
        if build_timestamp.utcoffset() is None:
            raise ReleaseValidationError("Release build timestamp must include a time zone.")
        build_date = build_timestamp.date()
        if tag != f"data-{build_date.isoformat()}":
            raise ReleaseValidationError("Release tag does not match its build date.")

        database_counts = self._database_schema_is_product_a(paths["immigration.duckdb"])
        if database_counts != (employer_count, institution_count):
            raise ReleaseValidationError("Release metadata row counts do not match its database.")
        return _ValidatedRelease(
            tag=tag,
            build_id=build_id,
            generated_at=generated_at,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _runtime(
        directory: Path, validated: _ValidatedRelease, *, cache_hit: bool
    ) -> ReleaseRuntime:
        return ReleaseRuntime(
            database_path=directory / "immigration.duckdb",
            quality_path=directory / "data-quality.json",
            metadata_path=directory / "build-metadata.json",
            checksums_path=directory / "checksums.sha256",
            release_tag=validated.tag,
            build_id=validated.build_id,
            generated_at=validated.generated_at,
            release_fingerprint=validated.fingerprint,
            cache_hit=cache_hit,
        )

    def _cached_runtime(self) -> ReleaseRuntime | None:
        pointer_path = self._repository_cache / "current.json"
        if not pointer_path.is_file():
            return None
        try:
            pointer = _read_json(pointer_path, maximum_bytes=64 * 1024)
            relative_directory = pointer.get("directory")
            if not isinstance(relative_directory, str):
                return None
            repository_cache = self._repository_cache.resolve()
            directory = (repository_cache / relative_directory).resolve()
            if repository_cache not in directory.parents:
                return None
            validated = self._validate_generation(directory)
            runtime = self._runtime(directory, validated, cache_hit=True)
            if self.requested_tag != "latest" and runtime.release_tag != self.requested_tag:
                return None
            return runtime
        except ReleaseValidationError:
            return None

    def _download_and_promote(self, descriptor: _ReleaseDescriptor) -> ReleaseRuntime:
        repository_cache = self._repository_cache
        generations = repository_cache / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=repository_cache))
        try:
            _write_json_atomic(
                staging / ".release.json",
                {
                    "release_tag": descriptor.tag,
                    "release_fingerprint": descriptor.fingerprint,
                    "asset_sha256": {asset.name: asset.sha256 for asset in descriptor.assets},
                },
            )
            for asset in descriptor.assets:
                self._download_asset(asset, staging / asset.name)
            validated = self._validate_generation(
                staging,
                expected_tag=descriptor.tag,
                expected_fingerprint=descriptor.fingerprint,
            )
            generation = generations / (
                f"{descriptor.tag}-{validated.build_id}-{uuid.uuid4().hex[:12]}"
            )
            os.replace(staging, generation)
            relative_generation = generation.relative_to(repository_cache).as_posix()
            _write_json_atomic(
                repository_cache / "current.json",
                {
                    "directory": relative_generation,
                    "release_tag": validated.tag,
                    "build_id": validated.build_id,
                    "release_fingerprint": validated.fingerprint,
                },
            )
            return self._runtime(generation, validated, cache_hit=False)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def ensure(self) -> ReleaseRuntime:
        """Return the current verified release, using cache only for a network outage."""

        repository_cache = self._repository_cache
        repository_cache.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(repository_cache / ".bootstrap.lock"))
        try:
            with lock.acquire(timeout=self.lock_timeout_seconds):
                cached = self._cached_runtime()
                try:
                    descriptor = self._release_descriptor()
                except ReleaseNetworkError:
                    if cached is not None:
                        return cached
                    raise ReleaseNetworkError(
                        "GitHub is unavailable and no verified cached release exists."
                    ) from None
                if cached is not None and cached.release_fingerprint == descriptor.fingerprint:
                    return cached
                try:
                    return self._download_and_promote(descriptor)
                except ReleaseNetworkError:
                    if cached is not None:
                        return cached
                    raise ReleaseNetworkError(
                        "Release download failed and no verified cached release exists."
                    ) from None
        except FileLockTimeout:
            raise ReleaseBootstrapError("Timed out waiting for the release cache lock.") from None


def bootstrap_release(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ReleaseRuntime:
    """Resolve hosted release settings without exposing the token to callers or logs."""

    if settings.deployment_mode is not DeploymentMode.RELEASE or not settings.require_data:
        raise ReleaseBootstrapError("Release bootstrap requires fail-closed deployment mode.")
    if settings.github_release_read_token is None:
        raise ReleaseBootstrapError("A GitHub release read token is required.")
    with ReleaseBootstrap(
        repository=settings.github_repository,
        release_tag=settings.release_tag,
        token=settings.github_release_read_token.get_secret_value(),
        cache_dir=settings.release_cache_dir,
        transport=transport,
    ) as bootstrap:
        return bootstrap.ensure()
