"""HTTP access restricted to explicitly configured official domains."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sponsor_intel.sources.errors import DownloadError, UnsafeSourceUrlError

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SponsorIntel/0.1; "
    "+https://github.com/ChimdumebiNebolisa/visa-sponsor-intelligence)"
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class RetryableHttpError(DownloadError):
    """HTTP status that is safe to retry for a GET request."""


def validate_official_url(url: str, official_domains: tuple[str, ...]) -> str:
    """Reject non-HTTPS, credentialed, non-default-port, or off-domain URLs."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeSourceUrlError(f"Official source URLs must use HTTPS: {url}")
    if parsed.username or parsed.password:
        raise UnsafeSourceUrlError(f"Credentials are not allowed in source URLs: {url}")
    if parsed.port not in (None, 443):
        raise UnsafeSourceUrlError(f"Non-default ports are not allowed in source URLs: {url}")

    hostname = parsed.hostname.casefold().rstrip(".")
    allowed = False
    for configured_domain in official_domains:
        domain = configured_domain.casefold().removeprefix("*.").rstrip(".")
        if hostname == domain or hostname.endswith(f".{domain}"):
            allowed = True
            break
    if not allowed:
        raise UnsafeSourceUrlError(f"URL host is outside the official source domains: {url}")
    return url


class OfficialHttpClient:
    """Bounded GET-only client with redirect and retry validation."""

    def __init__(
        self,
        official_domains: tuple[str, ...],
        *,
        transport: httpx.BaseTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.official_domains = official_domains
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=httpx.Timeout(connect=15, read=120, write=30, pool=30),
            transport=transport,
            follow_redirects=False,
        )

    def __enter__(self) -> OfficialHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @contextmanager
    def stream(self, url: str) -> Iterator[httpx.Response]:
        """Stream one GET response while validating every redirect target."""

        current_url = validate_official_url(url, self.official_domains)
        for _ in range(6):
            request = self._client.build_request("GET", current_url)
            response = self._client.send(request, stream=True)
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise DownloadError(
                        f"Redirect response did not include Location: {current_url}"
                    )
                current_url = validate_official_url(
                    urljoin(current_url, location), self.official_domains
                )
                continue
            if response.status_code in _RETRYABLE_STATUSES:
                response.close()
                raise RetryableHttpError(f"Retryable HTTP {response.status_code}: {current_url}")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                response.close()
                raise DownloadError(f"HTTP request failed for {current_url}: {error}") from error
            try:
                yield response
            finally:
                response.close()
            return
        raise DownloadError(f"Too many redirects while requesting {url}")

    def get_text(self, url: str, *, max_bytes: int = 10_000_000) -> str:
        """Fetch bounded UTF-8/HTTP-decoded text with safe retries."""

        def request_once() -> str:
            with self.stream(url) as response:
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > max_bytes:
                        raise DownloadError(f"Text response exceeded {max_bytes} bytes: {url}")
                    content.extend(chunk)
                return bytes(content).decode(response.encoding or "utf-8", errors="strict")

        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception_type((httpx.TransportError, RetryableHttpError)),
            reraise=True,
        )
        return retrying(request_once)
