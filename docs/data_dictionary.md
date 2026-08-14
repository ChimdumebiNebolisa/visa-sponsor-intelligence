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

## Phase 2 federal and institution tables

### USCIS H-1B petition decisions

Each `data/staging/uscis_h1b/fy=<year>/*.parquet` row is one employer/location/tax-ID-last-four observation for a single fiscal year. It contains initial, continuing, same-employer-change, concurrent, employer-change, and amended approval/denial counts. `employer_name_raw` and `source_line_id_raw` preserve the Tableau values; `source_row_number` is the deterministic row assigned after the long-form measure export is pivoted.

`evidence_type` is always `USCIS_H1B_PETITION_DECISIONS`. `legal_entity_id` and `parent_organization_id` remain null until entity resolution. A USCIS petition decision is never merged into a DOL case row.

### Institutions

`data/processed/institutions.parquet` contains the latest IPEDS directory with:

- `institution_id` (`ipeds:<six-digit UNITID>`)
- `ipeds_unitid`, `official_name`, `system_name`, city/state, and `official_domain`
- labeled `control`, `sector`, `highest_degree`, and `active_status`
- nullable legal-entity and parent-organization IDs for later resolution
- authoritative-source match confidence and review status

All IPEDS source columns remain available in the corresponding staging Parquet.

### HERD observations

`data/processed/herd_observations.parquet` contains one row per HERD institution, survey year, and form. `total_rd`, `federal_rd`, `business_funded_rd`, `institution_funded_rd`, `computing_rd`, and `engineering_rd` are whole U.S. dollars. Missing source values stay null. `rd_personnel` is a headcount when supplied by the standard questionnaire and null when the short form does not collect it.

Institution joins expose `institution_join_method`, `institution_match_confidence`, and `institution_review_status`. Only exact UNITID matches receive an `institution_id`; all other observations remain unmatched and appear in the review report.

## Phase 3 entity tables

### Legal entities

`data/resolved/legal_entities.parquet` has one row per legal identity. `legal_entity_id` is stable for the same inputs and decisions. `legal_name` is the retained canonical source or reviewed name; `normalized_legal_name` is matching evidence. Optional `parent_organization_id` never replaces the legal ID. Location, organization type, institution ID, creation method, and review status explain the identity's provenance.

### Parent organizations

`data/resolved/parent_organizations.parquet` contains only IPEDS system relationships and committed reviewed parent overrides. It includes the canonical parent name, organization type, optional headquarters state, staffing/consulting indicator, creation method, review status, and notes.

### Entity aliases and review queue

`data/resolved/entity_aliases.parquet` preserves `alias_raw` and adds normalized name, core name, acronym, normalized location, occurrence count, legal and parent IDs, candidate legal ID, match method, score, margin, status, feature values, and any reviewer evidence.

`outputs/review/entity_match_review.parquet` is the subset with `REVIEW_REQUIRED` or `REJECTED`. Its `legal_entity_id` identifies the separate provisional source entity. Its `candidate_legal_entity_id` is never an applied merge.

### Resolved source mirrors

Files under `data/resolved/sources/` preserve every staging column and add `legal_entity_id`, `parent_organization_id`, `entity_match_status`, `entity_match_method`, and `entity_match_score`. Staging Parquet is never modified. A blank source name may retain a null legal ID with `UNRESOLVED`; named records must have a legal identity.

## Phase 4 role classifications

`data/processed/role_classifications.parquet` contains one deterministic classification for each unique `(source_id, job_title_raw, soc_code_raw)` combination and its `occurrence_count`. `classification_id` and `classification_version` make the decision addressable and reproducible.

Classified DOL mirrors under `data/classified/sources/` preserve every resolved-source field and add nullable Boolean `technical_role`, `role_family`, `role_confidence`, `classification_method`, `classification_rule`, `classification_version`, and `review_status`. A null `technical_role` always uses family `ambiguous` and status `NEEDS_REVIEW`; it is not silently treated as false.
