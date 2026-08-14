# Codex instructions

## Purpose and stack

- This repository builds a private, local-first sponsorship intelligence explorer.
- Read `SPEC.md` before changing architecture, evidence semantics, or product behavior.
- Use Python 3.12, `uv`, Typer, Pydantic, and Streamlit. Keep application pages behind the service/query layer in `src/sponsor_intel/services/`.
- Implement one approved specification phase at a time and do not broaden scope without explicit approval.

## Evidence and data invariants

1. Use only authoritative source domains for production ingestion.
2. Preserve raw names, immutable source artifacts, and source provenance.
3. Never turn missing evidence into a negative conclusion; use `UNKNOWN` unless an authoritative source explicitly establishes otherwise.
4. Keep legal entities and parent organizations separate.
5. Route ambiguous entity matches to review rather than guessing.
6. Do not present E-Verify, an LCA, historical PERM, or institution type as a sponsorship promise.
7. Do not add job tracking, a public API, Postgres, or Supabase in V1.

## Development conventions

- Match the existing `src/` package layout and keep Streamlit free of raw SQL.
- Add tests for every parser, rule, override, and fixed defect.
- Use fixtures and mocks for extraction tests; do not call the OpenAI API in ordinary tests.
- Stop and report when an official source schema changes unexpectedly.
- Summarize schema changes and data-quality effects in every pull request.

## Commands

- Setup: `uv sync --frozen`
- Test: `uv run pytest`
- Format check: `uv run ruff format --check .`
- Lint: `uv run ruff check .`
- Type-check: `uv run pyright`
- Streamlit smoke test: `uv run python scripts/smoke_streamlit.py`
- CLI: `uv run sponsor-intel --help`
- App: `uv run sponsor-intel app`

## Configuration and safety

- Supported environment variables are documented in `.env.example`.
- Never commit `.env`, raw data, generated databases, API keys, or other secrets.
- Never log secret values or full request headers.
- Run Ruff, Pyright, and pytest before finishing a change.
