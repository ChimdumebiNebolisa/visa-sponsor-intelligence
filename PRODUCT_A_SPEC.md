# Historical Sponsorship Intelligence — Product A specification

**Version:** Product A 1.0  
**Status:** Authoritative active product specification  
**Effective:** 2026-08-16  
**Supersedes for active behavior:** `SPEC.md`

## Product definition

Product A is a private, local-first Streamlit application that ranks and explains U.S. employers,
universities, and research institutions using observed technical H-1B and PERM sponsorship history
from FY2022 onward.

It answers:

- which employers have certified LCAs for relevant technical H-1B roles;
- which employers have employer-level H-1B initial approvals in USCIS data;
- which employers have certified PERM history for relevant technical roles;
- whether activity is repeated, recent, and broad across normalized job families;
- which exact raw titles, statuses, locations, and wages support a rating;
- which universities and research institutions show the strongest observed sponsorship history;
- whether supplemental E-Verify or positive-only OPT evidence is available; and
- how research-intensive a matched institution is according to HERD.

The application does not claim that a current opening sponsors, that history guarantees future
sponsorship, that an LCA is an approved H-1B petition, that PERM certification is a green card, that
USCIS employer totals are technical-role approvals, that every university job is cap-exempt, or
that an institution's internal policy or a title's eligibility has been verified.

The following disclaimer must be prominent on primary pages:

> Ratings summarize observed historical evidence from official sources. They are not sponsorship
> guarantees or legal advice. Verify the exact position and current employer policy before relying
> on the result.

## Product boundary

- Streamlit, Python 3.12, Polars, Parquet, and DuckDB remain the V1 stack.
- UI pages use `src/sponsor_intel/services/`; they do not issue raw SQL.
- There is no job tracker, current-opening scraper, public API, Postgres, or Supabase.
- Only authoritative official sources may supply production evidence.
- Missing evidence is `UNKNOWN`/`Unrated`, never an unsupported negative.
- Raw source employer, petitioning legal entity, and parent organization remain distinct.

## Active and supplemental evidence

### Active sponsorship-rating evidence

- DOL LCA role-level records for H-1B only.
- DOL PERM role-level records.
- USCIS employer-level H-1B initial approvals as limited corroboration.

### Context that cannot change sponsorship ratings

- E-Verify enrollment evidence.
- Positive-only ICE/SEVP OPT evidence.
- IPEDS identity and institution type.
- HERD research expenditure.
- potential or reviewed cap-exemption context.
- retained institution policy evidence.

Retained policy evidence must be labeled:

- `Supplemental`
- `Incomplete`
- `Not used in sponsorship ratings`

Policy discovery and extraction are optional manual workflows. Normal ingestion, metrics, quality,
database builds, releases, tests, and application startup require no OpenAI API key.

## Official sources

`configs/sources.yaml` is the only machine-readable source registry.

### DOL LCA

- Landing page: `https://www.dol.gov/agencies/eta/foreign-labor/performance`
- Period: FY2022 onward.
- Select one final annual/Q4 snapshot per completed fiscal year.
- Select one latest cumulative snapshot for the current partial fiscal year.
- Never concatenate cumulative quarters.
- Preserve the selected record layout, complete/partial state, URL, retrieval time, and checksum.
- Only `H-1B` rows may affect H-1B ratings; H-1B1 and E-3 remain queryable.
- `CERTIFIED` weight is 1.0; `CERTIFIED-WITHDRAWN` is 0.5; every unsuccessful status is 0.

The normalized evidence includes case identity, period, class/status/dates, raw employer and legal
address, raw title, SOC/NAICS, worker positions, worksite, wage and prevailing wage, source row,
schema version, official URL, retrieval timestamp, and checksum.

### DOL PERM

- Same official landing page and FY2022-onward selection rule.
- Preserve every official form variant in the selected period, including both FY2024 forms.
- Normalize variants independently, record form version, then union.
- Stable exact duplicates may be removed deterministically; conflicting case IDs fail closed.
- `CERTIFIED` weight is 1.0; `CERTIFIED-EXPIRED` is 0.5; denied, withdrawn, and unsuccessful
  statuses are 0.
- User-facing label: `Observed employer-sponsored PERM history`.
- Never label PERM records as green-card approvals.

### USCIS H-1B Employer Data Hub

- Official current and archive landing pages only; official CSV is preferred.
- Period: FY2022 onward, including the latest partial snapshot when available.
- Data is employer-level and not job-title-specific.
- Exact UI label: `Employer-level H-1B initial approvals`.
- USCIS is a 5% corroborating component; DOL LCA supplies role-level evidence.

### IPEDS

- Landing page: `https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx?year=-1`.
- Use the latest finalized HD directory and IC characteristics with their dictionaries/value
  labels. Preserve and label provisional data separately when encountered.
- IPEDS supports identity, institution classification, and HERD linkage.
- Allowed context: `Higher-education institution; exact cap-exempt status requires verification.`
- IPEDS alone never supports `Cap-exempt: Yes`.

### NSF/NCSES HERD

- Landing page:
  `https://ncses.nsf.gov/explore-data/microdata/higher-education-research-development`.
- Ingest both full and short public-use files for 2022, 2023, 2024, and later official years.
- Preserve survey year/form/provenance and prevent institution-year double counting.
- The latest year drives Research Scale; history remains queryable.
- HERD affects only Research Scale.

### E-Verify and ICE/SEVP OPT

- E-Verify is a bounded targeted lookup, never a bulk-universe job.
- E-Verify statuses shown are Confirmed active, Confirmed inactive, Ambiguous, Not checked, or
  Unknown.
- OPT evidence is positive-only. Absence from a published list remains `UNKNOWN`.
- Neither source affects sponsorship stars.

## Technical-role classification

