"""ICE SEVP top-employer OPT/STEM OPT report adapter."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import pdfplumber
import polars as pl
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from selectolax.parser import HTMLParser

from sponsor_intel.entity_resolution.normalization import stable_id
from sponsor_intel.sources.errors import DownloadError, SourceDiscoveryError, UnsafeSourceUrlError
from sponsor_intel.sources.http_client import validate_official_url
from sponsor_intel.sources.manifests import write_json_atomic
from sponsor_intel.sources.models import (
    DiscoveryReport,
    DownloadedArtifact,
    IssueSeverity,
    NormalizedDataset,
    SourceArtifactCandidate,
    SourceConfig,
    SourceContext,
    ValidationIssue,
    ValidationResult,
)
from sponsor_intel.sources.tabular import TabularSourceAdapter, validation_status

_REPORT_NAME = re.compile(r"^(20\d{2})_Top200_Employers_OPT_STEM_OPT_Students\.pdf$", re.IGNORECASE)
_PROGRAMS = (
    ("OPT_OR_STEM_OPT", 1),
    ("OPT", 2),
    ("STEM_OPT", 3),
)
COVERAGE_NOTE = (
    "Positive observations from ICE SEVP's Top 200 Employers report only. "
    "Employers outside the report remain UNKNOWN. A student may be counted more than once "
    "when participating with the same employer in different practical-training programs."
)


def _parse_count(value: str | None) -> int | None:
    cleaned = (value or "").replace(",", "").strip()
    if not cleaned:
        return None
    if not cleaned.isdigit():
        raise ValueError(f"Unexpected OPT report count: {value!r}")
    return int(cleaned)


def parse_opt_table_rows(
    rows: list[list[str | None]],
    *,
    report_year: int,
    source_artifact_id: str,
    source_url: str,
    landing_page_url: str,
    retrieved_at: datetime,
    source_sha256: str,
) -> pl.DataFrame:
    """Convert extracted PDF table rows into positive-only long observations."""

    employer_rows = [
        row
        for row in rows
        if len(row) == 4
        and (row[0] or "").strip()
        and "Top 200 Employer Names" not in (row[0] or "")
    ]
    records: list[dict[str, object]] = []
    for rank, row in enumerate(employer_rows, start=1):
        employer_name = (row[0] or "").strip()
        for program_type, column in _PROGRAMS:
            reported_count = _parse_count(row[column])
            if reported_count is None:
                continue
            records.append(
                {
                    "observation_id": stable_id("opt", source_artifact_id, str(rank), program_type),
                    "source_artifact_id": source_artifact_id,
                    "source_id": "sevp_opt",
                    "report_year": report_year,
                    "rank": rank,
                    "employer_name_raw": employer_name,
                    "program_type": program_type,
                    "reported_count": reported_count,
                    "is_positive": True,
                    "source_url": source_url,
                    "landing_page_url": landing_page_url,
                    "retrieved_at": retrieved_at.isoformat(),
                    "source_sha256": source_sha256,
                    "coverage_note": COVERAGE_NOTE,
                    "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
                }
            )
    return pl.DataFrame(records)


class SevpOptAdapter(TabularSourceAdapter):
    """Discover and parse ICE's official positive-only employer report."""

    def __init__(
        self,
        config: SourceConfig,
        client,
        data_root: Path,
        output_root: Path,
    ) -> None:
        if config.id != "sevp_opt":
            raise ValueError(f"SevpOptAdapter requires sevp_opt, received {config.id}")
        super().__init__(config, client, data_root, output_root)
        self.raw_root = data_root / "raw"

    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact:
        """Download ICE's browser-served PDF into the immutable raw layer."""

        if candidate.source_id != self.config.id or candidate.expected_format != "pdf":
            raise DownloadError(f"Unexpected ICE OPT artifact: {candidate.file_name}")
        validate_official_url(candidate.download_url, self.config.official_domains)
        target_directory = self.raw_root / self.config.id / f"fy={candidate.fiscal_year}"
        target_directory.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.candidate_id}-", suffix=".part", dir=target_directory
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(accept_downloads=True)
                    page = context.new_page()
                    with page.expect_download(timeout=120_000) as download_info:
                        try:
                            page.goto(
                                candidate.download_url,
                                wait_until="commit",
                                timeout=120_000,
                            )
                        except PlaywrightError as error:
                            if "Download is starting" not in str(error):
                                raise
                    download = download_info.value
                    failure = download.failure()
                    if failure:
                        raise DownloadError(f"ICE PDF browser download failed: {failure}")
                    download.save_as(temporary_path)
                finally:
                    browser.close()
            byte_size = temporary_path.stat().st_size
            if byte_size <= 0 or byte_size > self.config.max_download_bytes:
                raise DownloadError(f"ICE PDF size {byte_size} is outside configured bounds")
            with temporary_path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise DownloadError("ICE browser download did not return a PDF")
                source.seek(0)
                checksum = hashlib.file_digest(source, "sha256").hexdigest()
            final_path = target_directory / f"{Path(candidate.file_name).stem}-{checksum[:16]}.pdf"
            cache_hit = final_path.exists()
            if cache_hit:
                if final_path.stat().st_size != byte_size:
                    raise DownloadError(
                        f"Content-addressed ICE raw path has unexpected size: {final_path}"
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
                mime_type="application/pdf",
                cache_hit=cache_hit,
            )
        except Exception:
            if temporary_path.exists():
                temporary_path.unlink()
            raise

    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]:
        candidates: list[SourceArtifactCandidate] = []
        warnings: list[str] = []
        try:
            document = HTMLParser(self.client.get_text(self.config.landing_page))
        except DownloadError as error:
            if (
                self.config.artifact_url is None
                or self.config.published_through_fiscal_year is None
            ):
                raise SourceDiscoveryError(
                    "ICE blocked landing-page discovery and no reviewed report fallback exists"
                ) from error
            url = validate_official_url(self.config.artifact_url, self.config.official_domains)
            file_name = PurePosixPath(urlparse(url).path).name
            match = _REPORT_NAME.fullmatch(file_name)
            year = self.config.published_through_fiscal_year
            if match is None or int(match.group(1)) != year:
                raise SourceDiscoveryError(
                    "Reviewed ICE OPT report URL does not agree with its publication year"
                ) from error
            if year >= max(context.from_fiscal_year, self.config.minimum_fiscal_year):
                candidates.append(self._candidate(url, file_name, year))
            warnings.append(
                "ICE landing page blocked automated discovery; used the reviewed official "
                f"FY{year} report URL from the source registry"
            )
        else:
            for anchor in document.css("a[href]"):
                href = anchor.attributes.get("href")
                if not href:
                    continue
                url = urljoin(self.config.landing_page, href)
                try:
                    validate_official_url(url, self.config.official_domains)
                except UnsafeSourceUrlError:
                    continue
                file_name = PurePosixPath(urlparse(url).path).name
                match = _REPORT_NAME.fullmatch(file_name)
                if match is None:
                    continue
                year = int(match.group(1))
                if year < max(context.from_fiscal_year, self.config.minimum_fiscal_year):
                    continue
                candidates.append(self._candidate(url, file_name, year))
        candidates.sort(key=lambda item: (item.fiscal_year, item.download_url))
        if not candidates:
            raise SourceDiscoveryError("No eligible ICE SEVP OPT employer report was found")
        selected = candidates[-1]
        report = DiscoveryReport(
            source_id=self.config.id,
            discovered_at=datetime.now(UTC),
            from_fiscal_year=context.from_fiscal_year,
            landing_page_url=self.config.landing_page,
            candidates=tuple(candidates),
            selected_candidate_ids=(selected.candidate_id,),
            warnings=tuple(warnings),
        )
        self.last_discovery_report = report
        return [selected]

    def _candidate(self, url: str, file_name: str, year: int) -> SourceArtifactCandidate:
        return SourceArtifactCandidate(
            source_id=self.config.id,
            authority=self.config.authority,
            landing_page_url=self.config.landing_page,
            download_url=url,
            fiscal_year=year,
            fiscal_quarter=None,
            is_partial_period=False,
            file_name=file_name,
            expected_format="pdf",
            variant="top_200_employers",
            record_layout_url=self.config.landing_page,
        )

    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset:
        rows: list[list[str | None]] = []
        with pdfplumber.open(artifact.raw_path) as document:
            for page in document.pages:
                tables = page.extract_tables()
                if len(tables) != 1:
                    raise ValueError(
                        f"Expected one table on each OPT report page, found {len(tables)}"
                    )
                rows.extend(tables[0])
        frame = parse_opt_table_rows(
            rows,
            report_year=artifact.candidate.fiscal_year,
            source_artifact_id=artifact.source_artifact_id,
            source_url=artifact.candidate.download_url,
            landing_page_url=artifact.candidate.landing_page_url,
            retrieved_at=artifact.retrieved_at,
            source_sha256=artifact.sha256,
        )
        total_rows = frame.filter(pl.col("program_type") == "OPT_OR_STEM_OPT")
        issues: list[ValidationIssue] = []
        if total_rows.height != self.config.minimum_row_count:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="opt_employer_count",
                    message="OPT report must contain exactly the configured employer count",
                    details={
                        "actual": total_rows.height,
                        "expected": self.config.minimum_row_count,
                    },
                )
            )
        if frame.is_empty() or frame.filter(pl.col("reported_count") <= 0).height:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="non_positive_opt_observation",
                    message="OPT report observations must be strictly positive",
                )
            )
        if total_rows["rank"].n_unique() != total_rows.height:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    category="duplicate_opt_rank",
                    message="OPT report employer ranks must be unique",
                )
            )
        status = validation_status(issues)
        schema_path = (
            self.report_root / "schema" / self.config.id / f"{artifact.source_artifact_id}.json"
        )
        schema_fingerprint = hashlib.sha256(
            ("\n".join(frame.columns) + "\n").encode("utf-8")
        ).hexdigest()
        write_json_atomic(
            schema_path,
            {
                "source_artifact_id": artifact.source_artifact_id,
                "source_id": self.config.id,
                "fiscal_year": artifact.candidate.fiscal_year,
                "variant": artifact.candidate.variant,
                "schema_version": self.config.schema_version,
                "parser_version": self.config.parser_version,
                "schema_fingerprint": schema_fingerprint,
                "original_columns": [
                    "Top 200 Employer Names",
                    "OPT or STEM-OPT total",
                    "OPT total",
                    "STEM-OPT total",
                ],
                "normalized_columns": frame.columns,
                "validation_status": status.value,
                "validation_issues": [issue.model_dump(mode="json") for issue in issues],
            },
        )
        return NormalizedDataset(
            artifact=artifact,
            frame=frame,
            original_columns=(
                "Top 200 Employer Names",
                "OPT or STEM-OPT total",
                "OPT total",
                "STEM-OPT total",
            ),
            normalized_columns=tuple(frame.columns),
            column_mapping={
                "employer_name_raw": "Top 200 Employer Names",
                "reported_count": "program-specific count",
            },
            validation=ValidationResult(status=status, issues=tuple(issues)),
            schema_diff_path=schema_path,
        )
