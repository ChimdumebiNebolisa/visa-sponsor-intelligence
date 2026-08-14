"""Discovery for current federal USCIS, IPEDS, and HERD artifacts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from sponsor_intel.sources.errors import DownloadError, SourceDiscoveryError, UnsafeSourceUrlError
from sponsor_intel.sources.http_client import OfficialHttpClient, validate_official_url
from sponsor_intel.sources.models import DiscoveryReport, SourceArtifactCandidate, SourceConfig

_USCIS_PERIOD = re.compile(r"fiscal year\s+(20\d{2})(?:\s*\(quarter\s*([1-4])\))?", re.IGNORECASE)
_IPEDS_DIRECTORY = re.compile(r"^HD(20\d{2})\.zip$", re.IGNORECASE)
_IPEDS_DICTIONARY = re.compile(r"^HD(20\d{2})_Dict\.zip$", re.IGNORECASE)
_HERD_ARCHIVE = re.compile(r"^higher_education_r_and_d_(20\d{2})(_short)?\.zip$", re.IGNORECASE)


def _official_links(config: SourceConfig, client: OfficialHttpClient) -> list[tuple[str, str]]:
    document = HTMLParser(client.get_text(config.landing_page))
    links: list[tuple[str, str]] = []
    for anchor in document.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        url = urljoin(config.landing_page, href)
        try:
            validate_official_url(url, config.official_domains)
        except UnsafeSourceUrlError:
            continue
        text = " ".join(anchor.text(separator=" ", strip=True).split())
        links.append((url, text))
    return links


def discover_uscis_h1b(
    config: SourceConfig,
    client: OfficialHttpClient,
    *,
    from_fiscal_year: int,
) -> DiscoveryReport:
    """Confirm the current hub period and select its official full-data CSV sheet."""

    warnings: list[str] = []
    try:
        html = client.get_text(config.landing_page)
    except DownloadError as error:
        if (
            config.published_through_fiscal_year is None
            or config.published_through_quarter is None
            or config.record_layout_url is None
        ):
            raise SourceDiscoveryError(
                "USCIS blocked landing-page discovery and no reviewed period fallback exists"
            ) from error
        html = ""
        latest_year = config.published_through_fiscal_year
        latest_quarter = config.published_through_quarter
        warnings.append(
            "USCIS landing page blocked automated access; using the reviewed registry period "
            f"FY{latest_year} Q{latest_quarter} and official Tableau artifact URL"
        )
    else:
        periods = [
            (int(year), int(quarter) if quarter else 4)
            for year, quarter in _USCIS_PERIOD.findall(html)
        ]
        if not periods:
            raise SourceDiscoveryError("USCIS H-1B hub did not disclose a current fiscal period")
        latest_year, latest_quarter = max(periods)
    if latest_year < max(from_fiscal_year, config.minimum_fiscal_year):
        raise SourceDiscoveryError("USCIS H-1B hub has no data in the requested period")
    if config.artifact_url is None:
        raise SourceDiscoveryError("USCIS H-1B source requires an official artifact_url")
    artifact_url = validate_official_url(config.artifact_url, config.official_domains)

    record_layout_url: str | None = config.record_layout_url
    if record_layout_url is not None:
        record_layout_url = validate_official_url(record_layout_url, config.official_domains)
    if html:
        document = HTMLParser(html)
        for anchor in document.css("a[href]"):
            text = " ".join(anchor.text(separator=" ", strip=True).split()).casefold()
            if "understanding" not in text or "h-1b" not in text:
                continue
            url = urljoin(config.landing_page, anchor.attributes["href"])
            try:
                record_layout_url = validate_official_url(url, config.official_domains)
            except UnsafeSourceUrlError:
                continue
            break
    if record_layout_url is None:
        raise SourceDiscoveryError("USCIS H-1B hub lacks its official data guide link")

    start_year = max(from_fiscal_year, config.minimum_fiscal_year)
    candidates = tuple(
        SourceArtifactCandidate(
            source_id=config.id,
            authority=config.authority,
            landing_page_url=config.landing_page,
            download_url=(
                f"{artifact_url}{'&' if '?' in artifact_url else '?'}Fiscal%20Year%20%20%20={year}"
            ),
            fiscal_year=year,
            fiscal_quarter=latest_quarter if year == latest_year else None,
            is_partial_period=year == latest_year and latest_quarter < 4,
            file_name=f"H1BPublic_FY{year}.csv",
            expected_format="csv",
            variant="tableau_fiscal_year",
            record_layout_url=record_layout_url,
        )
        for year in range(start_year, latest_year + 1)
    )
    return DiscoveryReport(
        source_id=config.id,
        discovered_at=datetime.now(UTC),
        from_fiscal_year=from_fiscal_year,
        landing_page_url=config.landing_page,
        candidates=candidates,
        selected_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
        warnings=tuple(warnings),
    )


def discover_ipeds(
    config: SourceConfig,
    client: OfficialHttpClient,
    *,
    from_fiscal_year: int,
) -> DiscoveryReport:
    """Select the latest official IPEDS institutional directory and dictionary."""

    data_links: dict[int, tuple[str, str]] = {}
    dictionary_links: dict[int, str] = {}
    for url, _text in _official_links(config, client):
        file_name = PurePosixPath(urlparse(url).path).name
        data_match = _IPEDS_DIRECTORY.fullmatch(file_name)
        dictionary_match = _IPEDS_DICTIONARY.fullmatch(file_name)
        if data_match:
            data_links[int(data_match.group(1))] = (url, file_name)
        elif dictionary_match:
            dictionary_links[int(dictionary_match.group(1))] = url
    eligible_years = [
        year
        for year in data_links
        if year >= max(from_fiscal_year, config.minimum_fiscal_year) and year in dictionary_links
    ]
    if not eligible_years:
        raise SourceDiscoveryError("No IPEDS directory with a matching dictionary was found")
    year = max(eligible_years)
    download_url, file_name = data_links[year]
    candidate = SourceArtifactCandidate(
        source_id=config.id,
        authority=config.authority,
        landing_page_url=config.landing_page,
        download_url=download_url,
        fiscal_year=year,
        fiscal_quarter=None,
        is_partial_period=False,
        file_name=file_name,
        expected_format="zip",
        variant="directory",
        record_layout_url=dictionary_links[year],
    )
    candidates = tuple(
        SourceArtifactCandidate(
            source_id=config.id,
            authority=config.authority,
            landing_page_url=config.landing_page,
            download_url=data_links[candidate_year][0],
            fiscal_year=candidate_year,
            fiscal_quarter=None,
            is_partial_period=False,
            file_name=data_links[candidate_year][1],
            expected_format="zip",
            variant="directory",
            record_layout_url=dictionary_links.get(candidate_year),
        )
        for candidate_year in sorted(data_links)
        if candidate_year >= max(from_fiscal_year, config.minimum_fiscal_year)
    )
    return DiscoveryReport(
        source_id=config.id,
        discovered_at=datetime.now(UTC),
        from_fiscal_year=from_fiscal_year,
        landing_page_url=config.landing_page,
        candidates=candidates,
        selected_candidate_ids=(candidate.candidate_id,),
    )


def discover_herd(
    config: SourceConfig,
    client: OfficialHttpClient,
    *,
    from_fiscal_year: int,
) -> DiscoveryReport:
    """Select standard and short-form HERD microdata for every eligible survey year."""

    candidates_by_url: dict[str, SourceArtifactCandidate] = {}
    for url, _text in _official_links(config, client):
        file_name = PurePosixPath(urlparse(url).path).name
        match = _HERD_ARCHIVE.fullmatch(file_name)
        if match is None:
            continue
        year = int(match.group(1))
        if year < max(from_fiscal_year, config.minimum_fiscal_year):
            continue
        variant = "short" if match.group(2) else "standard"
        candidates_by_url[url] = SourceArtifactCandidate(
            source_id=config.id,
            authority=config.authority,
            landing_page_url=config.landing_page,
            download_url=url,
            fiscal_year=year,
            fiscal_quarter=None,
            is_partial_period=False,
            file_name=file_name,
            expected_format="zip",
            variant=variant,
            record_layout_url=config.landing_page,
        )
    candidates = tuple(
        sorted(
            candidates_by_url.values(),
            key=lambda item: (item.fiscal_year, item.variant, item.download_url),
        )
    )
    if not candidates:
        raise SourceDiscoveryError("No eligible HERD microdata archives were found")
    by_year: dict[int, set[str]] = {}
    for candidate in candidates:
        by_year.setdefault(candidate.fiscal_year, set()).add(candidate.variant)
    incomplete = {
        year: variants for year, variants in by_year.items() if variants != {"standard", "short"}
    }
    if incomplete:
        raise SourceDiscoveryError(f"HERD years lack standard/short archive pairs: {incomplete}")
    return DiscoveryReport(
        source_id=config.id,
        discovered_at=datetime.now(UTC),
        from_fiscal_year=from_fiscal_year,
        landing_page_url=config.landing_page,
        candidates=candidates,
        selected_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
    )
