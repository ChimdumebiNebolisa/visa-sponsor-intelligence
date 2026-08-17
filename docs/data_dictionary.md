# Product A data dictionary

This page documents active Product A artifacts. Historical V1/V2 score sidecars and retained
policy tables may coexist for reproducibility, but they do not define active product behavior.

## Provenance fields

Every normalized source row retains the source-specific columns and the canonical provenance
needed to trace it to immutable bytes:

- `source_id`, `source_artifact_id`, and `source_file_name`;
- `source_row_number`;
- fiscal/survey year and quarter/form where applicable;
- LCA `coverage_start_quarter` and `is_quarter_partition` where applicable;
- `is_partial_period`;
- official source URL and retrieval timestamp;
- raw artifact SHA-256 checksum;
- parser/schema version and ingestion timestamp.

`outputs/manifests/raw_downloads.jsonl` and `source_artifacts.jsonl` record the immutable download
and normalization receipts. Required-column loss fails closed. Schema drift remains visible in
`outputs/reports/schema/` and Data Health.

## DOL staging and classified evidence

Each DOL staging Parquet preserves all source columns with deterministic snake-case names. Key
canonical LCA fields include case identity, visa class, status and dates, raw employer and legal
address, raw title, SOC/NAICS, worker positions, worksite, wage, and prevailing wage. PERM retains
equivalent available fields plus its official form variant.

`employer_name_raw` and `job_title_raw` are never overwritten. Entity matching and role
classification occur in later mirrors, not staging.

Exact duplicates may be collapsed by the tested deterministic normalizer rule while retaining the
selected source row. Repeated PERM case IDs that differ only in decision date use the tested latest-
decision rule; arbitrary conflicts fail closed.

Repeated LCA case IDs are evaluated globally across all selected fiscal years, not only within one
artifact or year. The sole permitted supersession is exactly two chronological rows with the same
normalized visa class and legal-employer name/address, where the earlier state is `CERTIFIED` and
the later state is `CERTIFIED-WITHDRAWN`. Staging keeps both immutable source rows; resolved and
classified mirrors retain only the later state. The excluded source-artifact/row keys are recorded
in `outputs/reports/entities/lca_superseded_source_rows.parquet`. Every other repeated LCA case ID
fails closed.

Classified mirrors under `data/classified/sources/` add:

- `legal_entity_id` and nullable `parent_organization_id`;
- entity match status/method/score;
- nullable `technical_role`, normalized `role_family`, confidence, method, rule, version, and review
  status.

The visa class remains present so `H-1B`, H-1B1, and E-3 are queryable separately. Only technical
`H-1B` rows with weighted positive statuses can affect H-1B History.

## USCIS employer-level H-1B evidence

Each `data/staging/uscis_h1b/fy=<year>/*.parquet` row is one employer/location/tax-ID-last-four
observation for one fiscal year. It contains initial, continuing, same-employer-change,
concurrent, employer-change, and amended approval/denial counts. Raw employer and source-line
identifiers are preserved.

`evidence_type` is `USCIS_H1B_PETITION_DECISIONS`. These are employer-level petition decisions,
not title-specific DOL cases or worker counts. Product A uses only initial approvals as a 5%
corroborating H-1B component and labels them `Employer-level H-1B initial approvals`.

## Institution and HERD tables

`data/processed/institutions.parquet` contains the latest finalized IPEDS directory and
characteristics evidence, including:

- `institution_id` (`ipeds:<six-digit UNITID>`) and raw UNITID;
- official institution/system names, city/state, and official domain;
- labeled control, sector, highest degree, and active status;
- nullable legal-entity and parent IDs;
- linkage method/confidence/review status; and
- final/provisional source status.

IPEDS identity does not itself establish a petitioning legal entity or verified cap exemption.

`data/processed/herd_observations.parquet` contains one row per institution, survey year, and form.
It includes whole-dollar total, federal, business-funded, institution-funded, computing, and
engineering R&D where supplied, plus personnel where the standard form reports it. Missing
short-form fields stay null. Exact identifier linkage exposes its method/confidence/status;
unmatched records remain in review.

The latest matched HERD year supplies Research Scale inputs. Earlier years remain queryable.

## Entity tables

### Legal entities

`data/resolved/legal_entities.parquet` has one stable row per legal identity. It includes legal and
normalized names, location, organization type, optional institution and parent IDs, creation
method, and review status. A parent ID never replaces `legal_entity_id`.

### Parent organizations

`data/resolved/parent_organizations.parquet` contains only authoritative system relationships and
committed reviewed parent overrides. It includes canonical parent name, type, optional location,
creation method, review status, and notes.

