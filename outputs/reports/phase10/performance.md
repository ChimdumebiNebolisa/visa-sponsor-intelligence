# Phase 10 performance evidence

> Scope: local Windows predeployment measurement unless explicitly labeled deployed. Hosted Community Cloud measurements remain explicitly unmeasured until the owner completes a private deployment.

## Runtime footprint

- Current DuckDB: 276,312,064 bytes
- Four-asset release transfer: 276,319,023 bytes
- Local runtime bundle build: v2-dcdfeabb8229bb92 (evidence_scores_v2_2026_08)
- Clean dependency install duration: NOT_MEASURED
- Clean installed size: 593736400 (approximately 566.23 MiB)
- Clean install note: Isolated Windows 11 Python 3.12 venv; runtime imports and pip check passed; ingestion-only packages were absent; install duration was not reliably recorded.
- Local peak process working set: 834842624
- Deployed peak runtime memory: NOT_MEASURED

## Current local latency

| Operation | Median | Runs | Rows/bytes | Comparable baseline median |
|---|---:|---|---:|---:|
| employer_search | 370.56 ms | 377.22, 357.38, 370.56 | 500 | 332.62 ms |
| institution_ranking | 192.91 ms | 199.83, 192.91, 177.57 | 500 | n/a |
| organization_detail | 705.42 ms | 761.43, 705.42, 696.01 | n/a | 490.87 ms |
| comparison | 57.10 ms | 57.10, 55.86, 60.34 | 3 | 39.04 ms |
| filtered_employer_csv_export | 101.02 ms | 101.02 | 3851703 | n/a |
| filtered_institution_csv_export | 182.37 ms | 182.37 | 163667 | n/a |

## Streamlit execution

- Status: **PASS**
- Local cold Home execution: 3127.87 ms
- Local warm Home rerun: 640.76 ms
- Baseline cold Home execution: 1792.03 ms
- Baseline warm Home rerun: 85.51 ms
- Baseline institution and export operations used different filters/scopes; they are not shown as like-for-like comparators in the table.

## Not yet measurable

- Deployed cold start and cache recovery reliability.
- Deployed peak memory and platform resource headroom.
- Private authentication behavior for owner, invited user, signed-out browser, and non-invited account.
- Basic mobile usability in the deployed app.

These gaps are owner-action validation items; they are not silently treated as successful framework evidence.
