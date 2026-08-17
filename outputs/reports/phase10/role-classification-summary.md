# Phase 10 role-classification change report

Baseline: `role_classifications.parquet` (`role_taxonomy_v1`), SHA-256 `008026f75108e675ffb3795be5dda75c75c47cc9d672ed6d5779cfda2d7a2587`.

Candidate taxonomy: `role_taxonomy_v2`.

- Unique title/SOC combinations: 347,979
- Semantically changed combinations: 810
- Record-weighted changed rows: 11,662
- Stratified sample awaiting manual inspection: 43

## Before and after record counts

| status | before | after |
| --- | --- | --- |
| TECHNICAL | 707240 | 695830 |
| NOT_RELEVANT | 518725 | 530135 |
| AMBIGUOUS | 13040 | 13040 |

## Largest classification transitions

| before | after | changed_records | changed_combinations |
| --- | --- | --- | --- |
| TECHNICAL:software_engineering | NOT_RELEVANT:not_relevant | 3473 | 142 |
| TECHNICAL:other_computing | NOT_RELEVANT:not_relevant | 3144 | 140 |
| TECHNICAL:systems_infrastructure | NOT_RELEVANT:not_relevant | 1980 | 149 |
| TECHNICAL:technical_management_related | NOT_RELEVANT:not_relevant | 1372 | 44 |
| TECHNICAL:data_science | NOT_RELEVANT:not_relevant | 1275 | 92 |
| NOT_RELEVANT:not_relevant | NOT_RELEVANT:not_relevant | 224 | 177 |
| TECHNICAL:data_engineering | NOT_RELEVANT:not_relevant | 151 | 35 |
| TECHNICAL:computer_science_research | NOT_RELEVANT:not_relevant | 17 | 10 |
| TECHNICAL:data_science | TECHNICAL:data_science | 8 | 5 |
| TECHNICAL:software_engineering | TECHNICAL:software_engineering | 4 | 3 |
| TECHNICAL:systems_infrastructure | TECHNICAL:cloud | 3 | 3 |
| NOT_RELEVANT:not_relevant | TECHNICAL:cloud | 2 | 2 |
| TECHNICAL:computer_science_research | TECHNICAL:machine_learning_ai | 2 | 2 |
| TECHNICAL:technical_management_related | TECHNICAL:technical_management_related | 2 | 1 |
| TECHNICAL:data_engineering | TECHNICAL:software_engineering | 1 | 1 |
| TECHNICAL:data_science | TECHNICAL:data_engineering | 1 | 1 |
| TECHNICAL:data_science | TECHNICAL:technical_management_related | 1 | 1 |
| TECHNICAL:software_engineering | TECHNICAL:research_software | 1 | 1 |
| TECHNICAL:technical_management_related | TECHNICAL:data_science | 1 | 1 |

The complete changed-combination file and stratified inspection packet are `role-classification-changes.csv` and `role-classification-review-sample.csv`. Pending rows are not represented as human-reviewed.
