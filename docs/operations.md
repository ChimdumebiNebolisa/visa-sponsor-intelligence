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

Full release and scheduled-workflow procedures will be added with the corresponding implementation phases.
