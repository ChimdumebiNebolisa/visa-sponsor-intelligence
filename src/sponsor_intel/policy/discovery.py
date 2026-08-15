"""Official-domain policy discovery with bounded sitemap and OpenAI fallback paths."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse
from xml.etree import ElementTree

import yaml
from openai import OpenAI

from sponsor_intel.policy.fetcher import normalize_official_domain
from sponsor_intel.policy.models import (
    DiscoveredPolicyDocument,
    PolicyCandidate,
    PolicyDiscoveryResult,
)
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url

DISCOVERY_VERSION = "policy_discovery_v1"
DISCOVERY_CACHE_TTL_DAYS = 120
POLICY_KEYWORDS = (
    "h-1b",
    "h1b",
    "permanent-residence",
    "permanent-residency",
    "green-card",
    "employment-based",
    "international-scholar",
    "immigration",
    "eb-1b",
    "eb1b",
    "perm",
)


class ResponsesClient(Protocol):
    """Narrow client seam used by production and mocked extraction tests."""

    responses: Any


def _classify_document(url: str) -> str:
    lowered = url.casefold()
    for keyword, document_type in (
        ("permanent", "permanent_residence_policy"),
        ("green-card", "permanent_residence_policy"),
        ("eb1", "eb1b_guidance"),
        ("eb-1", "eb1b_guidance"),
        ("perm", "perm_guidance"),
        ("postdoc", "postdoctoral_immigration_policy"),
        ("h1b", "h1b_sponsorship_policy"),
        ("h-1b", "h1b_sponsorship_policy"),
    ):
        if keyword in lowered:
            return document_type
    return "international_scholar_policy"


def _url_score(url: str) -> int:
    normalized = url.casefold().replace("_", "-")
    return sum(1 for keyword in POLICY_KEYWORDS if keyword in normalized)


def _parse_sitemap(content: str) -> tuple[list[str], list[str]]:
    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        return [], []
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return [], []
    local_name = root.tag.rsplit("}", 1)[-1]
    locations = [
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "loc" and (node.text or "").strip()
    ]
    if local_name == "sitemapindex":
        return [], locations
    return locations, []


def discover_from_sitemaps(
    candidate: PolicyCandidate, *, max_sitemaps: int = 4
) -> list[DiscoveredPolicyDocument]:
    """Inspect robots and a bounded set of sitemaps for policy-looking official URLs."""

    domain = normalize_official_domain(candidate.official_domain)
    sitemap_urls = [f"https://{domain}/sitemap.xml"]
    with OfficialHttpClient((domain,)) as client:
        try:
            robots = client.get_text(f"https://{domain}/robots.txt", max_bytes=1_000_000)
        except Exception:
            robots = ""
        for line in robots.splitlines():
            if line.casefold().startswith("sitemap:"):
                value = line.split(":", 1)[1].strip()
                try:
                    validate_official_url(value, (domain,))
                except Exception:
                    continue
                sitemap_urls.append(value)
        pages: list[str] = []
        queued = list(dict.fromkeys(sitemap_urls))
        visited: set[str] = set()
        while queued and len(visited) < max_sitemaps:
            sitemap_url = queued.pop(0)
            if sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            try:
                content = client.get_text(sitemap_url, max_bytes=6_000_000)
            except Exception:
                continue
            discovered_pages, child_sitemaps = _parse_sitemap(content)
            pages.extend(discovered_pages)
            for child in child_sitemaps:
                try:
                    validate_official_url(child, (domain,))
                except Exception:
                    continue
                if child not in visited:
                    queued.append(child)
    selected = sorted(
        {
            url
            for url in pages
            if _url_score(url) > 0
            and urlparse(url).scheme == "https"
            and not url.casefold().endswith((".jpg", ".jpeg", ".png", ".gif", ".zip"))
        },
        key=lambda value: (-_url_score(value), len(value), value),
    )[:5]
    return [
        DiscoveredPolicyDocument(
            url=url,
            title=url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " "),
            document_type=_classify_document(url),
            relevance_reason="Official sitemap URL contains immigration-policy keywords.",
        )
        for url in selected
    ]


class PolicySeedRegistry:
    """Reviewed deterministic discovery overrides for difficult official sites."""

    def __init__(
        self,
        values: dict[str, tuple[DiscoveredPolicyDocument, ...]],
        *,
        manual_priorities: tuple[str, ...] = (),
        additional_domains: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._values = values
        self.manual_priorities = manual_priorities
        self._additional_domains = additional_domains or {}

    @classmethod
    def from_yaml(cls, path: Path) -> PolicySeedRegistry:
        if not path.is_file():
            return cls({})
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = loaded.get("documents", []) if isinstance(loaded, dict) else []
        priorities = (
            loaded.get("manual_priority_institutions", []) if isinstance(loaded, dict) else []
        )
        additional_domains = (
            loaded.get("additional_official_domains", {}) if isinstance(loaded, dict) else {}
        )
        if not isinstance(entries, list):
            raise ValueError("Policy source registry documents must be a list")
        if not isinstance(priorities, list) or not all(
            isinstance(priority, str) for priority in priorities
        ):
            raise ValueError("Policy manual priorities must be a list of institution names")
        if not isinstance(additional_domains, dict) or not all(
            isinstance(institution_id, str)
            and isinstance(domains, list)
            and all(isinstance(domain, str) for domain in domains)
            for institution_id, domains in additional_domains.items()
        ):
            raise ValueError(
                "Policy additional official domains must map institution IDs to domain lists"
            )
        values: dict[str, list[DiscoveredPolicyDocument]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("institution_id"), str):
                raise ValueError("Every policy source must include institution_id")
            document = DiscoveredPolicyDocument.model_validate(
                {key: value for key, value in entry.items() if key != "institution_id"}
            )
            values.setdefault(entry["institution_id"], []).append(document)
        return cls(
            {key: tuple(items) for key, items in values.items()},
            manual_priorities=tuple(priorities),
            additional_domains={
                institution_id: tuple(normalize_official_domain(domain) for domain in domains)
                for institution_id, domains in additional_domains.items()
            },
        )

    def get(self, institution_id: str) -> tuple[DiscoveredPolicyDocument, ...]:
        return self._values.get(institution_id, ())

    def domains_for(self, candidate: PolicyCandidate) -> tuple[str, ...]:
        """Return reviewed campus and system domains permitted for this institution."""

        primary = normalize_official_domain(candidate.official_domain)
        return tuple(
            dict.fromkeys((primary, *self._additional_domains.get(candidate.institution_id, ())))
        )


class OpenAIPolicyDiscoverer:
    """Use domain-filtered OpenAI web search only after deterministic discovery fails."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        data_root: Path = Path("data"),
        client: ResponsesClient | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("OPENAI_POLICY_MODEL is required for OpenAI policy discovery")
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI policy discovery")
        self.model = model
        self.data_root = data_root
        self.client = (
            client if client is not None else cast(ResponsesClient, OpenAI(api_key=api_key))
        )
        self.api_call_count = 0
        self.cache_hit_count = 0

    def _cache_path(self, candidate: PolicyCandidate) -> Path:
        cache_key = hashlib.sha256(
            f"{DISCOVERY_VERSION}|{self.model}|{candidate.institution_id}|"
            f"{normalize_official_domain(candidate.official_domain)}".encode()
        ).hexdigest()
        return self.data_root / "cache" / "policy_discovery" / f"{cache_key}.json"

    def _read_cache(self, candidate: PolicyCandidate) -> list[DiscoveredPolicyDocument] | None:
        path = self._cache_path(candidate)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            discovered_at = datetime.fromisoformat(payload["discovered_at"])
            if discovered_at.tzinfo is None:
                discovered_at = discovered_at.replace(tzinfo=UTC)
            if discovered_at < datetime.now(UTC) - timedelta(days=DISCOVERY_CACHE_TTL_DAYS):
                return None
            result = PolicyDiscoveryResult.model_validate(payload["result"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        self.cache_hit_count += 1
        return self._validated(candidate, result.documents)

    def _validated(
        self,
        candidate: PolicyCandidate,
        documents: list[DiscoveredPolicyDocument],
    ) -> list[DiscoveredPolicyDocument]:
        domain = normalize_official_domain(candidate.official_domain)
        selected: list[DiscoveredPolicyDocument] = []
        seen: set[str] = set()
        for document in documents:
            if document.url in seen:
                continue
            try:
                validate_official_url(document.url, (domain,))
            except Exception:
                continue
            seen.add(document.url)
            selected.append(document)
        return selected[:5]

    def discover(self, candidate: PolicyCandidate) -> list[DiscoveredPolicyDocument]:
        cached = self._read_cache(candidate)
        if cached is not None:
            return cached
        domain = normalize_official_domain(candidate.official_domain)
        response = self.client.responses.parse(
            model=self.model,
            reasoning={"effort": "none"},
            instructions=(
                "Find official institution policy pages only. Return URLs hosted on the supplied "
                "allowed domain that address H-1B, employment-based permanent residence, faculty, "
                "research staff, general staff, postdocs, PERM, EB-1B, or international scholars. "
                "Do not return search snippets, third-party summaries, social media, or attorney "
                "sites. "
                "Return at most five high-value source documents."
            ),
            input=(
                f"Institution: {candidate.official_name}\n"
                f"Official domain: {domain}\n"
                "Locate the most authoritative current immigration sponsorship policy documents."
            ),
            tools=[
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": [domain]},
                    "search_context_size": "medium",
                }
            ],
            include=["web_search_call.action.sources"],
            text_format=PolicyDiscoveryResult,
            max_output_tokens=2_000,
            store=False,
        )
        self.api_call_count += 1
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError(
                f"OpenAI policy discovery returned no parsed output for {candidate.official_name}"
            )
        result = PolicyDiscoveryResult.model_validate(parsed)
        selected = self._validated(candidate, result.documents)
        path = self._cache_path(candidate)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "discovery_version": DISCOVERY_VERSION,
            "model_name": self.model,
            "model_response_id": response.id,
            "discovered_at": datetime.now(UTC).isoformat(),
            "result": PolicyDiscoveryResult(documents=selected).model_dump(mode="json"),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return selected
