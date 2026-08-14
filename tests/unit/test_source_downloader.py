"""Tests for bounded immutable artifact downloads."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import httpx
import xlsxwriter

from sponsor_intel.sources.downloader import ArtifactDownloader
from sponsor_intel.sources.http_client import OfficialHttpClient
from sponsor_intel.sources.models import SourceArtifactCandidate
from sponsor_intel.sources.registry import SourceRegistry


def _xlsx_bytes() -> bytes:
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    worksheet = workbook.add_worksheet("Disclosure")
    worksheet.write_row(0, 0, ["CASE_NUMBER", "CASE_STATUS"])
    worksheet.write_row(1, 0, ["I-200-TEST", "Certified"])
    workbook.close()
    return buffer.getvalue()


def _candidate() -> SourceArtifactCandidate:
    return SourceArtifactCandidate(
        source_id="dol_lca",
        authority="U.S. Department of Labor",
        landing_page_url="https://www.dol.gov/performance",
        download_url="https://www.dol.gov/files/lca.xlsx",
        fiscal_year=2025,
        fiscal_quarter=4,
        is_partial_period=False,
        file_name="lca.xlsx",
        expected_format="xlsx",
        record_layout_url="https://www.dol.gov/files/layout.pdf",
    )


def test_download_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    content = _xlsx_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content-length": str(len(content)),
                "etag": '"fixture"',
            },
            request=request,
        )

    config = SourceRegistry.from_yaml().get("dol_lca").model_copy(update={"minimum_row_count": 1})
    with OfficialHttpClient(
        config.official_domains, transport=httpx.MockTransport(handler)
    ) as client:
        downloader = ArtifactDownloader(config, client, tmp_path / "raw")
        first = downloader.download(_candidate())
        second = downloader.download(_candidate())

    assert first.raw_path == second.raw_path
    assert first.raw_path.is_file()
    assert second.cache_hit is True
    assert first.sha256 == hashlib.sha256(content).hexdigest()
