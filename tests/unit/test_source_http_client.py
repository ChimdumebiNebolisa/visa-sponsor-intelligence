"""Security boundary tests for official-source HTTP access."""

from __future__ import annotations

import httpx
import pytest

from sponsor_intel.sources.errors import DownloadError, UnsafeSourceUrlError
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url


def test_official_domain_validation_rejects_sibling_domain() -> None:
    with pytest.raises(UnsafeSourceUrlError):
        validate_official_url("https://notdol.gov/file.xlsx", ("dol.gov",))


def test_redirect_to_untrusted_domain_is_rejected_before_following() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://evil.example/payload.xlsx"},
            request=request,
        )

    with (
        OfficialHttpClient(("dol.gov",), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(UnsafeSourceUrlError),
    ):
        client.get_text("https://www.dol.gov/performance")

    assert requested_urls == ["https://www.dol.gov/performance"]


def test_text_response_limit_is_enforced_while_streaming() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"too many bytes", request=request)

    with (
        OfficialHttpClient(("dol.gov",), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(DownloadError, match="exceeded 4 bytes"),
    ):
        client.get_text("https://www.dol.gov/performance", max_bytes=4)
