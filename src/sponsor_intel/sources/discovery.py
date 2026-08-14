"""DOL disclosure-link discovery and canonical fiscal-year selection."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

from sponsor_intel.sources.errors import SourceDiscoveryError, UnsafeSourceUrlError
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url
from sponsor_intel.sources.models import DiscoveryReport, SourceArtifactCandidate, SourceConfig

_FISCAL_YEAR = re.compile(r"FY[ _-]?(20\d{2})", re.IGNORECASE)
_QUARTER = re.compile(r"(?:_|\b)Q([1-4])(?:\b|\.)", re.IGNORECASE)


def _artifact_metadata(text: str, url: str) -> tuple[int, int | None] | None:
    combined = f"{text} {unquote(url)}"
    fiscal_year_match = _FISCAL_YEAR.search(combined)
    if fiscal_year_match is None:
        return None
    quarter_match = _QUARTER.search(combined)
    quarter = int(quarter_match.group(1)) if quarter_match is not None else None
    return int(fiscal_year_match.group(1)), quarter


def _matches_disclosure(source_id: str, text: str, file_name: str) -> bool:
    combined = f"{text} {file_name}".casefold()
    if not file_name.casefold().endswith(".xlsx"):
        return False
    if source_id == "dol_lca":
        return (
            "lca" in combined
            and "data" in combined
            and re.search(r"dis\w*closure", combined) is not None
            and "appendix" not in combined
            and "worksite" not in combined
        )
    if source_id == "dol_perm":
        return "perm" in combined and "disclosure" in combined and "data" in combined
    raise SourceDiscoveryError(f"Unsupported DOL disclosure source: {source_id}")


def _matches_record_layout(source_id: str, text: str, file_name: str) -> bool:
    combined = f"{text} {file_name}".casefold()
    if not file_name.casefold().endswith(".pdf") or "record" not in combined:
        return False
    if source_id == "dol_lca":
        return "lca" in combined and "appendix" not in combined and "worksite" not in combined
    return "perm" in combined


def _variant(text: str, file_name: str) -> str:
    return "new_form" if "new_form" in f"{text} {file_name}".casefold() else "standard"


def _record_layout_for(
    candidate: SourceArtifactCandidate,
    layouts: list[tuple[int, int | None, str, str]],
) -> str | None:
    same_year = [layout for layout in layouts if layout[0] == candidate.fiscal_year]
    if candidate.variant == "new_form":
        preferred = [layout for layout in same_year if "new_form" in layout[3].casefold()]
        if preferred:
            same_year = preferred
    else:
        same_year = [layout for layout in same_year if "new_form" not in layout[3].casefold()]
    if not same_year:
        return None
    return max(same_year, key=lambda item: item[1] or 4)[2]


def _select_canonical(candidates: list[SourceArtifactCandidate]) -> tuple[str, ...]:
    selected: list[str] = []
    by_year: dict[int, list[SourceArtifactCandidate]] = {}
    for candidate in candidates:
        by_year.setdefault(candidate.fiscal_year, []).append(candidate)
    for yearly_candidates in by_year.values():
        latest_quarter = max(candidate.fiscal_quarter or 4 for candidate in yearly_candidates)
        for candidate in yearly_candidates:
            if (candidate.fiscal_quarter or 4) == latest_quarter:
                selected.append(candidate.candidate_id)
    return tuple(sorted(selected))


def discover_dol_artifacts(
    config: SourceConfig,
    client: OfficialHttpClient,
    *,
    from_fiscal_year: int,
) -> DiscoveryReport:
    """Discover official disclosure artifacts without synthesizing URLs."""

    html = client.get_text(config.landing_page)
    document = HTMLParser(html)
    disclosure_links: list[tuple[str, str, int, int | None, str]] = []
    layouts: list[tuple[int, int | None, str, str]] = []

    for anchor in document.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        text = " ".join(anchor.text(separator=" ", strip=True).split())
        url = urljoin(config.landing_page, href)
        try:
            validate_official_url(url, config.official_domains)
        except UnsafeSourceUrlError:
            continue
        file_name = PurePosixPath(urlparse(url).path).name
        metadata = _artifact_metadata(text, url)
        if metadata is None:
            continue
        fiscal_year, quarter = metadata
        if fiscal_year < max(from_fiscal_year, config.minimum_fiscal_year):
            continue
        if _matches_disclosure(config.id, text, file_name):
            disclosure_links.append((url, file_name, fiscal_year, quarter, text))
        elif _matches_record_layout(config.id, text, file_name):
            layouts.append((fiscal_year, quarter, url, f"{text} {file_name}"))

    deduplicated: dict[str, SourceArtifactCandidate] = {}
    for url, file_name, fiscal_year, quarter, text in disclosure_links:
        candidate = SourceArtifactCandidate(
            source_id=config.id,
            authority=config.authority,
            landing_page_url=config.landing_page,
            download_url=url,
            fiscal_year=fiscal_year,
            fiscal_quarter=quarter,
            is_partial_period=quarter is not None and quarter < 4,
            file_name=file_name,
            expected_format="xlsx",
            variant=_variant(text, file_name),
        )
        candidate = candidate.model_copy(
            update={"record_layout_url": _record_layout_for(candidate, layouts)}
        )
        deduplicated[url] = candidate

    candidates = sorted(
        deduplicated.values(),
        key=lambda item: (
            item.fiscal_year,
            item.fiscal_quarter or 4,
            item.variant,
            item.download_url,
        ),
    )
    if not candidates:
        raise SourceDiscoveryError(
            f"No {config.id} disclosure artifacts found from FY{from_fiscal_year} onward"
        )
    selected_ids = _select_canonical(candidates)
    selected_candidates = [
        candidate for candidate in candidates if candidate.candidate_id in set(selected_ids)
    ]
    if any(candidate.record_layout_url is None for candidate in selected_candidates):
        missing = [
            candidate.file_name
            for candidate in selected_candidates
            if candidate.record_layout_url is None
        ]
        raise SourceDiscoveryError(f"Selected artifacts lack official record layouts: {missing}")

    return DiscoveryReport(
        source_id=config.id,
        discovered_at=datetime.now(UTC),
        from_fiscal_year=from_fiscal_year,
        landing_page_url=config.landing_page,
        candidates=tuple(candidates),
        selected_candidate_ids=selected_ids,
    )
