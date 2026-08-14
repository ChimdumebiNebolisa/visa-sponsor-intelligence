# Sponsorship Intelligence Explorer

A private, evidence-first Streamlit application for exploring historical U.S. employer immigration activity and, in later phases, research-institution data and official policy evidence.

This repository currently contains the Phase 0 foundation. It intentionally performs no source discovery, downloads, ingestion, entity resolution, scoring, or OpenAI API calls.

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
uv run sponsor-intel app
```

The Phase 0 app is an honest shell: it reports that no evidence data has been loaded and uses `UNKNOWN` rather than displaying a negative conclusion.

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
- `app/` contains the minimal Streamlit multipage shell and never issues SQL directly.
- `configs/` contains safe non-secret configuration.
- `tests/` contains unit and integration tests; fixtures must be small and sanitized.
- `docs/` contains durable architecture and operations documentation.

See `SPEC.md` for the approved product and engineering requirements. The original supplied filename is retained as `sponsorship-intelligence-explorer-spec.md` for traceability.

## Evidence disclaimer

This product reports historical and official evidence. It does not provide legal advice or guarantee that an employer will sponsor a particular person or role.
