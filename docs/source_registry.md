# Product A source registry

`configs/sources.yaml` is the only machine-readable registry for production evidence. Each entry
declares the authority, official landing page and allowed domains, coverage floor, cadence,
formats, parser/schema versions, size limits, logical aliases, and schema fingerprints.

Production ingestion accepts official authoritative domains only. Every selected artifact records
its official URL, source ID, fiscal/survey period, complete or partial state, retrieval timestamp,
SHA-256 checksum, raw and normalized row counts, parser/schema version, and validation result.
Raw bytes and raw names are immutable.

## Canonical source matrix

| ID | Authority and official landing page | Product A selection | Rating role |
|---|---|---|---|
| `dol_lca` | [U.S. Department of Labor performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance) | FY2022 onward; prefer one annual artifact, otherwise use only the reviewed Q1–Q4 coverage segments for the verified FY2022–FY2025 archive; one latest cumulative snapshot for the current partial FY | Technical `H-1B` rows only |
| `dol_perm` | [U.S. Department of Labor performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance) | FY2022 onward under the same completed/partial rule; retain every selected form variant, including both FY2024 forms | Technical PERM rows |
| `uscis_h1b` | USCIS H-1B Employer Data Hub current/archive pages | One official employer-level dataset per FY from FY2022 through the latest available partial snapshot | Limited H-1B initial-approval corroboration |
| `ipeds` | [NCES IPEDS data files](https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx?year=-1) | Latest finalized HD directory and IC characteristics with matching dictionaries/value labels; provisional artifacts stay separate and labeled | Identity/classification context only |
| `herd` | [NSF/NCSES HERD microdata](https://ncses.nsf.gov/explore-data/microdata/higher-education-research-development) | Full and short public-use files for 2022, 2023, 2024, and later official years | Research Scale only |
| `sevp_opt` | ICE/SEVP official publication | Latest eligible official Top 200 employer report | Positive-only supplemental context |

E-Verify is a bounded official lookup rather than a bulk source-universe ingestion. Retained
institution policy pages are an optional manual supplemental workflow; see
[policy_extraction.md](policy_extraction.md). Neither source can alter sponsorship ratings.

## DOL LCA selection and semantics

DOL describes its disclosure releases as cumulative within a fiscal year. Real-file validation
found a narrower LCA archive exception: FY2022–FY2025 have no separate annual artifact, and the
linked files do not follow one consistent cumulative shape. FY2022 and FY2024–FY2025 contain four
exact quarters; FY2023 uses cumulative Q1–Q2 plus exact Q3 and Q4. Discovery therefore:

- prefers one final annual artifact when DOL publishes one;
- otherwise applies only the explicitly reviewed completed-year segment contract, persists
  `coverage_start_quarter` and the file's ending quarter, and requires Q1–Q4 coverage exactly once;
- enforces every segment's exact observed fiscal quarters and rejects arbitrary case conflicts
  globally across all selected fiscal years;
- recognizes only exactly two chronological rows with unchanged normalized visa class and legal-
  employer name/address, where a later `CERTIFIED-WITHDRAWN` supersedes an earlier `CERTIFIED`,
  retaining immutable source rows but using only the latest state downstream;
- rejects Q4 alone, an incomplete segment set, or an unreviewed completed-year shape;
- uses only the highest published cumulative quarter for the current partial fiscal year; and
- never concatenates current-year cumulative snapshots or combines an annual artifact with
  coverage segments.

This exception is source-shape reconciliation, not general permission to concatenate cumulative
files. The selected-artifact manifest and Data Health view expose the segment flag and coverage
bounds so the behavior is auditable.

Only `visa_class = H-1B` can affect H-1B History. H-1B1 and E-3 remain in normalized/queryable
evidence. `CERTIFIED` weighs 1.0, `CERTIFIED-WITHDRAWN` weighs 0.5, and unsuccessful statuses weigh
0. A certified LCA is not an approved H-1B petition.

The canonical record retains case identity, period, class/status/dates, raw employer name and legal
address, raw title, SOC/NAICS, worker positions, worksite, wage and prevailing wage, source row,
schema/form version, artifact ID, official URL, retrieval time, and checksum.

## DOL PERM selection and semantics

PERM uses the same one-snapshot-per-period rule, except every official form variant within the
selected snapshot is retained. The two FY2024 forms are normalized independently, labeled with
their form/schema version, and then unioned.

Stable exact duplicates may be removed deterministically while preserving the retained source row.
When duplicate case IDs differ only by decision date, the tested deterministic latest-decision
rule applies. Conflicting duplicate case IDs fail closed.

`CERTIFIED` weighs 1.0, `CERTIFIED-EXPIRED` weighs 0.5, and denied, withdrawn, or otherwise
unsuccessful statuses weigh 0. The product label is `Observed employer-sponsored PERM history`;
PERM evidence is never described as a green-card approval.

## USCIS H-1B Employer Data Hub

USCIS data is employer-level and not job-title-specific. The adapter prefers the official CSV,
requests/validates one fiscal year at a time when required, and rejects an artifact containing the
wrong fiscal year. If the official landing page blocks a non-browser discovery request, the build
may use only the reviewed publication boundary in `configs/sources.yaml`; the official artifact
still must pass domain, checksum, schema, and fiscal-period validation.

The exact UI label is `Employer-level H-1B initial approvals`. USCIS supplies a 5% corroborating
component; DOL LCA remains the role-level H-1B evidence.

## IPEDS identity and characteristics

Product A ingests the latest finalized IPEDS HD directory and IC characteristics, together with the
official dictionaries/value labels. `ipeds:<UNITID>` identifies the reported institution; UNITID
does not itself identify a petitioning legal employer or parent system. Provisional data, if
encountered, is stored separately and visibly labeled rather than silently replacing final data.

IPEDS supplies names, locations, official domains, control/sector/degree/status characteristics,
and linkage context. It supports only:

> Higher-education institution; exact cap-exempt status requires verification.

IPEDS alone never supports `Cap-exempt: Yes` and never changes sponsorship stars.

## HERD research context

Full and short HERD public-use files are disjoint survey populations and are both required for each
available year. Normalize them separately, retain survey year and form, then union without
double-counting an institution-year. Expenditures reported in thousands are converted to whole U.S.
dollars. Missing form fields remain null.

HERD-to-IPEDS linkage uses exact authoritative identifiers only; unmatched records enter review
without a name-based fallback. The latest matched year drives Research Scale while earlier years
remain queryable. HERD never changes sponsorship ratings.

## E-Verify and positive-only OPT

E-Verify lookup is explicitly bounded. Product states are Confirmed active, Confirmed inactive,
Ambiguous, Not checked, or Unknown. A no-match is not proof of non-enrollment and maps to Unknown.

OPT evidence is positive-only. Blank cells and absence from a published list do not create zero or
`NO`; they remain `UNKNOWN`. Each observation retains report year, program type, positive count,
official URL, retrieval time, checksum, and identity-link review metadata.

## Discovery and schema failure behavior

- HTTPS redirects must remain on an allowed official domain.
- Downloaders enforce compressed/uncompressed limits and reject HTML/error payloads masquerading as
  data.
- A required logical-column loss fails closed and produces a schema-diff report.
- A new optional layout produces a visible drift warning until reviewed and fingerprinted.
- Raw-download provenance is recorded before normalization so interrupted runs resume safely.
- The selected-source report is the audit record for why an artifact was used or excluded.

Run and inspect:

```bash
uv run sponsor-intel sources discover --source dol_lca --from-fy 2022
uv run sponsor-intel sources discover --source dol_perm --from-fy 2022
uv run sponsor-intel ingest --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_perm --from-fy 2022
```

Discovery and ingestion write source manifests under `outputs/manifests/`. After the real build,
`uv run python scripts/run_product_a_acceptance.py` regenerates the consolidated selection evidence
at `outputs/reports/product-a/source-selection.{md,json}`; do not treat a prior report as evidence
for a newer manifest.
