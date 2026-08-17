# Product A operations

Product A is a local-first, read-only explorer. Normal government-data ingestion, metrics,
ratings, quality, database, release packaging, tests, and startup do not require an OpenAI key or
policy evidence.

## Local setup

```bash
uv sync --frozen
uv run sponsor-intel --help
```

In Windows PowerShell, use `py -m uv` if the `uv` executable is not on `PATH`:

```powershell
py -m uv sync --frozen
py -m uv run sponsor-intel --help
```

Generated raw files, Parquet, reports, manifests, caches, release bundles, and DuckDB databases
remain outside Git. Never commit `.env`, `.env.local`, tokens, API keys, or request headers.

## Authoritative-source discovery and ingestion

Inspect the registry and discover before ingestion:

```bash
uv run sponsor-intel sources list
uv run sponsor-intel sources discover --source dol_lca --from-fy 2022
uv run sponsor-intel sources discover --source dol_perm --from-fy 2022
uv run sponsor-intel sources discover --source uscis_h1b --from-fy 2022
```

For DOL PERM, verify one final annual/Q4 period per completed fiscal year and one highest cumulative
quarter for the current partial year; retain both selected FY2024 PERM form variants. For DOL LCA,
prefer an annual artifact when present. The reviewed archive contract uses four exact segments for
FY2022 and FY2024–FY2025, and cumulative Q1–Q2 plus exact Q3 and Q4 for FY2023. Verify the persisted
`coverage_start_quarter`/ending-quarter bounds cover Q1–Q4 exactly once and observed decision
quarters exactly match those bounds. Across all selected fiscal years, a repeated case ID must fail
unless it is exactly two chronological rows with unchanged normalized visa class and legal-employer
name/address, moving from certified to certified-withdrawn; resolved output retains only that later
state. The current partial fiscal year must still select only its highest cumulative quarter.

Ingest each source explicitly:

```bash
uv run sponsor-intel ingest --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_perm --from-fy 2022
uv run sponsor-intel ingest --source uscis_h1b --from-fy 2022
uv run sponsor-intel ingest --source ipeds --from-fy 2022
uv run sponsor-intel ingest --source herd --from-fy 2022
```

Immediately after ingestion, review `outputs/manifests/raw_downloads.jsonl`,
`outputs/manifests/source_artifacts.jsonl`, `outputs/manifests/discovery/`, and
`outputs/reports/schema/`. Stop if a required logical column is missing or an official schema
changed unexpectedly. The Product A acceptance command later regenerates the consolidated
`outputs/reports/product-a/source-selection.{md,json}` from those inputs.

Raw receipts are written before normalization, and artifacts are content-addressed. An interrupted
run can be rerun safely; unchanged validated content is reused. Use `--force-download` only for a
deliberate official-URL replacement check.

Official-source contract tests are opt-in:

```powershell
$env:SPONSOR_INTEL_RUN_NETWORK_TESTS='1'
py -m uv run pytest tests/contracts
```

## Entity, role, metrics, and database build

```bash
uv run sponsor-intel entities validate-gold
uv run sponsor-intel entities build
uv run sponsor-intel roles validate-gold
uv run sponsor-intel roles build
uv run sponsor-intel metrics build
uv run sponsor-intel quality report
uv run sponsor-intel db build
```

The entity build keeps immigration evidence on petitioning legal entities and creates separately
labeled parent rollups only for authoritative/reviewed relationships. Do not bulk-approve the
entity review queue.

The role build applies strong exclusions before broad SOC inclusion. Inspect the role summary and
review queue, especially faculty, postdoctoral, medical, sales, support, technician, and generic
research/engineering titles.

The metrics build writes `product_a_metrics_v1` with `product_a_scores_v1`. Verify:

- only technical `H-1B` LCA rows influence H-1B History;
- current partial-year activity affects recency only and is not annualized;
- unsuccessful LCA/PERM statuses add no rating weight;
- parent and legal scopes remain separate;
- valid zero observations display `No observed … history` and not one star;
- missing/invalid coverage displays `Unrated`; and
- E-Verify, OPT, IPEDS/HERD, cap-exemption context, and policy do not change sponsorship ratings.

Rebuilding metrics and DuckDB is atomic; a failed replacement must leave the prior database
intact.

## Run and smoke-test the real application

```bash
uv run python scripts/smoke_streamlit.py --database db/immigration.duckdb
uv run sponsor-intel app
```

Real-database smoke mode fails if the supplied database is absent, empty, invalid, or a fallback.
The fixture mode (omit `--database`) remains useful for CI but is not real-data acceptance.

The app reads DuckDB only through `src/sponsor_intel/services/`. Confirm Home, All Employers,
Universities and Research Institutions, Organization Detail, Compare, and Data Health load nonzero
rows. The latest complete fiscal year and current partial-period warning must be visible.

