# Role taxonomy

Phase 4 assigns every DOL LCA and PERM row a versioned deterministic classification. V1 does not use an LLM for bulk role classification.

## Required output

Classified source mirrors preserve every Phase 3 source column and add:

- `technical_role`: `true`, `false`, or null when evidence is ambiguous;
- `role_family`: one of the 15 configured taxonomy values;
- `role_confidence`: deterministic rule confidence from 0 to 1;
- `classification_method`: the evidence tier that made the decision;
- `classification_rule`: the exact reviewed mapping or rule ID;
- `classification_version`: currently `role_taxonomy_v1`;
- `review_status`: `NOT_REQUIRED` or `NEEDS_REVIEW`.

## Classification precedence

1. Exact reviewed title override.
2. SOC-code family mapping.
3. Strong positive title pattern.
4. Strong exclusion pattern.
5. Combined SOC plus title rule.
6. Ambiguous-title routing.
7. Default to `not_relevant` when no technical evidence exists.

The taxonomy and all rules live in `configs/role_taxonomy.yaml`. Rules run against Unicode-folded uppercase titles and normalized SOC codes. Strong technical rules contain local guards for sales, recruiting, medical, and internship terms so an earlier positive pattern cannot bypass a required exclusion.

## Role families

The technical families are `software_engineering`, `research_software`, `research_engineering`, `machine_learning_ai`, `data_engineering`, `data_science`, `systems_infrastructure`, `hpc`, `cloud`, `devops_sre`, `computer_science_research`, `technical_management_related`, and `other_computing`. The two nontechnical decision families are `not_relevant` and `ambiguous`.

## Conservative exclusions

Generic Research Scientist, Research Engineer, Engineer, Systems Engineer, Architect, Scientist, Applied Scientist, research-associate, postdoc, and Technical Lead titles are not assumed technical. They enter the review queue unless a prior computing SOC, strong computing title, or combined SOC/title rule supplies evidence.

Explicit medical, biological/chemical, noncomputing faculty, sales engineering, recruiting, helpdesk/desktop support, generic business analysis/project management, internship, and clearly nontechnical production or service titles are excluded. A technical SOC may still establish computing evidence before a title exclusion, which preserves legitimate cases such as nursing informatics. Staffing-firm placement filtering is applied later as an employer-level query filter; it is not inferred from a job title.

## Outputs and commands

- `data/processed/role_classifications.parquet`: one row per unique source/title/SOC combination, with occurrence counts.
- `data/classified/sources/<source>/fy=<year>/*.parquet`: complete classified DOL mirrors.
- `outputs/review/role_classification_review.parquet`: all unique low-confidence or ambiguous combinations.
- `outputs/reports/roles/summary.json`: live record-weighted distribution.
- `outputs/reports/roles/gold_validation.json`: benchmark metrics.

```bash
uv run sponsor-intel roles validate-gold
uv run sponsor-intel roles build
```

## Validation evidence

The committed validation CSV contains 750 manually labeled records formed from 75 reviewed title/SOC cases across FY2022–FY2026, both DOL sources, and six employer types: technology, university, hospital/medical, research institute, staffing/consulting, and other industry. It includes all technical families plus required medical, biological, faculty, sales, recruiting, support, management, internship, generic research, and generic engineering controls.

Current benchmark results are 100% precision for `technical_role = true`, 100% recall for the labeled target universe, 100% correct technical family assignment, and 100% routing of expected low-confidence cases. These exceed the required 95% precision, 90% recall, and 90% family-accuracy thresholds.

The verified full build classified all 1,239,005 DOL records. It marked 707,240 technical, 518,725 not relevant, and 13,040 ambiguous across 347,979 unique source/title/SOC combinations. The review queue contains 1,337 unique combinations. Technical coverage is 64.1–66.2% for LCA and 39.7–58.6% for PERM across source years. Generic research and engineering titles dominate the highest-volume ambiguous cases. The only three technical records whose titles contain medical terms have explicit computing/data SOC evidence and were manually inspected as nursing-informatics or data-analysis cases.

Two consecutive full builds produced identical SHA-256 hashes for the classification lookup, review queue, and every classified source mirror.
