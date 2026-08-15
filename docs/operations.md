# Operations

## Local workflow

1. Install Python 3.12 and `uv`.
2. Run `uv sync --frozen`.
3. Run Ruff, Pyright, and pytest.
4. Start the explorer with `uv run sponsor-intel app`.

## DOL Phase 1 workflow

1. Inspect configured sources with `uv run sponsor-intel sources list`.
2. Discover canonical artifacts with `uv run sponsor-intel sources discover --source dol_lca --from-fy 2022`.
3. Ingest LCA and PERM separately with the commands in `README.md`.
4. Review `outputs/manifests/raw_downloads.jsonl`, `outputs/manifests/source_artifacts.jsonl`, and `outputs/reports/schema/`.
5. Re-running an unchanged source reuses validated raw/Parquet artifacts. Use `--force-download` only to check whether an official URL was replaced in place.

An interrupted run can be rerun safely. Raw receipts are written before normalization, and complete artifacts remain content-addressed and manifested. The next run resumes without downloading the same bytes again. Generated raw, staging, report, and manifest artifacts remain outside Git.

Live contract tests are opt-in because they access the official DOL site:

```bash
SPONSOR_INTEL_RUN_NETWORK_TESTS=1 uv run pytest tests/contracts
```

In PowerShell, set `$env:SPONSOR_INTEL_RUN_NETWORK_TESTS='1'` before running pytest.

## Phase 5 metrics and explorer build

After ingestion, entity resolution, and role classification have completed:

```bash
uv run sponsor-intel metrics build
uv run sponsor-intel db build
uv run sponsor-intel app
```

The metrics command writes compact processed cases, employer and institution metrics, and source-health Parquet. The database command atomically materializes those tables, indexes organization identifiers, and creates all presentation views. Streamlit reads only that database through the service boundary.

The full verified build contains 201,000 employer groups, 202,867 separately retained legal entities, 5,985 institutions, 696,448 LCA cases, 542,557 PERM cases, and 290,945 USCIS employer-year rows. Parent/legal totals, source aggregates, partial-period labels, and future-field `UNKNOWN` semantics are checked after the build.

Measured on the development laptop, a filtered employer query completed in 1.52 seconds, an organization detail query in 0.61 seconds, and full employer CSV/Parquet exports in under two seconds. The fixture integration test builds a fresh DuckDB and verifies the same query/export boundary in CI.

When FY2026 is present, the application must continue showing the partial-period warning. Rebuilding metrics or the database is safe; outputs are written to temporary files and atomically replaced after successful completion.

## Phase 6 E-Verify and OPT workflow

Install the pinned Chromium browser once after dependency setup:

```bash
uv run playwright install chromium
```

Build the ICE evidence and queue without making any E-Verify request:

```bash
uv run sponsor-intel evidence build --everify-limit 0
```

Run an explicitly bounded live batch only after reviewing `data/processed/everify_lookup_priorities.parquet`:

```bash
uv run sponsor-intel evidence build --everify-limit 10
```

The builder selects a full legal entity name, blocks unsafe short queries, forces the official Tableau dashboard to its last-30-years range, waits for each committed query's results, and enforces at least five seconds between query commits. Each lookup is cached for 90 days under `data/cache/everify/`. Re-running a fresh lookup uses the cache and does not contact E-Verify.

Review `outputs/review/everify_match_review.parquet` and `outputs/review/opt_entity_review.parquet`. An E-Verify `NO_MATCH` is retained as raw lookup evidence but maps to explorer `UNKNOWN`. `AMBIGUOUS`, `ERROR`, and unsafe short-name results require review. OPT output contains positive observations only; unlinked or absent employer names remain `UNKNOWN`.

The command refreshes metrics and DuckDB after evidence persistence. `outputs/reports/evidence/phase6_summary.json` records the queue, lookup, linkage, and review counts.

## Phase 7 institution policy workflow

Keep the OpenAI key in the ignored `.env.local` or a secret process environment:

```text
OPENAI_API_KEY=...
OPENAI_POLICY_MODEL=gpt-5.6-luna
```

Generate and inspect the deterministic candidate set before making API calls:

```bash
uv run sponsor-intel policy candidates
```

Run a bounded build. The V1 production run uses 200 candidates; a smaller limit is useful for an operator check:

```bash
uv run sponsor-intel policy build --enrichment-limit 10
uv run sponsor-intel policy build --enrichment-limit 200
```

Inspect `data/processed/policy_documents.parquet`, `policy_facts.parquet`, `policy_review_queue.parquet`, and `outputs/reports/policy/errors.json`. Confirm the domain, page currency, campus or system scope, fact value, and exact excerpt before recording review decisions. The bounded helper does not accept cap-exemption or general-staff permanent-residence conclusions:

```bash
uv run sponsor-intel policy review-exact \
  --fact-ids outputs/review/policy_fact_ids.txt \
  --reviewer-id "operator-id" \
  --note "Official URL, current page, scope, affirmative value, and exact excerpt reviewed."
uv run sponsor-intel policy evaluate
uv run sponsor-intel metrics build
uv run sponsor-intel db build
```

Raw pages, discovery responses, and extraction responses are cached. Processed completed-document metadata is reusable for 24 hours, so those documents need no discovery, network, or OpenAI call on an immediate replay. Exact failed source URLs use a matching 24-hour retry backoff recorded in the error report. After 24 hours, sources are fetched again and unchanged text still produces no extraction call. Deleting cache files is not part of normal recovery; rerun the same command after a transient failure and the content-addressed completed work will be reused.

## Phase 8 scoring and comparison

Rebuild scores from the checked-in formula configuration, then refresh DuckDB:

