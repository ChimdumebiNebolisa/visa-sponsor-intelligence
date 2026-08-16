# Codex instructions

## Purpose and stack

- This repository builds the private, local-first Product A historical sponsorship explorer.
- Read `PRODUCT_A_SPEC.md` before changing architecture, evidence semantics, or product behavior.
- `SPEC.md` is retained historical Product B context and is superseded for active behavior.
- Use Python 3.12, `uv`, Typer, Pydantic, and Streamlit. Keep application pages behind the service/query layer in `src/sponsor_intel/services/`.
- Product A is the approved active scope; do not reintroduce a policy-dependent product phase.

## Evidence and data invariants

1. Use only authoritative source domains for production ingestion.
2. Preserve raw names, immutable source artifacts, and source provenance.
3. Never turn missing evidence into a negative conclusion; use `UNKNOWN` unless an authoritative source explicitly establishes otherwise.
4. Keep legal entities and parent organizations separate.
5. Route ambiguous entity matches to review rather than guessing.
6. Do not present E-Verify, an LCA, historical PERM, or institution type as a sponsorship promise.
7. E-Verify, OPT, IPEDS, HERD, cap-exemption context, and policy evidence never alter sponsorship ratings.
8. Only H-1B LCA rows may affect H-1B ratings; keep H-1B1 and E-3 queryable but unscored.
9. A validated resolved zero is distinct from missing evidence: show no observed history versus `Unrated`.
10. Policy evidence is supplemental, incomplete, non-blocking, and requires no OpenAI key for normal builds or releases.
11. Do not add job tracking, a public API, Postgres, or Supabase in V1.

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
- Discover DOL sources: `uv run sponsor-intel sources discover --source dol_lca --from-fy 2022`
- Ingest DOL sources: `uv run sponsor-intel ingest --source dol_lca --from-fy 2022`
- Live DOL contracts: `SPONSOR_INTEL_RUN_NETWORK_TESTS=1 uv run pytest tests/contracts`

## Phase 1 source invariants

- DOL quarterly disclosures are cumulative; select only the latest published quarter per fiscal year.
- Preserve both FY2024 PERM form variants as separate artifacts.
- Record raw-download provenance before normalization so interrupted builds resume safely.
- Treat exact duplicate rows and repeated decision dates only through the tested deterministic rules in the normalizer; conflicting duplicate case IDs must fail.
- Keep current partial fiscal years explicitly labeled and never compare them with complete years without a warning.
- A source-schema fingerprint change is a visible drift warning; a missing required logical column fails closed.

## Configuration and safety

- Supported environment variables are documented in `.env.example`.
- Never commit `.env`, raw data, generated databases, API keys, or other secrets.
- Never log secret values or full request headers.
- Run Ruff, Pyright, and pytest before finishing a change.
