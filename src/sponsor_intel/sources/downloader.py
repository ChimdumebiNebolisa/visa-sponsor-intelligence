"""Immutable, bounded source-artifact downloads."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sponsor_intel.sources.errors import DownloadError
from sponsor_intel.sources.http_client import OfficialHttpClient, RetryableHttpError
from sponsor_intel.sources.models import DownloadedArtifact, SourceArtifactCandidate, SourceConfig


def _safe_directory(root: Path, source_id: str, fiscal_year: int) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / source_id / f"fy={fiscal_year}").resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise DownloadError(f"Raw artifact path escaped its configured root: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


_FORMAT_SUFFIXES = {"csv": ".csv", "pdf": ".pdf", "xlsx": ".xlsx", "zip": ".zip"}


def _validate_zip_archive(
    path: Path,
    *,
    max_uncompressed_bytes: int,
    require_xlsx_members: bool = False,
) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 10_000:
                raise DownloadError(f"ZIP member count is invalid: {path.name}")
            names = {info.filename for info in infos}
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if require_xlsx_members and not required.issubset(names):
                raise DownloadError(f"File is not a valid XLSX workbook: {path.name}")
            total_uncompressed = 0
            for info in infos:
                member = PurePosixPath(info.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise DownloadError(f"Unsafe XLSX member path: {info.filename}")
                total_uncompressed += info.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise DownloadError(
                        f"ZIP expands beyond {max_uncompressed_bytes} bytes: {path.name}"
                    )
                if info.file_size > 10_000_000 and info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > 1000:
                        raise DownloadError(
                            f"Suspicious ZIP compression ratio {ratio:.0f}:1: {path.name}"
                        )
    except zipfile.BadZipFile as error:
        raise DownloadError(f"Downloaded file is not a valid ZIP archive: {path.name}") from error


def _validate_csv(path: Path) -> None:
    with path.open("rb") as source:
        prefix = source.read(4096)
    if not prefix or b"\x00" in prefix:
        raise DownloadError(f"Downloaded file is not a valid text CSV: {path.name}")
    lowered = prefix.lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html")):
        raise DownloadError(f"Downloaded CSV contains an HTML response: {path.name}")


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as source:
        prefix = source.read(5)
    if prefix != b"%PDF-":
        raise DownloadError(f"Downloaded file is not a valid PDF: {path.name}")


def _safe_file_stem(file_name: str) -> str:
    stem = Path(file_name).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    if not safe:
        raise DownloadError(f"Artifact filename has no safe stem: {file_name}")
    return safe[:180]


class ArtifactDownloader:
    """Download official files to content-addressed immutable raw paths."""

    def __init__(
        self,
        config: SourceConfig,
        client: OfficialHttpClient,
        raw_root: Path,
    ) -> None:
        self.config = config
        self.client = client
        self.raw_root = raw_root

    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact:
        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, RetryableHttpError)),
            reraise=True,
        )
        return retrying(self._download_once, candidate)

    def _download_once(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact:
        if candidate.source_id != self.config.id:
            raise DownloadError(
                f"Candidate source {candidate.source_id} does not match {self.config.id}"
            )
        if candidate.expected_format not in self.config.expected_formats:
            raise DownloadError(
                f"Unexpected artifact format {candidate.expected_format}: {candidate.file_name}"
            )
        try:
            suffix = _FORMAT_SUFFIXES[candidate.expected_format]
        except KeyError as error:
            raise DownloadError(
                f"Unsupported artifact format {candidate.expected_format}: {candidate.file_name}"
            ) from error
        target_directory = _safe_directory(
            self.raw_root, candidate.source_id, candidate.fiscal_year
        )
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.candidate_id}-", suffix=".part", dir=target_directory
        )
        temporary_path = Path(temporary_name)
        hasher = hashlib.sha256()
        byte_size = 0
        mime_type = "application/octet-stream"
        etag: str | None = None
        last_modified: str | None = None
        try:
            with os.fdopen(handle, "wb") as destination:
                with self.client.stream(candidate.download_url) as response:
                    content_length = response.headers.get("content-length")
                    if (
                        content_length is not None
                        and int(content_length) > self.config.max_download_bytes
                    ):
                        raise DownloadError(
                            f"Artifact exceeds {self.config.max_download_bytes} bytes: "
                            f"{candidate.download_url}"
                        )
                    mime_type = response.headers.get("content-type", mime_type).split(";", 1)[0]
                    if mime_type.casefold() in {"text/html", "application/xhtml+xml"}:
                        raise DownloadError(
                            f"Expected {candidate.expected_format}, received {mime_type}: "
                            f"{candidate.download_url}"
                        )
                    etag = response.headers.get("etag")
                    last_modified = response.headers.get("last-modified")
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        byte_size += len(chunk)
                        if byte_size > self.config.max_download_bytes:
                            raise DownloadError(
                                f"Artifact exceeded {self.config.max_download_bytes} bytes while "
                                f"streaming: {candidate.download_url}"
                            )
                        hasher.update(chunk)
                        destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            if byte_size == 0:
                raise DownloadError(f"Downloaded artifact is empty: {candidate.download_url}")
            if candidate.expected_format == "xlsx":
                _validate_zip_archive(
                    temporary_path,
                    max_uncompressed_bytes=self.config.max_uncompressed_bytes,
                    require_xlsx_members=True,
                )
            elif candidate.expected_format == "zip":
                _validate_zip_archive(
                    temporary_path,
                    max_uncompressed_bytes=self.config.max_uncompressed_bytes,
                )
            elif candidate.expected_format == "csv":
                _validate_csv(temporary_path)
            elif candidate.expected_format == "pdf":
                _validate_pdf(temporary_path)

            checksum = hasher.hexdigest()
            safe_stem = _safe_file_stem(candidate.file_name)
            final_path = target_directory / f"{safe_stem}-{checksum[:16]}{suffix}"
            cache_hit = final_path.exists()
            if cache_hit:
                if final_path.stat().st_size != byte_size:
                    raise DownloadError(
                        f"Content-addressed raw path has unexpected size: {final_path}"
                    )
                temporary_path.unlink()
            else:
                os.replace(temporary_path, final_path)
            return DownloadedArtifact(
                candidate=candidate,
                raw_path=final_path,
                retrieved_at=datetime.now(UTC),
                sha256=checksum,
                byte_size=byte_size,
                mime_type=mime_type,
                etag=etag,
                last_modified=last_modified,
                cache_hit=cache_hit,
            )
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
