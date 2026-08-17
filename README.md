# Historical Sponsorship Intelligence — Product A

A private, local-first Streamlit explorer for observed U.S. employer sponsorship history from
FY2022 onward. Product A ranks employers, universities, and research institutions using technical
H-1B LCA and PERM records from the U.S. Department of Labor, with employer-level USCIS H-1B
initial approvals as limited corroboration. IPEDS and HERD add institution identity and research
context.

> Ratings summarize observed historical evidence from official sources. They are not sponsorship
> guarantees or legal advice. Verify the exact position and current employer policy before relying
> on the result.

The active contract is [PRODUCT_A_SPEC.md](PRODUCT_A_SPEC.md). `SPEC.md`, Phase 10/V2 decision
readiness, letter grades, research pathways, and policy-dependent rankings are retained historical
context and do not define current behavior.

## What Product A does

- Preserves immutable source artifacts, selected official URLs, retrieval times, checksums, schema
  versions, and raw employer/title values.
- Keeps the petitioning legal entity separate from any reviewed parent organization. Immigration
  evidence remains attached to the legal entity; a parent rollup is a separate labeled scope.
- Scores only technical `H-1B` LCA rows for H-1B History. H-1B1 and E-3 remain queryable but do not
  affect ratings.
- Scores technical PERM records as observed employer-sponsored PERM history, never as green-card
  approvals or a promise to sponsor.
- Distinguishes a validated resolved zero (`No observed technical … history`) from missing or
  invalid evidence (`Unrated`). Zero never becomes one star.
- Rates confirmed records under `PARTIAL_ENTITY_COVERAGE` when additional ambiguous candidates are
  excluded, and reserves identity-based `Unrated` for `UNRESOLVED_IDENTITY`.
- Shows E-Verify, positive-only OPT, institution type, possible cap-exemption context, HERD, and
  retained policy evidence only as supplemental context. None can change sponsorship stars.

## Requirements and setup

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- GNU Make is optional

```bash
uv sync --frozen
```

On Windows PowerShell, use `py -m uv` if `uv` is not on `PATH`:

```powershell
py -m uv sync --frozen
py -m uv run sponsor-intel --help
```

Normal Product A ingestion, scoring, quality, database, release, test, and app workflows do not
need an OpenAI key. Copy `.env.example` to the ignored `.env.local` only for local overrides or an
explicit, manual supplemental policy run. Never commit secrets.

## Build from official evidence

The machine-readable authority registry is `configs/sources.yaml`. Discovery prefers one final
annual artifact for a completed DOL period and one latest cumulative current-period artifact. The
verified FY2022–FY2025 LCA archive is an official-source exception: FY2022 and FY2024–FY2025 use
four exact quarters, while FY2023 uses cumulative Q1–Q2 plus exact Q3 and Q4. Product A persists
and validates those reviewed coverage bounds, rejects gaps and arbitrary case conflicts, and
collapses only a tested later certified-withdrawn state for the same stable case.
Current-year LCA still uses only the latest cumulative snapshot. Both FY2024 PERM form variants
are retained.

```bash
uv run sponsor-intel sources list
uv run sponsor-intel sources discover --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_perm --from-fy 2022
uv run sponsor-intel ingest --source uscis_h1b --from-fy 2022
uv run sponsor-intel ingest --source ipeds --from-fy 2022
uv run sponsor-intel ingest --source herd --from-fy 2022
uv run sponsor-intel entities validate-gold
uv run sponsor-intel entities build
uv run sponsor-intel roles validate-gold
uv run sponsor-intel roles build
uv run sponsor-intel metrics build
uv run sponsor-intel quality report
uv run sponsor-intel db build
```

The current partial fiscal year may affect recency, but is not annualized and never counts as a
complete comparison year. Inspect Data Health and
`outputs/reports/product-a/source-selection.{md,json}` before using a build.

## Run the real database and app

```bash
uv run python scripts/smoke_streamlit.py --database db/immigration.duckdb
uv run sponsor-intel app
```

