# Entity resolution

Phase 3 implements the required identity chain:

`raw source name -> legal entity -> parent organization`

Raw DOL, USCIS, and IPEDS values are never overwritten. Evidence remains attached to a legal entity. Parent-level totals must be derived from the reviewed `parent_organization_id`; a parent is never used as a substitute for its constituent legal identities.

## Inputs and normalization

The build reads only manifest records whose parser and schema versions match the current source registry. Stale artifacts are excluded. It normalizes Unicode, case, whitespace, punctuation, configured abbreviations, legal suffixes, city, five-character ZIP, and U.S. state or territory names/codes. The raw value remains in `alias_raw`.

Name normalization and legal-suffix removal produce candidate features, not replacement evidence. For example, `Inc.` and `INC` normalize alike, but `Inc.` and `LLC` are treated as a legal-suffix conflict and cannot fuzzy-auto merge.

## Matching sequence

1. Apply a committed reviewed alias or rejection from `configs/entity_overrides.yaml`.
2. Accept an exact normalized legal name only when known legal-employer locations do not conflict. A unique name candidate with a conflicting state or city remains separate and is routed to review. When authoritative records contain duplicate exact names, location disambiguates them.
3. Generate fuzzy candidates only from reviewed legal entities and authoritative IPEDS UNITID identities. Source-created provisional identities never expand later fuzzy candidate pools.
4. Score token and character similarity plus state, city, and ZIP agreement.
5. Auto-accept only when score is at least `0.97`, the candidate margin is at least `0.05`, location agrees, legal suffixes do not conflict, and informative token counts and organization-scope markers are compatible.
6. Route scores from `0.80` through the auto threshold to review. The observed name receives its own provisional legal identity; `candidate_legal_entity_id` is only a suggestion.
7. Preserve lower-scoring named observations as deterministic source legal identities. Blank names remain `UNRESOLVED` and retain null legal IDs.

Fuzzy matching never creates a parent relationship. Parents come only from official IPEDS system identifiers or committed reviewed overrides. A campus and its system therefore retain different IDs.

Employer identity uses petitioner or legal-employer address columns. Worksite city, state, and ZIP remain activity evidence and are never substituted for the legal-employer location. Polluted historical PERM state/province values are normalized only when a known U.S. state can be identified; otherwise they remain missing rather than becoming a false state code.

## Status semantics

- `DETERMINISTIC`: exact normalized identity, authoritative UNITID identity, or a new source legal identity.
- `HIGH_CONFIDENCE_AUTO`: all configured fuzzy-auto and legal-scope safeguards passed.
- `REVIEW_REQUIRED`: a plausible candidate exists, but no merge occurred.
- `UNRESOLVED`: no usable name was supplied.
- `REJECTED`: a reviewed candidate is explicitly prohibited; the observed entity remains separate.
- `MANUAL_OVERRIDE`: a committed reviewed alias was applied.

## Outputs

- `data/resolved/legal_entities.parquet`: legal entity registry.
- `data/resolved/parent_organizations.parquet`: reviewed and IPEDS-derived parent registry.
- `data/resolved/entity_aliases.parquet`: raw aliases, normalized features, scores, statuses, and review evidence.
- `data/resolved/sources/<source>/fy=<year>/*.parquet`: immutable-staging mirrors with entity columns added.
- `outputs/review/entity_match_review.parquet`: unmerged review candidates and reviewed rejections.
- `outputs/reports/entities/top_entity_inspection.parquet`: deterministic top-100 inspection sample.
- `outputs/reports/entities/summary.json`: machine-readable build summary.
- `outputs/reports/entities/gold_validation.json`: gold-set acceptance results.

All generated data remains ignored by Git. Configuration, reviewed decisions, gold examples, code, tests, and documentation are committed.

## Override workflow

Add a legal entity or parent before referencing it from an alias. Every alias, rejection, and parent override records a reviewer, review date, and reason. A rejection identifies the prohibited candidate; it does not discard the source observation. Run both commands after changing decisions:

```bash
uv run sponsor-intel entities validate-gold
uv run sponsor-intel entities build
```

Reviewed decisions have regression coverage. Do not add a parent mapping based only on name similarity.

## Validation evidence

The committed gold CSV contains 201 pairs: the original 50 technology employers, 50 universities, and 25 each for university systems, hospitals or medical organizations, research institutes or national laboratories, and staffing or consulting firms, plus an exact-name/conflicting-state regression. Current results retain 100% auto-accepted precision, zero false auto-accepts, zero parent/legal collapses, and route all 26 ambiguous pairs without merging.

Phase 10 audit packets are generated with `python scripts/generate_phase10_data_quality_reports.py`. The packet covers at least 30 significant companies and 30 significant universities or research institutions. Generated rows remain `PENDING_HUMAN_REVIEW` until a person records the review; the generator never auto-approves uncertain parents or location conflicts.

The verified full build on August 14, 2026 produced:

- 365,261 distinct source name/location observations;
- 202,867 legal entities and 431 parent organizations;
- resolved mirrors for 1,535,935 DOL, USCIS, and IPEDS source records;
- 383 high-confidence automatic matches, 1,014 review-required candidates, 147 applied reviewed aliases, one encountered reviewed rejection, and 26 unresolved blank-name observations;
- no duplicate registry IDs, no parent orphans, no review candidate merged into its suggested legal entity, and no nonblank source record without a legal ID.

Manual inspection covered the highest-volume legal identities and the lowest-scoring auto-accepted matches. Amazon legal petitioners remain separate while rolling up to the reviewed Amazon parent; Meta Platforms and Facebook remain separate legal entities under Meta; the reviewed Walmart petitioner is separate under Walmart. Microsoft Corporation and Google LLC each converge across DOL LCA, DOL PERM, and USCIS after full-state normalization. Potential hospital, athletic-corporation, system-office, campus, foundation, and research-scope differences are routed to review.

Two consecutive full builds produced identical SHA-256 hashes for the legal registry, parent registry, alias registry, review queue, and top-inspection table.