### Aliases and review

`data/resolved/entity_aliases.parquet` preserves `alias_raw` and normalized name/location
features, occurrence count, legal/parent IDs, candidate ID, match method, score/margin, status, and
review evidence.

`outputs/review/entity_match_review.parquet` contains unmerged review candidates and reviewed
rejections. Its candidate ID is not an applied merge.

## Compact case/evidence tables

`lca_cases_resolved.parquet`, `perm_cases_resolved.parquet`, and
`h1b_petitions_resolved.parquet` retain the source artifact/file/period, raw evidence fields,
legal entity, optional parent, and an organization scope ID.

The primary case identity is the petitioning legal entity. Parent rollup rows are derived
separately and labeled `PARENT_ROLLUP`; they do not rewrite case-level legal ownership.

## Employer metrics and ratings

`data/processed/employer_metrics.parquet` contains separately labeled `LEGAL_ENTITY` and
`PARENT_ROLLUP` rows. It includes:

- legal/parent IDs, organization name/type/state, scope, and legal-entity count;
- raw and qualifying LCA/PERM counts, weighted counts, positive complete years, latest observed
  year, role-family/title summaries, worksite/state summaries, and partial-period markers;
- USCIS initial approvals;
- source-validity and rating coverage states;
- H-1B History score/status/stars/accessible label/explanation;
- Green Card Sponsorship History score/status/stars/accessible label/explanation;
- Overall Sponsorship score/status/stars/accessible label/explanation; and
- `metric_version = product_a_metrics_v1` and `score_version = product_a_scores_v1`.

The rating ingredients and formulas are defined in [scoring.md](scoring.md). A resolved zero under
valid coverage has `NO_OBSERVED_HISTORY` status and no stars. Missing/invalid coverage or
unresolved identity has `UNRATED` status and null score/stars. Neither state is encoded as one star.

## Institution metrics

`data/processed/institution_metrics.parquet` joins one IPEDS institution to its resolved legal
petitioner evidence without collapsing a campus, system, hospital, foundation, or laboratory.
It carries the three sponsorship ratings and raw counts, institution characteristics, latest HERD
measures, Research Scale status/stars/explanation, linkage/coverage, and the active metric/score
versions.

The permitted IPEDS context is `Higher-education institution; exact cap-exempt status requires
verification.` Research Scale and possible cap-exemption context do not affect sponsorship stars.

## Source and quality health

`data/processed/data_health.parquet` and the source-artifact presentation table expose selected
artifacts, official URLs, SHA-256 checksums, fiscal/survey periods, complete/partial state, raw and
normalized rows, schema versions, warnings, and freshness.

`data/processed/quality_checks.parquet` contains one row per Product A source selection,
schema/provenance, duplicate, entity, role, rating, independence, partial-period, nonzero-output,
or freshness check. It records `PASS`, `WARN`, or `FAIL`, whether the check blocks packaging, the
measured value/threshold/details, build ID, and check time. Policy completeness is not a Product A
quality gate.

`outputs/reports/quality/data_quality.json` is the machine-readable release decision.
`build_metadata.json` records the build ID, manifest checksum, Product A metric/score versions,
nonzero row counts, source periods, and quality result.

## DuckDB presentation layer

`db/immigration.duckdb` materializes processed Product A tables and read-only presentation views.
Streamlit accesses them only through `src/sponsor_intel/services/`. Core views cover employer and
institution exploration, detail, yearly LCA/PERM and employer-level USCIS evidence, legal/parent
relationships, role/title evidence, source artifacts, quality, and comparison.

## Supplemental evidence tables

`everify_lookup_priorities.parquet` is a bounded queue. `everify_observations.parquet` preserves
the official lookup result, matched identity, dates, confidence/review state, URL, and retained raw
result. Only confidently linked active/inactive observations receive those labels; no match,
ambiguous, not checked, and error map to `UNKNOWN`.

`opt_employer_observations.parquet` contains strictly positive official OPT/STEM OPT report rows,
program type, report year, positive count, provenance, and linkage review. Absence is `UNKNOWN`.

Policy candidate, document, fact, decision, and review-queue tables are retained supplemental
artifacts. Only current exact human-reviewed official facts may be displayed; all must be labeled
`Supplemental`, `Incomplete`, and `Not used in sponsorship ratings`. They are not required for
metrics, quality, database, release, tests, or app startup.

## Product A acceptance reports

The real-data acceptance family is:

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

These reports are derived artifacts. Raw data, Parquet, DuckDB, release bundles, caches, and
secrets remain ignored by Git.
