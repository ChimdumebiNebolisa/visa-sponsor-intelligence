# Sponsorship Intelligence Explorer

A private, evidence-first Streamlit application for exploring historical U.S. employer immigration activity and, in later phases, research-institution data and official policy evidence.

This repository contains the Phase 0 foundation and Phase 1 DOL LCA/PERM ingestion. It discovers official disclosure files, preserves immutable raw artifacts, validates source layouts, and writes provenance-rich Parquet staging data. Entity resolution, role classification, scoring, and OpenAI API calls are not part of Phase 1.

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
uv run sponsor-intel app
```

The Streamlit app remains an honest shell until the Phase 5 query layer is built. It uses `UNKNOWN` rather than turning absent evidence into a negative conclusion.

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
- `src/sponsor_intel/services/` is the boundary between user interfaces and future analytical storage.
- `src/sponsor_intel/sources/` implements official-domain discovery, immutable downloads, schema validation, normalization, and manifests.
- `app/` contains the minimal Streamlit multipage shell and never issues SQL directly.
- `configs/` contains safe non-secret configuration.
- `tests/` contains unit and integration tests; fixtures must be small and sanitized.
- `docs/` contains durable architecture and operations documentation.

Generated raw workbooks, Parquet files, reports, and manifests are ignored by Git. A verified full Phase 1 build on August 14, 2026 selected 11 official artifacts and produced 1,239,005 normalized rows; current partial-year snapshots remain labeled separately from complete fiscal years.

See `SPEC.md` for the approved product and engineering requirements. The original supplied filename is retained as `sponsorship-intelligence-explorer-spec.md` for traceability.

## Evidence disclaimer

This product reports historical and official evidence. It does not provide legal advice or guarantee that an employer will sponsor a particular person or role.
