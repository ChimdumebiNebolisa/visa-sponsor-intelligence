# Phase 10 baseline

> Captured before any Phase 10 scoring or ranking changes against the latest checksum-verified quality-approved release.

## Release

- Git commit: `b28148c3cfd5e04277e60c057b73714e45792b61`
- Release tag: `data-2026-08-15`
- Build ID: `v1-df123235990f8fbc`
- Score version: `evidence_scores_v1_2026_08`
- Metric version: `scored_metrics_v1`
- Release checksum validation: **PASS**

## Counts

- Employers: 201,000
- Legal entities: 202,867
- Parent organizations: 431
- Institutions: 5,985
- Relevant LCA records: 451,158
- Relevant certified PERM records: 240,666
- Entity review queue: 0
- Ambiguous role classifications: 13,040

## Score coverage

| Score | Rows | Denominator | Coverage |
|---|---:|---:|---:|
| employer.stem_opt_readiness_score | 98 | 201,000 | 0.05% |
| employer.h1b_history_score | 146,326 | 201,000 | 72.80% |
| employer.green_card_history_score | 88,619 | 201,000 | 44.09% |
| employer.immigration_evidence_score | 70 | 201,000 | 0.03% |
| institution.research_strength_score | 911 | 5,985 | 15.22% |
| institution.policy_support_score | 23 | 5,985 | 0.38% |
| institution.research_pathway_score | 6 | 5,985 | 0.10% |

## Evidence and policy coverage

- E-Verify lookup coverage: 10 of 201,000 (0.0050%)
- Confirmed E-Verify coverage: 7 of 201,000 (0.0035%)
- Positive OPT coverage: 92 of 201,000 (0.0458%)
- Policy candidates attempted: 200
- Institutions with any accepted fact: 100
- Institutions with all four accepted core facts: 0

## Existing default ordering

- All Employers: `relevant_lca_count DESC, initial_approvals DESC, organization_name`
- Research Institutions: `total_rd DESC, relevant_lca_count DESC, official_name`
- Organization search: `relevant_lca_count DESC, organization_name`

## Representative local query latency

| Operation | Median | Runs | Result rows/bytes |
|---|---:|---|---:|
| employer_search | 332.62 ms | 351.96, 332.62, 319.04 | 500 |
| institution_search | 40.06 ms | 40.06, 34.75, 40.33 | 500 |
| organization_detail | 490.87 ms | 567.11, 490.87, 473.45 | n/a |
| comparison | 39.04 ms | 39.04, 35.17, 41.16 | 3 |
| full_filtered_employer_csv_export | 2316.52 ms | 2316.52 | 147938133 |
| full_filtered_institution_csv_export | 122.88 ms | 122.88 | 2600285 |

## Streamlit measurement

- Method: streamlit.testing.v1.AppTest Home.py execution
- Cold Home execution: 1792.03 ms
- Warm Home rerun: 85.51 ms
- Errors: 0

## Baseline quality result

- Passed: **True**
- Critical failures: 0
- Warnings: 1
  - `manifest_warnings`: 8 source artifacts retain validation warnings.