Classification uses normalized title and SOC together while preserving raw titles. Reviewed
overrides have highest priority. Strong exclusions execute before broad SOC inclusion.

Included families cover software engineering/development, research and scientific software,
computing research engineering, ML/AI, data engineering, database architecture, distributed
systems, infrastructure/platform/SRE, cloud/DevOps, HPC/research computing, and computer science
research.

Default exclusions include interns, student workers, postdoctoral fellows, assistant/associate/full
professors, lecturers, physicians, residents, nurses, recruiters, sales engineers/sales roles, help
desk/support, technicians, and unrelated science/engineering roles.

## Entity model

```text
raw source employer observation
→ petitioning legal entity
→ reviewed or deterministically verified parent organization
```

- Immigration evidence stays attached to the petitioning legal entity.
- Parent rollups are separate, clearly labeled rows.
- University systems, campuses, affiliated hospitals, medical centers, foundations, institutes,
  laboratories, and operators remain distinct.
- Identity matching uses legal-employer address, not worksite location.
- Conflicts and ambiguity remain unresolved or enter review.

## Active rating system

Hidden deterministic scores range from 0 to 100 and exist for sorting, testing, versioning, and
audit. Primary product tables display whole-star ratings and accessible labels, not hidden numbers,
letter grades, or probabilities.

### Common behavior

- Count components use `log1p` and a deterministic 95th-percentile cap calculated over eligible
  resolved employers for that build. The cap is persisted in score metadata.
- Complete-year consistency equals complete fiscal years with positive relevant activity divided
  by complete covered fiscal years since FY2022.
- The current partial year can affect recency but is not counted, compared as complete, or
  annualized.
- Recency is 1.00 for the current partial or latest complete year, 0.75 one complete year earlier,
  0.50 two years earlier, 0.25 three years earlier, and 0 thereafter.
- Breadth counts normalized relevant job families and is capped at five.

### H-1B History

- 45% weighted relevant certified H-1B LCA volume.
- 25% complete-year consistency.
- 15% recency.
- 10% relevant job-family breadth.
- 5% employer-level USCIS initial-approval corroboration.

Invalid/missing LCA coverage produces `Unrated`. Valid coverage with zero qualifying evidence
produces `No observed technical H-1B history`.

### Green Card Sponsorship History

- 45% weighted relevant PERM volume.
- 25% complete-year consistency.
- 15% recency.
- 15% relevant job-family breadth.

Invalid/missing PERM coverage produces `Unrated`. Valid coverage with zero qualifying evidence
produces `No observed technical PERM history`.

### Overall Sponsorship

- 40% H-1B History.
- 60% Green Card Sponsorship History.
- Both components must be resolved.

A resolved zero is valid history. A missing source or unresolved identity is not zero.

### Star mapping

| Hidden score | Display |
|---:|---|
| 80–100 | 5 stars |
| 65–<80 | 4 stars |
| 45–<65 | 3 stars |
| 25–<45 | 2 stars |
| >0–<25 | 1 star |
| 0 with valid required evidence | No observed technical sponsorship history |
| missing/invalid coverage or identity | Unrated |

Accessible labels use `N out of 5 stars`. Zero is never displayed as one star.

### Research Scale

Research Scale is a separate 1–5 star context rating among matched latest-year HERD institutions,
using computer and information sciences R&D first, engineering R&D second, and total R&D as
secondary context/fallback. It never changes sponsorship ratings and is never called Sponsorship
Potential.

## Streamlit product contract

### Home

Show purpose/limitations/disclaimer, build and score version, source freshness, latest complete FY,
partial-period warning, top observed employers, top observed institutions, and methodology.

### All Employers

Default order is Overall hidden score, Green Card hidden score, H-1B hidden score, latest observed
year, then employer name. Primary columns show stars and raw counts. Filters cover minimum stars,
organization type, state, job family, latest year, E-Verify, and minimum qualifying PERM/LCA counts.

### Universities and Research Institutions

Default order is observed sponsorship history, never R&D or policy. Show the institution, legal
employer, parent, control, the three sponsorship ratings, qualifying counts, employer-level initial
approvals, latest year, E-Verify, higher-education context, Research Scale, and coverage. Do not
provide internal-policy filters.

### Organization Detail

Show ratings, accessible labels, evidence-based explanations, legal/parent scope, yearly LCA/PERM,
employer-level USCIS activity, raw titles/families/statuses/locations/wages, supplemental E-Verify
and OPT, IPEDS/HERD, provenance, freshness, partial-period and entity warnings.

Every rating has `Why this rating` and `What this does not prove` text.

### Compare and Data Health

Compare up to five organizations across ratings, annual/raw evidence, families, E-Verify, Research
Scale, and coverage. Data Health lists the selected artifacts, official URLs, checksums, periods,
complete/partial state, raw/normalized rows, schema versions, entity/classifier/rating coverage,
supplemental evidence coverage, build ID, and score version.

## Quality and release

- Product A quality gates cover source selection, schema/provenance, duplicates, entity and role
  coverage, rating contracts, independence, partial-period semantics, nonzero outputs, and source
  freshness.
- Policy completeness is not a Product A gate or release requirement.
- A release requires no OpenAI API key.
- Streamlit smoke must have a real-database mode that rejects an empty or fallback database.
- Release publication remains forbidden while the GitHub repository is public.
- Deployment/visibility/hosting are owner actions; this implementation does not merge PR #10,
  publish a release, change visibility, or deploy Community Cloud.

## Required Product A evidence

The real build writes only this report family:

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

Validation covers Microsoft, Google, Amazon legal and parent scopes, Meta, IBM, Smart Data
Solutions when confidently resolved, two smaller technical employers, the six named universities,
and the two required contrasting HERD/sponsorship institutions. Ambiguity remains explicit.

