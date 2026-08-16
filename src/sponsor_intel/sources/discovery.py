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
REVIEWED_LCA_COMPLETED_SEGMENTS: dict[int, tuple[tuple[int, int], ...]] = {
    2022: ((1, 1), (2, 2), (3, 3), (4, 4)),
    2023: ((1, 2), (3, 3), (4, 4)),
    2024: ((1, 1), (2, 2), (3, 3), (4, 4)),
    2025: ((1, 1), (2, 2), (3, 3), (4, 4)),
}


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
    same_period = [layout for layout in same_year if layout[1] == candidate.fiscal_quarter]
    if same_period:
        same_year = same_period
    latest_quarter = max(layout[1] or 4 for layout in same_year)
    latest = [layout for layout in same_year if (layout[1] or 4) == latest_quarter]
    return min(latest, key=lambda item: (item[1] is not None, item[2].casefold()))[2]


def _select_one(candidates: list[SourceArtifactCandidate]) -> SourceArtifactCandidate:
    return min(
        candidates,
        key=lambda candidate: (
            candidate.fiscal_quarter is not None,
            candidate.download_url.casefold(),
        ),
    )


def _select_canonical(
    source_id: str, candidates: list[SourceArtifactCandidate]
) -> tuple[tuple[str, ...], dict[str, dict[str, object]]]:
    selected: list[str] = []
    selected_updates: dict[str, dict[str, object]] = {}
    latest_fiscal_year = max(candidate.fiscal_year for candidate in candidates)
    by_year_variant: dict[tuple[int, str], list[SourceArtifactCandidate]] = {}
    for candidate in candidates:
        key = (candidate.fiscal_year, candidate.variant)
        by_year_variant.setdefault(key, []).append(candidate)
    for (fiscal_year, variant), yearly_candidates in sorted(by_year_variant.items()):
        complete_year = fiscal_year < latest_fiscal_year or any(
            candidate.fiscal_quarter in {None, 4} for candidate in yearly_candidates
        )
        if source_id == "dol_lca":
            annual_candidates = [
                candidate for candidate in yearly_candidates if candidate.fiscal_quarter is None
            ]
            if complete_year and annual_candidates:
                canonical = _select_one(annual_candidates)
                selected.append(canonical.candidate_id)
                selected_updates[canonical.candidate_id] = {
                    "is_partial_period": False,
                    "is_quarter_partition": False,
                    "coverage_start_quarter": 1,
                }
                continue
            if complete_year:
                segments = REVIEWED_LCA_COMPLETED_SEGMENTS.get(fiscal_year)
                if segments is None:
                    raise SourceDiscoveryError(
                        f"Completed FY{fiscal_year} {variant} LCA artifacts lack a reviewed "
                        "coverage-segment contract"
                    )
                covered_quarters = [
                    quarter
                    for start_quarter, end_quarter in segments
                    for quarter in range(start_quarter, end_quarter + 1)
                ]
                if sorted(covered_quarters) != [1, 2, 3, 4] or len(covered_quarters) != 4:
                    raise SourceDiscoveryError(
                        f"Reviewed FY{fiscal_year} LCA coverage segments do not cover Q1-Q4 "
                        "exactly once"
                    )
                for start_quarter, end_quarter in segments:
                    matching = [
                        candidate
                        for candidate in yearly_candidates
                        if candidate.fiscal_quarter == end_quarter
                    ]
                    if not matching:
                        raise SourceDiscoveryError(
                            f"Completed FY{fiscal_year} {variant} LCA coverage is missing "
                            f"reviewed segment Q{start_quarter}-Q{end_quarter}"
                        )
                    canonical = _select_one(matching)
                    selected.append(canonical.candidate_id)
                    selected_updates[canonical.candidate_id] = {
                        "is_partial_period": False,
                        "is_quarter_partition": True,
                        "coverage_start_quarter": start_quarter,
                    }
                continue
            latest_quarter = max(candidate.fiscal_quarter or 4 for candidate in yearly_candidates)
            latest = [
                candidate
                for candidate in yearly_candidates
                if (candidate.fiscal_quarter or 4) == latest_quarter
            ]
            canonical = _select_one(latest)
            selected.append(canonical.candidate_id)
            selected_updates[canonical.candidate_id] = {
                "is_partial_period": True,
                "is_quarter_partition": False,
                "coverage_start_quarter": 1,
            }
            continue
        if complete_year:
            final_candidates = [
                candidate
                for candidate in yearly_candidates
                if candidate.fiscal_quarter in {None, 4}
            ]
            if not final_candidates:
                raise SourceDiscoveryError(
                    f"Completed FY{fiscal_year} {variant} artifacts lack an annual/Q4 snapshot"
                )
            annual_candidates = [
                candidate for candidate in final_candidates if candidate.fiscal_quarter is None
            ]
            if annual_candidates:
                selected.append(_select_one(annual_candidates).candidate_id)
                continue
            yearly_candidates = final_candidates
        latest_quarter = max(candidate.fiscal_quarter or 4 for candidate in yearly_candidates)
        latest = [
            candidate
            for candidate in yearly_candidates
            if (candidate.fiscal_quarter or 4) == latest_quarter
        ]
        selected.append(_select_one(latest).candidate_id)
    return tuple(sorted(selected)), selected_updates


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

    if not deduplicated:
        raise SourceDiscoveryError(
            f"No {config.id} disclosure artifacts found from FY{from_fiscal_year} onward"
        )
    candidates = list(deduplicated.values())
    candidates.sort(
        key=lambda item: (
            item.fiscal_year,
            item.fiscal_quarter or 4,
            item.variant,
            item.download_url,
        )
    )
    selected_ids, selected_updates = _select_canonical(config.id, candidates)
    candidates = [
        candidate.model_copy(update=selected_updates[candidate.candidate_id])
        if candidate.candidate_id in selected_updates
        else candidate
        for candidate in candidates
    ]
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