## Product A acceptance

After the real metrics, quality report, and DuckDB are current:

```bash
uv run python scripts/run_product_a_acceptance.py
```

This command regenerates the consolidated source-selection report; do not rely on a report left by
an earlier build. Inspect it together with the other acceptance outputs below.

The command must exit nonzero when an automated Product A requirement fails and write:

```text
outputs/reports/product-a/
  source-selection.md
  source-selection.json
  score-distribution.md
  score-distribution.json
  validation.md
  validation.csv
  unresolved-entities.csv
  acceptance.md
  acceptance.json
```

Validation covers Microsoft, Google, Amazon legal and parent scopes, Meta, IBM, Smart Data
Solutions when confidently resolved, two smaller technical employers, six named universities, and
the required contrasting HERD/sponsorship institutions. A result that remains ambiguous must be
reported as ambiguous rather than forced to pass.

## Supplemental E-Verify and OPT

Install the pinned browser only when running supplemental evidence collection:

```bash
uv run playwright install chromium
uv run sponsor-intel evidence build --everify-limit 0
```

`--everify-limit 0` builds the priority queue and positive OPT evidence without live E-Verify
requests. Review `data/processed/everify_lookup_priorities.parquet` before supplying a small
positive limit. The lookup enforces safe full-name queries, rate limits, and caching.

Review `outputs/review/everify_match_review.parquet` and
`outputs/review/opt_entity_review.parquet`. E-Verify no match/ambiguous/error/not checked and OPT
absence remain `UNKNOWN`; neither can alter stars.

## Supplemental policy workflow

Institution policy extraction is optional, manual, incomplete, and excluded from Product A
ratings, ordering, quality gates, releases, and startup. Only this workflow may need
`OPENAI_API_KEY`; see [policy_extraction.md](policy_extraction.md).

```bash
uv run sponsor-intel policy candidates
uv run sponsor-intel policy build --enrichment-limit 10
```

Do not run an unbounded extraction. Every model fact starts in review and cannot become product
evidence without explicit human verification of official domain, exact excerpt, currency, and
campus/system scope. Policy failures must not block a government-data refresh or Product A build.

## Quality and release bundle

```bash
uv run sponsor-intel quality report
uv run sponsor-intel db build
uv run sponsor-intel release bundle
```

Product A quality gates cover canonical source selection, schema/provenance, duplicates, entity and
role coverage, score/star contracts, legal/parent separation, supplemental-evidence independence,
partial-period semantics, nonzero outputs, and freshness. Policy completeness is not a gate.

The release builder verifies the current zero-critical-failure report, nonzero Product A metadata,
active metric/score versions, DuckDB views, and checksums before packaging. It may create local
ignored assets such as the presentation database, data-quality/build metadata, processed/build
state archives, source manifests, and `checksums.sha256`.

**Do not publish a release while the GitHub repository is public.** Release assets inherit
repository visibility. Existing public assets may already have been copied and are not made private
retroactively. Publication, repository visibility changes, PR merge, and deployment are owner
actions; see [deployment/community-cloud.md](deployment/community-cloud.md).

## Scheduled workflows

- `.github/workflows/refresh_government_data.yml` refreshes authoritative government data,
  entities, roles, metrics, Product A ratings, quality, DuckDB, and local workflow artifacts.
  E-Verify defaults to no live lookup. It must not require policy state or an OpenAI key.
- `.github/workflows/refresh_policies.yml` is manual-only supplemental maintenance. It must not
  trigger or gate Product A release publication.
- `.github/workflows/publish_data_release.yml` verifies exact workflow artifacts, checksums,
  quality, metadata, and private repository visibility. Public state must fail closed before upload.

## Failure recovery

- **Interrupted ingestion:** rerun the same command; manifests and content-addressed artifacts
  allow safe resume.
- **Source schema failure:** stop, inspect `outputs/reports/schema/`, compare the official layout,
  update the reviewed contract and regression tests, then rediscover/reingest.
- **Entity ambiguity:** retain the separate provisional legal entity and add a reviewed override
  only with evidence.
- **Quality failure:** inspect `data/processed/quality_checks.parquet` and Data Health; do not bundle
  or publish.
- **Database failure:** fix the reported table/view contract and rerun; do not delete a known-good
  database as a recovery shortcut.
- **Supplemental policy/API failure:** leave Product A evidence intact. Retry only the optional
  workflow after its recorded backoff; unchanged document hashes reuse the cache.
- **Release upload failure:** keep publication stopped, verify the retained artifact and repository
  privacy, then let the owner rerun the protected publish workflow.
- **Missing/corrupt hosted release:** checksum verification fails closed. Never serve an empty or
  unverified fallback in release mode.

## Full verification before handoff

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python scripts/smoke_streamlit.py --database db/immigration.duckdb
uv run python scripts/run_product_a_acceptance.py
```