`--database` is the real-data smoke mode: it rejects a missing, empty, or fallback database. The
app queries `db/immigration.duckdb` only through `src/sponsor_intel/services/`; Streamlit pages do
not issue raw SQL.

Run Product A acceptance after rebuilding the real database:

```bash
uv run python scripts/run_product_a_acceptance.py
```

The report family is written under `outputs/reports/product-a/` and includes source selection,
score distribution, named-organization validation, unresolved entities, and acceptance results.

## Rating summary

Hidden deterministic 0–100 scores support sorting, testing, and audit. Primary tables display
whole stars and accessible labels (`N out of 5 stars`).

| Rating | Formula |
|---|---|
| H-1B History | 45% weighted qualifying LCA volume, 25% complete-year consistency, 15% recency, 10% family breadth, 5% employer-level USCIS initial approvals |
| Green Card Sponsorship History | 45% weighted qualifying PERM volume, 25% complete-year consistency, 15% recency, 15% family breadth |
| Overall Sponsorship | 40% H-1B History and 60% Green Card Sponsorship History; both must be resolved |

Counts use `log1p` with a persisted deterministic 95th-percentile cap. Certified records weigh
1.0; `CERTIFIED-WITHDRAWN` LCA and `CERTIFIED-EXPIRED` PERM weigh 0.5; unsuccessful statuses weigh
0. See [docs/scoring.md](docs/scoring.md) for the exact star bands and treatment of complete versus
partial years.

HERD Research Scale is a separate 1–5-star context rating and never changes sponsorship ratings.

## Supplemental evidence

An optional bounded evidence run can refresh positive-only OPT and a reviewed subset of E-Verify:

```bash
uv run playwright install chromium
uv run sponsor-intel evidence build --everify-limit 0
```

Use a positive bounded E-Verify limit only after reviewing the lookup queue. No match, ambiguity,
not checked, and errors remain `UNKNOWN`. Policy discovery/extraction is a manual supplemental
workflow documented in [docs/policy_extraction.md](docs/policy_extraction.md); it is incomplete and
not used in any sponsorship rating or release gate.

## Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python scripts/smoke_streamlit.py --database db/immigration.duckdb
```

Opt-in official-source contracts require network access:

```powershell
$env:SPONSOR_INTEL_RUN_NETWORK_TESTS='1'
py -m uv run pytest tests/contracts
```

## Architecture

- `src/sponsor_intel/sources/`: official discovery, immutable download, validation, normalization,
  and manifests.
- `src/sponsor_intel/entity_resolution/`: conservative legal-entity matching, separate parent
  relationships, overrides, and review queues.
- `src/sponsor_intel/role_classification/`: deterministic title/SOC classification with strong
  exclusions and reviewed overrides.
- `src/sponsor_intel/metrics/` and `src/sponsor_intel/scoring/`: Product A metrics, scores, stars,
  explanations, and coverage states.
- `src/sponsor_intel/database/` and `src/sponsor_intel/services/`: read-only DuckDB presentation
  boundary.
- `app/`: Home, employer, institution, organization detail, compare, evidence, and Data Health
  pages.
- `src/sponsor_intel/evidence/` and `src/sponsor_intel/policy/`: supplemental evidence only.
- `src/sponsor_intel/quality/`, `src/sponsor_intel/releases/`, and
  `src/sponsor_intel/deployment/`: fail-closed Product A quality and delivery controls.

## Privacy, releases, and deployment

The GitHub repository is public. Do not publish a new data release while it remains public, and do
not describe the app or its data as private or deployed. Existing public release assets may already
have been copied; changing visibility does not retract them.

Repository visibility, PR merge, release publication, Community Cloud secrets/sharing, and hosted
validation are owner actions. Follow
[docs/deployment/community-cloud.md](docs/deployment/community-cloud.md). The implementation must
not perform those actions automatically.

Start with [the user workflow](docs/USER_WORKFLOW.md), use [the Product A UAT
protocol](docs/UAT.md), and consult [operations](docs/operations.md) for reproducible builds and
failure recovery.
