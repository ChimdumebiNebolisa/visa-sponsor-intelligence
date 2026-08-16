# Product A technical-role taxonomy

Every DOL LCA and PERM row receives a versioned deterministic classification from normalized title
and SOC evidence while preserving the raw title and SOC fields. Reviewed overrides have highest
priority. Product A does not use an LLM for bulk role classification.

## Classified fields

Classified source mirrors preserve every resolved-source field and add:

- `technical_role`: `true`, `false`, or null when evidence is ambiguous;
- `role_family`: a configured normalized family, `not_relevant`, or `ambiguous`;
- `role_confidence`: deterministic rule confidence from 0 to 1;
- `classification_method` and `classification_rule`: the evidence tier and exact rule;
- `classification_version`: the checked-in taxonomy version;
- `review_status`: `NOT_REQUIRED` or `NEEDS_REVIEW`.

Null/ambiguous is not silently treated as qualifying or nonqualifying evidence.

## Precedence

1. Apply an exact reviewed title override.
2. Evaluate strong exclusions before broad SOC inclusion.
3. Apply a strong technical title/SOC rule when it supplies specific computing evidence and does
   not conflict with an exclusion.
4. Apply reviewed SOC-family mappings.
5. Apply combined title/SOC rules.
6. Route generic or conflicting evidence to `ambiguous` review.
7. Default to `not_relevant` when no technical evidence exists.

Rules live in `configs/role_taxonomy.yaml` and run against Unicode-folded uppercase titles and
normalized SOC codes. A broad computing SOC cannot turn an excluded internship, faculty,
postdoctoral, medical, sales, recruiting, support, or technician title into Product A evidence.

## Included families

The Product A target universe covers:

- software engineering and development;
- research and scientific software;
- computing research engineering;
- machine learning and artificial intelligence;
- data engineering and database architecture;
- distributed systems;
- infrastructure, platform engineering, and site reliability engineering;
- cloud and DevOps;
- high-performance/research computing; and
- computer science research.

Configured technical family names remain versioned so evidence and scores can be reproduced.

## Strong default exclusions

The following are nontechnical for Product A unless an explicit reviewed override establishes a
different role:

- interns and student workers;
- postdoctoral fellows and postdocs;
- assistant, associate, and full professors;
- lecturers and other faculty/teaching titles;
- physicians, residents, nurses, and other clinical roles;
- recruiters and human-resources roles;
- sales engineers and other sales roles;
- help desk, desktop support, and general support roles;
- technicians; and
- unrelated scientific, medical, business, and engineering roles.

Generic `Research Scientist`, `Research Engineer`, `Engineer`, `Systems Engineer`, `Architect`,
`Scientist`, `Applied Scientist`, research-associate, and technical-lead titles are not assumed to
be computing roles. They require specific technical title/SOC evidence or enter review.

Institution type, employer name, E-Verify, OPT, HERD, and policy evidence never change a row's role
classification. Staffing/consulting context may be a separate explorer filter; it is not inferred
from title alone.

## Rating interaction

- Only rows classified `technical_role = true` can contribute.
- H-1B History additionally requires `visa_class = H-1B` and a weighted positive LCA status.
- Green Card Sponsorship History additionally requires a weighted positive PERM status.
- H-1B1/E-3 and unsuccessful cases remain queryable in raw evidence but contribute zero.
- Family breadth counts distinct normalized qualifying families, capped at five.

## Outputs and verification

- `data/processed/role_classifications.parquet`: one decision per unique source/title/SOC
  combination with occurrence count.
- `data/classified/sources/<source>/fy=<year>/*.parquet`: classified resolved-source mirrors.
- `outputs/review/role_classification_review.parquet`: ambiguous/low-confidence combinations.
- `outputs/reports/roles/summary.json`: record-weighted distribution.
- `outputs/reports/roles/gold_validation.json`: benchmark results.

```bash
uv run sponsor-intel roles validate-gold
uv run sponsor-intel roles build
uv run pytest tests/unit/test_role_classification.py
```

The gold set must cover every included family and the strong exclusions above across DOL sources
and employer types. Fixed defects require regression rows. Deterministic reruns must produce the
same classification lookup, review queue, and classified mirrors.
