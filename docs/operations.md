# Operations

## Local foundation workflow

1. Install Python 3.12 and `uv`.
2. Run `uv sync --frozen`.
3. Run Ruff, Pyright, and pytest.
4. Start the shell with `uv run sponsor-intel app`.

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

Full release and scheduled-workflow procedures will be added with the corresponding implementation phases.