```bash
uv run sponsor-intel scores build
uv run sponsor-intel db build
```

The score command atomically rewrites processed metrics and `employer_scores.parquet`. Invalid
weights or mappings fail before any output is replaced. Re-running unchanged evidence and
configuration is deterministic. Verify the formula contract with
`uv run pytest tests/unit/test_scoring.py tests/integration/test_metrics_explorer.py` and inspect
the Compare page with one to five organizations. Unknown scores, partial coverage, raw counts,
score version, and explanations must remain visible. Scores must be described only as historical
evidence strength, never as legal probability.

The OpenAI live contract is intentionally opt-in and must run only where a secret key is available:

```bash
RUN_LIVE_OPENAI_POLICY_TEST=1 uv run pytest tests/contracts/test_phase7_policy_contracts.py
```

## Clean setup and reproduction

From a clean clone, install only the locked Python environment and Chromium:

```bash
uv sync --frozen
uv run playwright install chromium
```

To restore the latest private quality-approved build, authenticate `gh`, download the latest
release's `processed-parquet.zip`, `build-state.zip`, and `source-manifests.zip`, then extract each
at the repository root. Rebuild and verify the presentation layer:

```bash
uv run sponsor-intel scores build
uv run sponsor-intel quality report
uv run sponsor-intel db build
uv run python scripts/smoke_streamlit.py
```

To reproduce government data from authoritative sources instead of restoring it, run
`uv run sponsor-intel refresh government --everify-limit 0`. This ingests FY2022 onward, rebuilds
entities, roles, OPT evidence, metrics, scores, quality checks, and DuckDB. Restore the previous
private release first when reviewed policy history and extraction caches must carry forward. A
first-ever policy bootstrap still requires the explicit Phase 7 review workflow.

## Scheduled refresh workflows

`.github/workflows/refresh_government_data.yml` runs on January, April, July, and October 1 and can
also be dispatched manually. It restores reusable reviewed state when a release exists, refreshes
official sources, runs live contracts, requires the publication quality report to pass, builds the
release assets, and uploads a short-retention workflow artifact. Scheduled E-Verify live lookups
default to zero; a manual operator must explicitly supply a bounded limit.

`.github/workflows/refresh_policies.yml` runs every four months and requires the GitHub Actions
`OPENAI_API_KEY` secret. It restores the latest approved build, refreshes the bounded 200-institution
policy set, reruns the reviewed benchmark, metrics, quality checks, DuckDB, and release packaging.

`.github/workflows/publish_data_release.yml` runs only after a successful refresh (or an explicit
run-ID dispatch). It downloads the exact workflow artifact, verifies every SHA-256 checksum and the
zero-critical-failure quality result, then creates or updates the private repository release tagged
`data-YYYY-MM-DD`. Failed refreshes and failed critical checks cannot reach the publication job.

## Quality gates and Data Health

Run:

```bash
uv run sponsor-intel quality report
uv run sponsor-intel db build
```

The report fails nonzero for missing processed tables/columns/manifests/schema reports, failed
source validations, duplicate source keys, entity coverage below 95%, role coverage below 98%,
legal/parent ID collisions, fewer than 100 reviewed institutions, invalid accepted evidence,
inconsistent score versions/coverage, or missing freshness rows. Source `WARNING` statuses remain
visible and reviewable but do not masquerade as failures. `sponsor-intel release bundle` refuses to
package any build whose current report has a critical failure.

The Data Health page displays source row counts and periods, warnings, build ID, manifest/schema
versions and checksum, failed checks, identity/role coverage, policy coverage, and score contract.

## Private release contents

After quality and DuckDB pass, run `uv run sponsor-intel release bundle`. It atomically creates:

- `immigration.duckdb`
- `processed-parquet.zip`
- `build-state.zip` for resolved/classified state and policy caches
- `source-manifests.zip`
- `data-quality.json`
- `build-metadata.json`
- `checksums.sha256`

Large data, databases, and release assets remain ignored by Git. GitHub Releases inherit the
repository's private visibility.

## Full V1 acceptance

After the Phase 9 commit is on `main`, build the release bundle and run:

```bash
uv run python scripts/run_v1_acceptance.py --verify-restore
```

The acceptance command evaluates all 20 definition-of-done requirements, queries the private
repository and exact current-main CI result, extracts the release archives into a temporary clean
root, rebuilds metrics, quality outputs, and DuckDB from the restored state, and writes the
machine-readable result to `outputs/reports/acceptance/v1.json`. It exits nonzero if any item is
not supported by current build evidence.

## Failure recovery

- Interrupted ingestion, evidence, or policy refresh: rerun the same command. Immutable artifacts,
  source manifests, document hashes, extraction caches, and retry backoff resume completed work.
- Source schema or required-column failure: do not use cached outputs to publish. Inspect
  `outputs/reports/schema/`, update the reviewed source contract only after checking the official
  record layout, add a regression test, and rerun from discovery.
- Quality failure: inspect `data/processed/quality_checks.parquet` or the Data Health page. No
  release bundle is created and the publish workflow is not triggered.
- Policy API/network failure: rerun after the recorded retry window. Unchanged document hashes use
  the validated extraction cache and do not incur another extraction call.
- Database build failure: the existing DuckDB remains intact because replacement is atomic. Fix
  the reported table/view issue and rerun `sponsor-intel db build`.
- Release upload failure after a successful refresh: verify the retained workflow artifact, then
  manually dispatch `Publish private data release` with that successful refresh run ID. Checksums
  and quality are verified again before upload.
- Missing/corrupt private release: checksum verification must fail closed. Re-run the last
  successful refresh; never silently fall back to an unverified local bundle.
