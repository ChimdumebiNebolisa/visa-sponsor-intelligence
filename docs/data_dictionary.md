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

## Phase 5 processed metrics and presentation views

`lca_cases_resolved.parquet`, `perm_cases_resolved.parquet`, and `h1b_petitions_resolved.parquet` are compact processed case/evidence tables. They retain source artifact, file, ingestion time, fiscal period, legal entity, parent organization, and `organization_id`. `organization_id` is the reviewed parent when one exists and otherwise the legal entity; it never replaces either identity field.

`employer_metrics.parquet` has one row per parent-or-legal organization scope. It contains the legal-entity count; organization type and state; raw LCA, relevant LCA, relevant certified PERM, and USCIS petition counts; active and last-observed years; technical-family/title summaries; worksite states; source coverage; partial-period markers; and `metric_version = scored_metrics_v1`. Its legal-entity counts sum to the full legal-entity table. The 26 blank-name USCIS observations retain null identity and are excluded from organization aggregation rather than guessed.

`institution_metrics.parquet` has one row per IPEDS institution and joins the latest available HERD measures plus immigration counts at the institution's legal petitioner. Research expenditures remain separate fields for total, federal, computing, and engineering R&D. IPEDS institutions receive `POTENTIALLY_CAP_EXEMPT_HIGHER_ED`; this is not a verified cap-exemption decision.

`data_health.parquet` reports source row counts, coverage years, the latest complete year, and current partial year/quarter. Phase 5 identifies FY2025 as the latest complete immigration year and FY2026 as partial; DOL is currently available through Q2.

Every explorer row displays `evidence_classes`. Raw source presence is `OBSERVED_GOVERNMENT_RECORD`; an aggregation is `DERIVED_METRIC`. E-Verify and OPT remain `UNKNOWN` unless Phase 6 has linked qualifying evidence. Institution-policy values remain `UNKNOWN` unless Phase 7 has an exact, current, `REVIEWED_ACCEPTED` fact; those rows add `REVIEWED_OFFICIAL_POLICY`. Phase 8 scores are nullable and carry coverage, confidence, grade/status, explanation, and `score_version`; a missing score must not be presented as negative evidence.

`db/immigration.duckdb` materializes the processed tables and evidence views, including `vw_everify_evidence`, `vw_opt_evidence`, `vw_policy_evidence`, and their review queues. `vw_policy_evidence` retains both reviewed and unreviewed facts with a visible evidence class; application detail queries filter it to `REVIEWED_ACCEPTED`.

## Phase 6 E-Verify and OPT evidence

`everify_lookup_priorities.parquet` is built before lookups and contains `priority_rank`, legal and parent IDs, `queried_name`, state, priority tier/reason/score, a safe-query flag, and current lookup status. Activity employers precede top research institutions and manual targets.

`everify_observations.parquet` contains the lookup ID; queried legal name and identity IDs; raw `enrollment_status`; enrollment/termination dates; workforce and hiring-site evidence; matched employer/DBA; retrieval time; match confidence/method; review status/reason; source URL; and retained source-result JSON. Only `CONFIRMED_ACTIVE` and `CONFIRMED_INACTIVE` become product statuses. `NO_MATCH`, `AMBIGUOUS`, `NOT_CHECKED`, and `ERROR` map to product `UNKNOWN`.

`opt_employer_observations.parquet` contains the official artifact and report year, source employer name, rank, program type, strictly positive reported count, source URL/retrieval/checksum, coverage note, legal/parent/organization IDs when a unique exact match exists, and review metadata. An employer can have up to three observations for `OPT_OR_STEM_OPT`, `OPT`, and `STEM_OPT`; a blank source cell does not create a zero row.

## Phase 7 institution policy evidence

`policy_candidates.parquet` contains the deterministic 200-institution enrichment rank and its component values. It is not a product score.

`policy_documents.parquet` contains document and institution IDs, official URL/domain, type/title, retrieval time and HTTP metadata, content and parsed-text hashes, published/updated date when found, immutable raw/parsed paths, current/parse status, discovery method, injection flag, and cache status.

`policy_facts.parquet` contains one row for every required fact type per extracted document. It preserves the enum value, qualifier, smallest supporting excerpt, section/page, official source URL, retrieval and validity dates, extractor/model/response IDs, confidence, exact-excerpt result, contradiction group, current state, review status, reviewer ID/time, and reviewer note. Model output begins in `NEEDS_REVIEW`; a separate review-decision overlay is required for `REVIEWED_ACCEPTED`.

`policy_review_queue.parquet` contains all facts still requiring review. `institution_metrics.parquet` maps only accepted, current, exact HTTPS facts into the five Phase 7 policy summary fields and sets `policy_review_status` to `REVIEWED` or `NEEDS_REVIEW` without treating absent facts as `NO`.

## Phase 8 scores

`employer_scores.parquet` contains one row per organization with the STEM OPT readiness, H-1B
history, green-card history, and immigration evidence composite outputs. Each output includes a
nullable score, coverage/confidence, status or grade, and explanation. The same columns remain next
to their raw inputs in `employer_metrics.parquet`.

`institution_metrics.parquet` additionally contains HERD percentile inputs, research strength,
reviewed policy support, and the research pathway composite. Policy scores use only exact, current,
human-reviewed official facts. `score_version = evidence_scores_v1_2026_08` identifies the formula
configuration; see `docs/scoring.md` for the complete definitions.
