# Sponsorship Intelligence Explorer

An evidence-first Streamlit application intended for private, local-first exploration of historical U.S. employer immigration activity, research-institution data, E-Verify enrollment evidence, positive OPT/STEM OPT observations, and reviewed official institution policies.

This repository contains Phase 0 through Phase 10 implementation: official source ingestion, institution reconciliation, conservative legal-entity resolution, deterministic technical-role classification, V1-preserving V2 evidence scores, decision-readiness rankings, a read-only DuckDB service layer, policy review, quality-gated releases, and a fail-closed hosted-data bootstrap. It preserves immutable raw evidence, the raw-name to legal-entity to parent-organization chain, versioned SOC/title and score decisions, retrieval evidence, review queues, explicit coverage, and partial-period labels.

The code is ready for a private Community Cloud deployment, but the live repository is currently public. Do not describe the application or its existing release assets as private until the owner completes and verifies the steps in [the Community Cloud runbook](docs/deployment/community-cloud.md).

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- GNU Make is optional; every Make target is a thin alias for a documented `uv` command.

## Setup

```bash
uv sync --frozen
uv run playwright install chromium
```

Copy `.env.example` to `.env.local` only when local overrides are needed. `.env.local` is ignored by Git. Keep `OPENAI_API_KEY` there or in a secret process environment; never put it in YAML, fixtures, logs, or committed files.

## Run

```bash
uv run sponsor-intel --help
uv run sponsor-intel config
uv run sponsor-intel sources list
uv run sponsor-intel sources discover --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_perm --from-fy 2022
uv run sponsor-intel ingest --source ipeds --from-fy 2022
uv run sponsor-intel ingest --source herd --from-fy 2022
uv run sponsor-intel ingest --source uscis_h1b --from-fy 2022
uv run sponsor-intel entities validate-gold
uv run sponsor-intel entities build
uv run sponsor-intel roles validate-gold
uv run sponsor-intel roles build
uv run sponsor-intel evidence build --everify-limit 10
uv run sponsor-intel policy candidates
uv run sponsor-intel policy build --enrichment-limit 200
uv run sponsor-intel policy review-exact --fact-ids outputs/review/policy_fact_ids.txt --reviewer-id "operator-id" --note "Official URL, current page, scope, value, and exact excerpt reviewed."
uv run sponsor-intel policy evaluate
uv run sponsor-intel metrics build
uv run sponsor-intel scores build
uv run sponsor-intel quality report
uv run sponsor-intel db build
uv run sponsor-intel release bundle
uv run sponsor-intel app
```

The Streamlit app queries only processed tables and DuckDB presentation views through the service layer. `evidence build` ingests the official ICE report, creates the complete E-Verify priority queue, performs only the explicitly bounded number of live searches, refreshes metrics, and rebuilds DuckDB. Use `--everify-limit 0` to perform no live E-Verify requests. E-Verify no-match/ambiguous results and absence from the positive-only OPT report are rendered as `UNKNOWN`, never `NO`.

`policy build` ranks exactly 200 eligible institutions, confines discovery and fetching to reviewed official domains, parses the fetched page, extracts all required facts through strict OpenAI Structured Outputs, and caches results by document hash, extractor version, and model. Every extracted fact starts in `NEEDS_REVIEW`; only an explicit list of inspected fact IDs and review decision can publish it to institution metrics and organization detail. General-staff permanent-residence and cap-exemption conclusions are excluded from the exact-fact review helper and require individual review.

## Quality checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python scripts/smoke_streamlit.py
```

Where GNU Make is installed, the equivalent aliases include `make setup`, `make lint`, `make typecheck`, `make test`, `make smoke`, `make policy`, `make policy-evaluate`, and `make app`.

## Architecture

- `src/sponsor_intel/` contains domain-neutral application code.
- `src/sponsor_intel/services/` contains parameterized, read-only DuckDB queries and export logic; Streamlit does not issue SQL.
- `src/sponsor_intel/sources/` implements official-domain discovery, immutable downloads, schema validation, normalization, and manifests.
- `src/sponsor_intel/entity_resolution/` implements conservative legal matching, parent safeguards, reviewed overrides, review routing, and gold validation.
- `src/sponsor_intel/role_classification/` implements versioned SOC/title classification, exclusions, review routing, and benchmark metrics.
- `src/sponsor_intel/metrics/` builds processed employer, institution, case, and source-health tables.
- `src/sponsor_intel/evidence/` builds positive OPT observations and cached, rate-limited E-Verify evidence.
- `src/sponsor_intel/policy/` ranks candidates, discovers and fetches official documents, performs schema-constrained extraction, validates exact evidence, caches unchanged results, and applies review decisions.
- `src/sponsor_intel/scoring/` applies the V1 formulas in `configs/scoring.yaml` and canonical V2 formulas in `configs/scoring_v2.yaml` without converting missing evidence to zero.
- `src/sponsor_intel/quality/` computes visible, publication-blocking V2 gates; `src/sponsor_intel/releases/` creates release assets only after those gates pass.
- `src/sponsor_intel/deployment/` verifies the minimum four release assets and opens the hosted DuckDB read-only; invalid or missing deployment data fails closed.
- `src/sponsor_intel/database/` materializes the required DuckDB presentation views.
- `app/` contains the Streamlit overview, employer, institution, and organization-detail explorer and never issues SQL directly.
- `configs/` contains safe non-secret configuration.
- `tests/` contains unit and integration tests; fixtures must be small and sanitized.
- `docs/` contains durable architecture and operations documentation.

Generated raw artifacts, Parquet files, ordinary reports, release bundles, and DuckDB databases remain ignored by Git; bounded Phase 10 audit and acceptance reports are explicitly included. The restored baseline release is `data-2026-08-15` / `v1-df123235990f8fbc`. Rebuilding with the Phase 10 rules produces 225,996 employer rows, 227,863 separate legal entities, 431 parents, and 5,985 institutions. The location-safe resolver routes 21,091 observations to review instead of silently merging conflicts. Role V2 classifies 695,830 of 1,239,005 DOL records as technical and leaves 13,040 ambiguous. The V2 quality build `v2-dcdfeabb8229bb92` passes with zero critical failures; all 50 priority institutions still require bounded core-policy review (197 of 200 fact questions remain incomplete). FY2025 is the latest complete immigration year; FY2026 Q2 remains visibly partial.

Start with [the user workflow](docs/USER_WORKFLOW.md), inspect [the UAT protocol](docs/UAT.md), and use [the deployment runbook](docs/deployment/community-cloud.md) for the remaining owner-only privacy and hosting actions.

See `SPEC.md` for the approved product and engineering requirements. The original supplied filename is retained as `sponsorship-intelligence-explorer-spec.md` for traceability.

## Evidence disclaimer

This product reports historical and official evidence. It does not provide legal advice or guarantee that an employer will sponsor a particular person or role.
