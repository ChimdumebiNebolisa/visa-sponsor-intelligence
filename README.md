# Sponsorship Intelligence Explorer

A private, evidence-first Streamlit application for exploring historical U.S. employer immigration activity and, in later phases, research-institution data and official policy evidence.

This repository contains the Phase 0 foundation through Phase 5: official source ingestion, institution reconciliation, legal-entity resolution, deterministic technical-role classification, processed metrics, a DuckDB query layer, and the raw evidence explorer. It preserves immutable raw evidence, the raw-name to legal-entity to parent-organization chain, versioned SOC/title decisions, and explicit partial-period labels. E-Verify/OPT enrichment, scoring, and OpenAI policy extraction remain later-phase work.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- GNU Make is optional; every Make target is a thin alias for a documented `uv` command.

## Setup

```bash
uv sync --frozen
```

Copy `.env.example` to `.env` only when local overrides are needed. Do not put real secrets in committed files.

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
uv run sponsor-intel metrics build
uv run sponsor-intel db build
uv run sponsor-intel app
```

The Streamlit app queries only processed tables and DuckDB presentation views through the service layer. Build metrics and the database before starting it. E-Verify, OPT, policy, and score fields use `UNKNOWN` or null until their approved phases; absence is never rendered as `NO`.

## Quality checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run python scripts/smoke_streamlit.py
```

Where GNU Make is installed, the equivalent aliases are `make setup`, `make lint`, `make typecheck`, `make test`, `make smoke`, and `make app`.

## Architecture

- `src/sponsor_intel/` contains domain-neutral application code.
- `src/sponsor_intel/services/` contains parameterized, read-only DuckDB queries and export logic; Streamlit does not issue SQL.
- `src/sponsor_intel/sources/` implements official-domain discovery, immutable downloads, schema validation, normalization, and manifests.
- `src/sponsor_intel/entity_resolution/` implements conservative legal matching, parent safeguards, reviewed overrides, review routing, and gold validation.
- `src/sponsor_intel/role_classification/` implements versioned SOC/title classification, exclusions, review routing, and benchmark metrics.
- `src/sponsor_intel/metrics/` builds processed employer, institution, case, and source-health tables.
- `src/sponsor_intel/database/` materializes the required DuckDB presentation views.
- `app/` contains the Streamlit overview, employer, institution, and organization-detail explorer and never issues SQL directly.
- `configs/` contains safe non-secret configuration.
- `tests/` contains unit and integration tests; fixtures must be small and sanitized.
- `docs/` contains durable architecture and operations documentation.

Generated raw artifacts, Parquet files, reports, manifests, and DuckDB databases are ignored by Git. A verified full Phase 1 build on August 14, 2026 selected 11 official artifacts and produced 1,239,005 normalized rows. Phase 2 added 290,945 USCIS employer-year observations, 5,985 current IPEDS institutions, and 2,736 HERD institution-year/form observations. Phase 3 resolved 1,535,935 source rows into 202,867 legal entities and 431 parents. Phase 4 classified all 1,239,005 DOL records, including 707,240 technical and 13,040 ambiguous records. Phase 5 groups the legal identities into 201,000 employer rows and exposes 451,158 relevant LCA records plus 240,666 relevant certified PERM records. FY2025 is the latest complete immigration year; FY2026 Q2 DOL and current USCIS snapshots are visibly partial.

See `SPEC.md` for the approved product and engineering requirements. The original supplied filename is retained as `sponsorship-intelligence-explorer-spec.md` for traceability.

## Evidence disclaimer

This product reports historical and official evidence. It does not provide legal advice or guarantee that an employer will sponsor a particular person or role.
