# Data dictionary

## Phase 1 staging invariants

Each DOL Parquet file preserves all source columns with deterministic snake-case names and adds canonical logical columns plus:

- `source_artifact_id`
- `source_row_number` (the retained 1-based Excel row, including the header offset)
- `source_id`
- `fiscal_year`
- `fiscal_quarter`
- `is_partial_period`
- `source_file_name`
- `ingested_at`

`employer_name_raw` and `job_title_raw` preserve source values. No entity merge, parent aggregation, or role classification occurs in staging.

Required logical columns are configured in `configs/sources.yaml`. Missing required fields fail closed and produce a JSON schema-diff report. Source columns are preserved, and their ordered layout is compared with a committed fingerprint.

Exact duplicate source rows are collapsed deterministically while retaining the first source row number. If repeated case IDs differ only by decision date, the latest decision is retained. Any repeated case ID with other conflicting source fields fails validation. The schema report records each removal; raw workbooks remain immutable and unchanged.
