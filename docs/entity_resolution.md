# Product A entity resolution

Product A preserves this identity chain:

```text
raw source employer observation
→ petitioning legal entity
→ reviewed or deterministically verified parent organization
```

Raw DOL, USCIS, and institution names are never overwritten. Immigration evidence stays attached
to the petitioning legal entity. A parent aggregation is a separate, clearly labeled rollup; it
never replaces or erases the constituent legal-entity rows.

University systems, campuses, affiliated hospitals, medical centers, foundations, institutes,
laboratories, and operators remain distinct unless authoritative evidence or a committed reviewed
override establishes the relationship. Ambiguity is a review outcome, not permission to guess.

## Inputs and normalization

Resolution reads only source artifacts whose parser and schema versions satisfy the active source
registry. It preserves each `alias_raw` while normalizing Unicode, case, whitespace, punctuation,
configured abbreviations, legal suffixes, city, five-character ZIP, and U.S. state or territory
names/codes for matching features.

Normalized names and suffix-stripped forms are candidate evidence, not replacement identities.
For example, capitalization variants of `Inc.` may normalize together, while conflicting legal
suffixes or incompatible locations block fuzzy auto-merge.

Employer identity uses petitioner/legal-employer address fields. Worksite location is activity
evidence and is never substituted for the legal-employer address.

## Matching sequence

1. Apply a committed reviewed alias, rejection, or relationship from
   `configs/entity_overrides.yaml`.
2. Accept an exact normalized legal name only when known legal-employer locations and scope do not
   conflict. Use address evidence to disambiguate duplicate exact names.
3. Generate fuzzy candidates only from reviewed legal entities and authoritative IPEDS UNITID
   identities. Source-created provisional identities do not expand later candidate pools.
4. Score token/character similarity together with state, city, ZIP, legal suffix, informative-token,
   and organization-scope compatibility.
5. Auto-accept only when every configured threshold and safeguard passes.
6. Route a plausible but nonqualifying match to review. The source observation keeps a separate
   provisional legal identity; `candidate_legal_entity_id` is a suggestion, not an applied merge.
7. Preserve lower-scoring named observations as deterministic source legal identities. Blank names
   remain unresolved with a null legal ID.

Fuzzy matching never creates a parent relationship. Parents come only from authoritative system
relationships or committed reviewed overrides. Name similarity alone is insufficient.

## Status semantics

- `DETERMINISTIC`: exact normalized identity, authoritative UNITID identity, or separate source
  legal identity.
- `HIGH_CONFIDENCE_AUTO`: every configured fuzzy-auto and legal-scope safeguard passed.
- `REVIEW_REQUIRED`: a plausible candidate exists, but no merge occurred.
- `UNRESOLVED`: no usable identity evidence was available.
- `REJECTED`: a reviewed candidate is explicitly prohibited; the source entity remains separate.
- `MANUAL_OVERRIDE`: a committed reviewed decision was applied.

An unresolved identity makes the affected rating `Unrated`; it is not converted to zero. A
resolved legal entity with valid source coverage and no qualifying technical history is a valid
`No observed … history` result.

Additional unresolved candidates do not invalidate confirmed records: scoreable confirmed evidence
is `PARTIAL_ENTITY_COVERAGE`, while insufficient confirmed identity evidence is
`UNRESOLVED_IDENTITY`. Partial coverage displays: `Rating is based on confirmed records. Additional
ambiguous records were excluded.`

## Product scopes

- `LEGAL_ENTITY`: the primary evidence scope. Case rows and employer-level source observations keep
  `legal_entity_id` as their organization identity.
- `PARENT_ROLLUP`: a separately materialized aggregation across reviewed child legal entities. It
  carries its own organization ID and scope label. Unreviewed candidate subsidiaries are excluded
  and disclosed; they never enter the rollup counts.

Detail and comparison views expose the legal ID, optional parent ID, scope, raw aliases, and
relationship evidence. A user must be able to compare a legal petitioner with its parent without
mistaking the two ratings for the same entity.

## Outputs

- `data/resolved/legal_entities.parquet`: legal-entity registry.
- `data/resolved/parent_organizations.parquet`: reviewed/authoritative parent registry.
- `data/resolved/entity_aliases.parquet`: aliases, normalized features, candidates, scores,
  statuses, and review evidence.
- `data/resolved/sources/<source>/fy=<year>/*.parquet`: resolved mirrors of immutable staging data.
- `outputs/review/entity_match_review.parquet`: unmerged review candidates and reviewed
  rejections.
- `outputs/reports/product-a/unresolved-entities.csv`: Product A acceptance inventory.

## Override and verification workflow

Add a legal entity or parent before referencing it from an alias. Every override records reviewer,
date, reason, and evidence. A rejection prohibits a candidate merge; it does not discard the source
observation.

```bash
uv run sponsor-intel entities validate-gold
uv run sponsor-intel entities build
uv run sponsor-intel metrics build
uv run python scripts/run_product_a_acceptance.py
```

Tests and acceptance must cover exact matches, legal-location conflicts, suffix conflicts,
ambiguous candidates, reviewed aliases/rejections, parent/legal separation, and deterministic
reruns. Named-company and named-university checks in
`outputs/reports/product-a/validation.{md,csv}` must state whether each row is a legal or parent
scope and leave unresolved ambiguity explicit.
