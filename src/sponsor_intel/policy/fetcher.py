"""Bounded retrieval and parsing of official institution policy documents."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pdfplumber
import polars as pl
from playwright.sync_api import Browser, Playwright, Route, sync_playwright
from selectolax.parser import HTMLParser

from sponsor_intel.policy.models import (
    DiscoveredPolicyDocument,
    ParseStatus,
    PolicyCandidate,
    PolicyDocument,
)
from sponsor_intel.sources.errors import DownloadError
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url

MAX_POLICY_BYTES = 24 * 1024 * 1024
MAX_PARSED_CHARACTERS = 1_500_000
FETCH_REUSE_TTL_HOURS = 24
_SUSPICIOUS_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?", re.I),
    re.compile(r"reveal|exfiltrat|send\s+(?:the\s+)?(?:api\s+key|secret|system\s+prompt)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.I),
    re.compile(r"<\s*(?:system|assistant|developer)\s*>", re.I),
)


def normalize_official_domain(value: str) -> str:
    """Normalize an IPEDS web address into a conservative official-domain root."""

    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname or "." not in hostname:
        raise ValueError(f"IPEDS official domain is invalid: {value}")
    return hostname


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._=-]+", "_", value).strip("._")
    if not safe:
        raise ValueError("Policy artifact path segment is empty")
    return safe[:160]


def _atomic_write(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_bytes() != content:
            raise DownloadError(f"Content-addressed policy path changed unexpectedly: {path}")
        return True
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return False


def _meta_content(tree: HTMLParser, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        node = tree.css_first(selector)
        if node is not None:
            content = node.attributes.get("content") or node.attributes.get("datetime")
            if content and content.strip():
                return content.strip()
    return None


def _html_text(content: bytes, encoding: str) -> tuple[str, str, str | None]:
    try:
        decoded = content.decode(encoding, errors="strict")
    except (LookupError, UnicodeDecodeError):
        decoded = content.decode("utf-8", errors="replace")
    tree = HTMLParser(decoded)
    for selector in ("script", "style", "noscript", "svg", "nav", "footer", "form"):
        for node in tree.css(selector):
            node.decompose()
    root = tree.css_first("main") or tree.css_first('[role="main"]') or tree.css_first("article")
    root = root or tree.body
    if root is None:
        return "", "Untitled policy page", None
    lines: list[str] = []
    for node in root.css("h1, h2, h3, h4, h5, p, li, th, td"):
        value = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()
        if not value:
            continue
        tag = node.tag.casefold()
        if tag.startswith("h") and len(tag) == 2:
            value = f"[HEADING {tag[1]}] {value}"
        if not lines or lines[-1] != value:
            lines.append(value)
    text = "\n".join(lines)[:MAX_PARSED_CHARACTERS]
    title_node = tree.css_first("title") or tree.css_first("h1")
    title = (
        re.sub(r"\s+", " ", title_node.text(separator=" ", strip=True)).strip()
        if title_node is not None
        else "Untitled policy page"
    )
    published = _meta_content(
        tree,
        (
            'meta[property="article:modified_time"]',
            'meta[property="article:published_time"]',
            'meta[name="last-modified"]',
            'meta[name="date"]',
            "time[datetime]",
        ),
    )
    return text, title, published


def _pdf_text(content: bytes) -> tuple[str, str | None]:
    pages: list[str] = []
    with pdfplumber.open(BytesIO(content)) as document:
        for number, page in enumerate(document.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            pages.append(f"[PAGE {number}]\n{text.strip()}")
            if sum(len(value) for value in pages) >= MAX_PARSED_CHARACTERS:
                break
        metadata = document.metadata or {}
    return "\n\n".join(pages)[:MAX_PARSED_CHARACTERS], metadata.get("ModDate")


def _published_is_current(value: str | None, retrieved_at: datetime) -> bool:
    if value is None:
        return False
    match = re.search(r"(20\d{2})[-/:]?(0[1-9]|1[0-2])[-/:]?([0-2]\d|3[01])", value)
    if not match:
        return False
    try:
        published = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=UTC
        )
    except ValueError:
        return False
    return published >= retrieved_at - timedelta(days=548)


def contains_prompt_injection(text: str) -> bool:
    """Flag source text that attempts to act as model instructions."""

    sample = text[:500_000]
    return any(pattern.search(sample) is not None for pattern in _SUSPICIOUS_PATTERNS)


class PolicyDocumentFetcher:
    """Fetch official HTML/PDF sources into immutable evidence paths."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        transport: httpx.BaseTransport | None = None,
        browser_fallback: bool = True,
    ) -> None:
        self.data_root = data_root
        self.transport = transport
        self.browser_fallback = browser_fallback
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._recent_documents: dict[tuple[str, str], PolicyDocument] | None = None

    def close(self) -> None:
        """Close the lazily created browser fallback, if it was needed."""

        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _browser_instance(self) -> Browser:
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _retrieve_with_browser(
        self,
        url: str,
        domains: tuple[str, ...],
    ) -> tuple[bytes, str, str, int]:
        context = self._browser_instance().new_context(accept_downloads=False)
        page = context.new_page()

        def domain_confined(route: Route) -> None:
            request_url = route.request.url
            if request_url.startswith(("data:", "blob:")):
                route.continue_()
                return
            try:
                validate_official_url(request_url, domains)
            except Exception:
                route.abort()
                return
            route.continue_()

        page.route("**/*", domain_confined)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if response is None:
                raise DownloadError(f"Browser navigation returned no response: {url}")
            validate_official_url(page.url, domains)
            if response.status >= 400:
                raise DownloadError(f"Browser HTTP {response.status}: {page.url}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if content_type == "application/pdf":
                payload = response.body()
            else:
                payload = page.content().encode()
                content_type = content_type or "text/html"
            if len(payload) > MAX_POLICY_BYTES:
                raise DownloadError(
                    f"Browser policy response exceeded {MAX_POLICY_BYTES} bytes: {url}"
                )
            return payload, content_type, "utf-8", response.status
        finally:
            context.close()

    def _retrieve(self, url: str, domains: tuple[str, ...]) -> tuple[bytes, str, str, int]:
        try:
            with (
                OfficialHttpClient(domains, transport=self.transport) as client,
                client.stream(url) as response,
            ):
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=128 * 1024):
                    if len(content) + len(chunk) > MAX_POLICY_BYTES:
                        raise DownloadError(
                            f"Policy document exceeded {MAX_POLICY_BYTES} bytes: {url}"
                        )
                    content.extend(chunk)
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                encoding = response.encoding or "utf-8"
                status = response.status_code
            return bytes(content), content_type, encoding, status
        except (DownloadError, httpx.TransportError):
            if not self.browser_fallback or self.transport is not None:
                raise
            return self._retrieve_with_browser(url, domains)

    def _load_recent_documents(self) -> dict[tuple[str, str], PolicyDocument]:
        if self._recent_documents is None:
            self._recent_documents = {}
            path = self.data_root / "processed" / "policy_documents.parquet"
            if path.is_file():
                for row in pl.read_parquet(path).iter_rows(named=True):
                    try:
                        document = PolicyDocument.model_validate(row)
                    except ValueError:
                        continue
                    key = (document.institution_id, document.url)
                    previous = self._recent_documents.get(key)
                    if previous is None or document.retrieved_at > previous.retrieved_at:
                        self._recent_documents[key] = document
        return self._recent_documents

    @staticmethod
    def _is_reusable(document: PolicyDocument) -> bool:
        if document.parse_status is not ParseStatus.PARSED:
            return False
        retrieved_at = document.retrieved_at
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        return (
            retrieved_at >= datetime.now(UTC) - timedelta(hours=FETCH_REUSE_TTL_HOURS)
            and document.raw_path.is_file()
            and document.parsed_text_path.is_file()
        )

    def recent_sources(
        self,
        candidate: PolicyCandidate,
        *,
        limit: int = 5,
    ) -> list[DiscoveredPolicyDocument]:
        """Return recent completed official documents without repeating discovery."""

        documents = [
            document
            for (institution_id, _), document in self._load_recent_documents().items()
            if institution_id == candidate.institution_id and self._is_reusable(document)
        ]
        documents.sort(key=lambda document: document.retrieved_at, reverse=True)
        return [
            DiscoveredPolicyDocument(
                url=document.url,
                title=document.title,
                document_type=document.document_type,
                relevance_reason="Recent completed official document reused within 24 hours.",
            )
            for document in documents[: max(1, limit)]
        ]

    def _recent_document(
        self,
        candidate: PolicyCandidate,
        discovered: DiscoveredPolicyDocument,
    ) -> PolicyDocument | None:
        document = self._load_recent_documents().get((candidate.institution_id, discovered.url))
        if document is None:
            return None
        if not self._is_reusable(document):
            return None
        return document.model_copy(update={"cache_hit": True})

    def fetch(
        self,
        candidate: PolicyCandidate,
        discovered: DiscoveredPolicyDocument,
        *,
        discovery_method: str,
        official_domains: tuple[str, ...] | None = None,
    ) -> PolicyDocument:
        primary_domain = normalize_official_domain(candidate.official_domain)
        domains = official_domains or (primary_domain,)
        validate_official_url(discovered.url, domains)
        source_host = (urlparse(discovered.url).hostname or "").casefold().rstrip(".")
        evidence_domain = max(
            (
                domain
                for domain in domains
                if source_host == domain or source_host.endswith(f".{domain}")
            ),
            key=len,
        )
        recent = self._recent_document(candidate, discovered)
        if recent is not None:
            return recent
        retrieved_at = datetime.now(UTC)
        payload, content_type, encoding, status = self._retrieve(discovered.url, domains)
        if not payload:
            raise DownloadError(f"Policy document is empty: {discovered.url}")
        content_sha256 = hashlib.sha256(payload).hexdigest()
        lowered_prefix = payload[:256].lstrip().lower()
        if content_type == "application/pdf" or payload.startswith(b"%PDF-"):
            suffix = ".pdf"
            parsed_text, published = _pdf_text(payload)
            title = discovered.title or Path(urlparse(discovered.url).path).name
        elif content_type in {
            "text/html",
            "application/xhtml+xml",
            "",
        } or lowered_prefix.startswith((b"<!doctype html", b"<html")):
            suffix = ".html"
            parsed_text, parsed_title, published = _html_text(payload, encoding)
            title = discovered.title or parsed_title
            if title == "Untitled policy page":
                title = parsed_title
        else:
            raise DownloadError(
                f"Unsupported policy content type {content_type or 'unknown'}: {discovered.url}"
            )
        parsed_text = parsed_text.strip()
        suspicious = contains_prompt_injection(parsed_text)
        if suspicious:
            parse_status = ParseStatus.SUSPICIOUS
        elif not parsed_text:
            parse_status = ParseStatus.EMPTY
        else:
            parse_status = ParseStatus.PARSED
        text_sha256 = hashlib.sha256(parsed_text.encode("utf-8")).hexdigest()
        policy_document_id = hashlib.sha256(
            f"{candidate.institution_id}|{discovered.url}|{content_sha256}".encode()
        ).hexdigest()[:32]
        institution_segment = _safe_segment(candidate.institution_id)
        raw_path = (
            self.data_root
            / "raw"
            / "policy"
            / f"institution={institution_segment}"
            / f"{content_sha256}{suffix}"
        )
        parsed_path = (
            self.data_root
            / "staging"
            / "policy"
            / f"institution={institution_segment}"
            / f"{text_sha256}.txt"
        )
        raw_cache_hit = _atomic_write(raw_path, payload)
        text_cache_hit = _atomic_write(parsed_path, parsed_text.encode("utf-8"))
        return PolicyDocument(
            policy_document_id=policy_document_id,
            institution_id=candidate.institution_id,
            document_type=discovered.document_type,
            title=title[:500],
            url=discovered.url,
            official_domain=evidence_domain,
            retrieved_at=retrieved_at,
            http_status=status,
            content_type=content_type or ("application/pdf" if suffix == ".pdf" else "text/html"),
            content_sha256=content_sha256,
            text_sha256=text_sha256,
            published_or_updated_date=published,
            raw_path=raw_path,
            parsed_text_path=parsed_path,
            is_current=_published_is_current(published, retrieved_at),
            parse_status=parse_status,
            discovery_method=discovery_method,
            suspicious_text=suspicious,
            cache_hit=raw_cache_hit and text_cache_hit,
        )
