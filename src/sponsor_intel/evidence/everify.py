"""Prioritized, cached, rate-limited E-Verify employer lookups."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import polars as pl
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from sponsor_intel.entity_resolution.models import EntityResolutionConfig
from sponsor_intel.entity_resolution.normalization import normalize_name, stable_id
from sponsor_intel.evidence.io import write_parquet_atomic
from sponsor_intel.evidence.models import EVerifyBuildSummary
from sponsor_intel.sources.manifests import write_json_atomic

EVERIFY_SOURCE_URL = "https://www.e-verify.gov/e-verify-employer-search"
MIN_LOOKUP_INTERVAL_SECONDS = 5.0
CACHE_TTL_DAYS = 90
_STATUSES = {
    "CONFIRMED_ACTIVE",
    "CONFIRMED_INACTIVE",
    "NO_MATCH",
    "AMBIGUOUS",
    "NOT_CHECKED",
    "ERROR",
}

EVERIFY_OBSERVATION_SCHEMA = {
    "lookup_id": pl.String,
    "priority_rank": pl.Int64,
    "queried_name": pl.String,
    "legal_entity_id": pl.String,
    "parent_organization_id": pl.String,
    "organization_id": pl.String,
    "state": pl.String,
    "enrollment_status": pl.String,
    "enrollment_date": pl.String,
    "termination_date": pl.String,
    "workforce_size": pl.String,
    "hiring_site_count": pl.Int64,
    "hiring_site_locations": pl.String,
    "matched_name": pl.String,
    "matched_dba": pl.String,
    "retrieved_at": pl.String,
    "match_confidence": pl.Float64,
    "match_method": pl.String,
    "review_status": pl.String,
    "review_reason": pl.String,
    "source_url": pl.String,
    "source_evidence_json": pl.String,
    "cache_hit": pl.Boolean,
    "evidence_class": pl.String,
}


@dataclass(frozen=True, slots=True)
class EVerifySearchResponse:
    rows: tuple[dict[str, str], ...]
    retrieved_at: datetime


class EVerifySearchProvider(Protocol):
    def search(self, queried_name: str) -> EVerifySearchResponse: ...


def _safe_query(name: str, config: EntityResolutionConfig) -> bool:
    normalized = normalize_name(name, config)
    return len(normalized) >= 8 and len(normalized.split()) >= 2


def build_everify_priorities(
    *,
    data_root: Path = Path("data"),
    top_institution_limit: int = 200,
    manual_organization_ids: Sequence[str] = (),
) -> pl.DataFrame:
    """Build the full lookup universe before any official-site query is made."""

    config = EntityResolutionConfig.from_yaml()
    employers = pl.read_parquet(data_root / "processed" / "employer_metrics.parquet")
    institutions = pl.read_parquet(data_root / "processed" / "institution_metrics.parquet")
    legal = pl.read_parquet(data_root / "resolved" / "legal_entities.parquet")
    aliases = pl.read_parquet(data_root / "resolved" / "entity_aliases.parquet")
    alias_counts = (
        aliases.filter(pl.col("legal_entity_id").is_not_null())
        .group_by("legal_entity_id")
        .agg(pl.col("occurrence_count").sum().alias("alias_occurrence_count"))
    )
    legal_candidates = (
        legal.with_columns(
            pl.coalesce("parent_organization_id", "legal_entity_id").alias("organization_id")
        )
        .join(alias_counts, on="legal_entity_id", how="left")
        .with_columns(pl.col("alias_occurrence_count").fill_null(0))
        .sort(
            ["organization_id", "alias_occurrence_count", "legal_name"],
            descending=[False, True, False],
        )
        .group_by("organization_id", maintain_order=True)
        .first()
    )
    legal_by_org = {row["organization_id"]: row for row in legal_candidates.iter_rows(named=True)}
    employer_by_org = {row["organization_id"]: row for row in employers.iter_rows(named=True)}

    selected: dict[str, tuple[int, str, float]] = {}
    for row in employers.iter_rows(named=True):
        activity_score = float(
            (row["relevant_lca_count"] or 0)
            + (row["relevant_certified_perm_count"] or 0)
            + (row["initial_approvals"] or 0)
        )
        if activity_score > 0:
            selected[row["organization_id"]] = (1, "SPONSORSHIP_ACTIVITY", activity_score)

    institution_rows = (
        institutions.filter(pl.col("organization_id").is_not_null())
        .sort(
            ["total_rd", "relevant_lca_count", "official_name"],
            descending=[True, True, False],
        )
        .unique("organization_id", keep="first", maintain_order=True)
        .head(top_institution_limit)
    )
    for row in institution_rows.iter_rows(named=True):
        organization_id = row["organization_id"]
        selected.setdefault(
            organization_id,
            (2, "TOP_RESEARCH_INSTITUTION", float(row["total_rd"] or 0)),
        )
    for organization_id in manual_organization_ids:
        selected.setdefault(organization_id, (3, "MANUAL_TARGET", 0.0))

    rows: list[dict[str, object]] = []
    for organization_id, (tier, reason, score) in selected.items():
        legal_row = legal_by_org.get(organization_id)
        employer_row = employer_by_org.get(organization_id)
        if legal_row is None or employer_row is None:
            continue
        queried_name = str(legal_row["legal_name"])
        rows.append(
            {
                "lookup_id": stable_id(
                    "everify", organization_id, legal_row["legal_entity_id"], queried_name
                ),
                "organization_id": organization_id,
                "parent_organization_id": legal_row["parent_organization_id"],
                "legal_entity_id": legal_row["legal_entity_id"],
                "organization_name": employer_row["organization_name"],
                "queried_name": queried_name,
                "state": legal_row["state"] or employer_row["state"],
                "priority_tier": tier,
                "priority_reason": reason,
                "priority_score": score,
                "query_is_safe": _safe_query(queried_name, config),
                "lookup_status": "NOT_CHECKED",
            }
        )
    rows.sort(
        key=lambda row: (
            int(cast(int, row["priority_tier"])),
            -float(cast(float, row["priority_score"])),
            str(row["organization_name"]),
            str(row["lookup_id"]),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["priority_rank"] = rank
    columns = [
        "priority_rank",
        "lookup_id",
        "organization_id",
        "parent_organization_id",
        "legal_entity_id",
        "organization_name",
        "queried_name",
        "state",
        "priority_tier",
        "priority_reason",
        "priority_score",
        "query_is_safe",
        "lookup_status",
    ]
    return pl.DataFrame(rows).select(columns)


def _site_count(value: str | None) -> int | None:
    cleaned = (value or "").replace(",", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _evaluate_matches(
    priority: dict[str, Any],
    response: EVerifySearchResponse,
    config: EntityResolutionConfig,
) -> dict[str, object]:
    queried_name = str(priority["queried_name"])
    queried_normalized = normalize_name(queried_name, config)
    rows = list(response.rows)
    employer_exact = [
        row for row in rows if normalize_name(row.get("Employer"), config) == queried_normalized
    ]
    dba_exact = [
        row
        for row in rows
        if normalize_name(row.get("Doing Business As"), config) == queried_normalized
    ]
    exact = employer_exact or dba_exact
    match_method = "EXACT_EMPLOYER_NAME" if employer_exact else "EXACT_DBA_NAME"
    state = str(priority.get("state") or "").upper()
    if exact and state:
        state_matches = [
            row
            for row in exact
            if state
            in {
                item.strip().upper() for item in (row.get("Hiring Site Locations") or "").split(",")
            }
        ]
        if state_matches:
            exact = state_matches
        else:
            return _observation(
                priority,
                response,
                status="AMBIGUOUS",
                confidence=0.0,
                match_method=match_method,
                review_status="NEEDS_REVIEW",
                review_reason="Exact name results did not agree with the legal-entity state",
            )
    if not exact:
        if rows:
            return _observation(
                priority,
                response,
                status="AMBIGUOUS",
                confidence=0.0,
                match_method="SUBSTRING_RESULTS_ONLY",
                review_status="NEEDS_REVIEW",
                review_reason="Official search returned results but no full-name exact match",
            )
        return _observation(
            priority,
            response,
            status="NO_MATCH",
            confidence=0.0,
            match_method="NO_RESULTS",
            review_status="NOT_REQUIRED",
            review_reason=(
                "No official search result; this is not evidence that the employer is not enrolled"
            ),
        )

    active = [row for row in exact if (row.get("Account Status") or "").casefold() == "open"]
    terminated = [
        row for row in exact if (row.get("Account Status") or "").casefold() == "terminated"
    ]
    if active:
        chosen = active[0]
        status = "CONFIRMED_ACTIVE"
    elif terminated:
        chosen = terminated[0]
        status = "CONFIRMED_INACTIVE"
    else:
        return _observation(
            priority,
            response,
            status="AMBIGUOUS",
            confidence=0.0,
            match_method=match_method,
            review_status="NEEDS_REVIEW",
            review_reason="Exact result had an unrecognized account status",
        )
    confidence = 1.0 if state and match_method == "EXACT_EMPLOYER_NAME" else 0.98
    return _observation(
        priority,
        response,
        status=status,
        confidence=confidence,
        match_method=match_method,
        review_status="NOT_REQUIRED",
        review_reason=None,
        chosen=chosen,
    )


def _observation(
    priority: dict[str, Any],
    response: EVerifySearchResponse,
    *,
    status: str,
    confidence: float,
    match_method: str,
    review_status: str,
    review_reason: str | None,
    chosen: dict[str, str] | None = None,
) -> dict[str, object]:
    if status not in _STATUSES:
        raise ValueError(f"Unsupported E-Verify status: {status}")
    selected = chosen or {}
    return {
        "lookup_id": priority["lookup_id"],
        "priority_rank": priority["priority_rank"],
        "queried_name": priority["queried_name"],
        "legal_entity_id": priority["legal_entity_id"],
        "parent_organization_id": priority["parent_organization_id"],
        "organization_id": priority["organization_id"],
        "state": priority["state"],
        "enrollment_status": status,
        "enrollment_date": selected.get("Date Enrolled") or None,
        "termination_date": selected.get("Date Terminated") or None,
        "workforce_size": selected.get("Workforce Size") or None,
        "hiring_site_count": _site_count(selected.get("SUM(Number of Hiring Sites)")),
        "hiring_site_locations": selected.get("Hiring Site Locations") or None,
        "matched_name": selected.get("Employer") or None,
        "matched_dba": selected.get("Doing Business As") or None,
        "retrieved_at": response.retrieved_at.astimezone(UTC).isoformat(),
        "match_confidence": confidence,
        "match_method": match_method,
        "review_status": review_status,
        "review_reason": review_reason,
        "source_url": EVERIFY_SOURCE_URL,
        "source_evidence_json": json.dumps(response.rows, sort_keys=True),
        "cache_hit": False,
        "evidence_class": "OBSERVED_GOVERNMENT_RECORD",
    }


class PlaywrightEVerifyClient:
    """Use the official public Tableau interface without bypassing access controls."""

    def __init__(self, *, lookup_interval_seconds: float = MIN_LOOKUP_INTERVAL_SECONDS) -> None:
        if lookup_interval_seconds < MIN_LOOKUP_INTERVAL_SECONDS:
            raise ValueError(
                f"E-Verify lookups must be at least {MIN_LOOKUP_INTERVAL_SECONDS:g} seconds apart"
            )
        self.lookup_interval_seconds = lookup_interval_seconds
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_commit: float | None = None

    def __enter__(self) -> PlaywrightEVerifyClient:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        try:
            self._prepare_dashboard()
        except Exception:
            self.__exit__()
            raise
        return self

    def _prepare_dashboard(self) -> None:
        if self._page is None:
            raise RuntimeError("E-Verify browser client is not open")
        self._page.goto(EVERIFY_SOURCE_URL, wait_until="domcontentloaded", timeout=120_000)
        self._page.wait_for_function(
            """() => {
                try {
                    const viz = document.querySelector('tableau-viz');
                    return Boolean(viz && viz.workbook && viz.workbook.activeSheet);
                } catch (_) {
                    return false;
                }
            }""",
            timeout=120_000,
        )
        frame = self._tableau_frame()
        search = frame.get_by_role(
            "textbox", name=re.compile(r"Filter Business Name", re.IGNORECASE)
        )
        search.wait_for(state="visible", timeout=60_000)
        date_button = frame.get_by_role("button", name=re.compile(r"Date Enrolled", re.IGNORECASE))
        date_button.click()
        dialog = frame.get_by_role("dialog", name=re.compile(r"Date Enrolled", re.IGNORECASE))
        dialog.get_by_role("radio", name=re.compile(r"Last \d+ years", re.IGNORECASE)).click()
        years_box = dialog.get_by_role("textbox").first
        years_box.fill("30")
        years_box.press("Enter")
        start_year = datetime.now(UTC).year - 29
        dialog.get_by_text(re.compile(rf"1/1/{start_year}\s+to\s+12/31/")).wait_for(
            state="visible", timeout=30_000
        )
        self._page.keyboard.press("Escape")
        frame.get_by_role(
            "button", name=re.compile(r"Date Enrolled.*Last 30 years", re.IGNORECASE)
        ).wait_for(state="visible", timeout=30_000)

    def __exit__(self, *_: object) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _tableau_frame(self):
        if self._page is None:
            raise RuntimeError("E-Verify browser client is not open")
        frame = next((item for item in self._page.frames if item.name == "tableau-viz"), None)
        if frame is None:
            raise RuntimeError("E-Verify Tableau frame is unavailable")
        return frame

    def search(self, queried_name: str) -> EVerifySearchResponse:
        if self._page is None:
            raise RuntimeError("E-Verify browser client is not open")
        if self._last_commit is not None:
            remaining = self.lookup_interval_seconds - (time.monotonic() - self._last_commit)
            if remaining > 0:
                time.sleep(remaining)
        frame = self._tableau_frame()
        search = frame.get_by_role(
            "textbox", name=re.compile(r"Filter Business Name", re.IGNORECASE)
        )
        search.fill(queried_name)
        search.press("Enter")
        self._last_commit = time.monotonic()
        deadline = time.monotonic() + 30
        payload: dict[str, Any] | None = None
        empty_since: float | None = None
        self._page.wait_for_timeout(1_000)
        while time.monotonic() < deadline:
            payload = self._worksheet_payload()
            if self._results_match_query(payload, queried_name) and payload.get("rows"):
                break
            if self._is_empty_result(payload):
                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= 2:
                    break
            else:
                empty_since = None
            self._page.wait_for_timeout(500)
        else:
            raise RuntimeError("E-Verify results did not synchronize with the committed query")
        if payload is None:
            raise RuntimeError("E-Verify did not return a worksheet payload")
        columns = payload["columns"]
        raw_rows = payload["rows"]
        rows = tuple(
            {str(column): str(value) for column, value in zip(columns, row, strict=True)}
            for row in raw_rows
        )
        return EVerifySearchResponse(rows=rows, retrieved_at=datetime.now(UTC))

    def _worksheet_payload(self) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("E-Verify browser client is not open")
        return self._page.evaluate(
            """async () => {
                const viz = document.querySelector('tableau-viz');
                const worksheet = viz.workbook.activeSheet.worksheets
                    .find((item) => item.name === 'Employer List');
                const data = await worksheet.getSummaryDataAsync({maxRows: 10000});
                return {
                    columns: data.columns.map((item) => item.fieldName),
                    rows: data.data.map((row) => row.map((item) => item.formattedValue ?? '')),
                    totalRowCount: data.totalRowCount,
                };
            }"""
        )

    @staticmethod
    def _results_match_query(payload: dict[str, Any], queried_name: str) -> bool:
        rows = payload.get("rows")
        columns = payload.get("columns")
        if not isinstance(rows, list) or not isinstance(columns, list):
            return False
        if not rows:
            return False
        try:
            employer_index = columns.index("Employer")
            dba_index = columns.index("Doing Business As")
        except ValueError:
            return False

        def comparable(value: object) -> str:
            return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).split())

        needle = comparable(queried_name)
        return bool(needle) and all(
            needle in comparable(f"{row[employer_index]} {row[dba_index]}") for row in rows
        )

    @staticmethod
    def _is_empty_result(payload: dict[str, Any]) -> bool:
        return payload.get("rows") == [] and int(payload.get("totalRowCount", -1)) == 0


def _load_cache(path: Path, *, now: datetime) -> dict[str, object] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    retrieved = datetime.fromisoformat(str(payload["observation"]["retrieved_at"]))
    if now - retrieved.astimezone(UTC) > timedelta(days=CACHE_TTL_DAYS):
        return None
    return payload


class EVerifyEvidenceBuilder:
    """Persist priorities and a bounded slice of cached or live lookups."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        resolution_config_path: Path = Path("configs/entity_resolution.yaml"),
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.config = EntityResolutionConfig.from_yaml(resolution_config_path)

    def build_priorities(self) -> pl.DataFrame:
        priorities = build_everify_priorities(data_root=self.data_root)
        target = self.data_root / "processed" / "everify_lookup_priorities.parquet"
        write_parquet_atomic(priorities, target)
        return priorities

    def run(
        self,
        priorities: pl.DataFrame,
        *,
        limit: int,
        provider: EVerifySearchProvider | None,
    ) -> EVerifyBuildSummary:
        if limit < 0:
            raise ValueError("E-Verify lookup limit cannot be negative")
        selected = priorities.head(limit) if limit else priorities.head(0)
        observations_path = self.data_root / "processed" / "everify_observations.parquet"
        existing = (
            pl.read_parquet(observations_path)
            if observations_path.is_file()
            else pl.DataFrame(schema=EVERIFY_OBSERVATION_SCHEMA)
        )
        now = datetime.now(UTC)
        built: list[dict[str, object]] = []
        cache_hits = 0
        for priority in selected.iter_rows(named=True):
            cache_path = self.data_root / "cache" / "everify" / f"{priority['lookup_id']}.json"
            cached = _load_cache(cache_path, now=now)
            if cached is not None:
                observation = dict(cast(dict[str, object], cached["observation"]))
                observation["cache_hit"] = True
                built.append(observation)
                cache_hits += 1
                continue
            if not priority["query_is_safe"]:
                response = EVerifySearchResponse(rows=(), retrieved_at=datetime.now(UTC))
                observation = _observation(
                    priority,
                    response,
                    status="NOT_CHECKED",
                    confidence=0.0,
                    match_method="UNSAFE_SHORT_QUERY",
                    review_status="NEEDS_REVIEW",
                    review_reason="Full legal name is too short for a safe official-site lookup",
                )
            elif provider is None:
                raise ValueError(
                    "A live or test provider is required when lookup limit is positive"
                )
            else:
                try:
                    response = provider.search(str(priority["queried_name"]))
                    observation = _evaluate_matches(priority, response, self.config)
                except Exception as error:  # lookup failures are retained as reviewable evidence
                    response = EVerifySearchResponse(rows=(), retrieved_at=datetime.now(UTC))
                    observation = _observation(
                        priority,
                        response,
                        status="ERROR",
                        confidence=0.0,
                        match_method="LOOKUP_ERROR",
                        review_status="NEEDS_REVIEW",
                        review_reason=f"{type(error).__name__}: {error}",
                    )
            built.append(observation)
            write_json_atomic(
                cache_path,
                {"lookup": priority, "observation": observation},
            )

        new = (
            pl.DataFrame(built, schema=EVERIFY_OBSERVATION_SCHEMA)
            if built
            else pl.DataFrame(schema=EVERIFY_OBSERVATION_SCHEMA)
        )
        observations = (
            pl.concat([existing, new], how="vertical_relaxed")
            .sort(["lookup_id", "retrieved_at"])
            .unique("lookup_id", keep="last")
            .sort("priority_rank")
        )
        status_by_lookup = observations.select(
            "lookup_id", pl.col("enrollment_status").alias("observed_lookup_status")
        )
        priorities = (
            priorities.drop("lookup_status")
            .join(status_by_lookup, on="lookup_id", how="left")
            .with_columns(
                pl.col("observed_lookup_status").fill_null("NOT_CHECKED").alias("lookup_status")
            )
            .drop("observed_lookup_status")
        )
        priorities_path = self.data_root / "processed" / "everify_lookup_priorities.parquet"
        review_path = self.output_root / "review" / "everify_match_review.parquet"
        review = observations.filter(pl.col("review_status") == "NEEDS_REVIEW")
        write_parquet_atomic(priorities, priorities_path)
        write_parquet_atomic(observations, observations_path)
        write_parquet_atomic(review, review_path)
        counts = observations.group_by("enrollment_status").len()
        count_map = dict(
            zip(counts["enrollment_status"].to_list(), counts["len"].to_list(), strict=True)
        )
        return EVerifyBuildSummary(
            priority_count=priorities.height,
            attempted_count=selected.height,
            cache_hit_count=cache_hits,
            confirmed_active_count=count_map.get("CONFIRMED_ACTIVE", 0),
            confirmed_inactive_count=count_map.get("CONFIRMED_INACTIVE", 0),
            no_match_count=count_map.get("NO_MATCH", 0),
            ambiguous_count=count_map.get("AMBIGUOUS", 0),
            error_count=count_map.get("ERROR", 0),
            priorities_path=priorities_path,
            observations_path=observations_path,
            review_path=review_path,
        )
