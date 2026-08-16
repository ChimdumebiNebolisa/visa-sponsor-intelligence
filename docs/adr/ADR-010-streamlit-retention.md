# ADR-010: Retain Streamlit with a review date

> **Historical decision record (2026-08-15):** Measurements and Phase 10/V2 terminology below
> describe the build evaluated when this ADR was accepted. They are not the active product or
> rating contract. Product A remains on Streamlit under the same no-rewrite decision, but its
> active behavior is defined by `PRODUCT_A_SPEC.md`, `product_a_metrics_v1`, and
> `product_a_scores_v1`. Policy/readiness scores and letter grades mentioned below are superseded.
> Repository privacy, release publication, and hosted validation remain owner actions.

- Status: Accepted with validation gap
- Decision: `KEEP_STREAMLIT_WITH_REVIEW_DATE`
- Date: 2026-08-15
- Review date: 2026-09-30
- Owners: repository owner and Sponsorship Intelligence maintainer

## Context

The explorer is a read-only, local-first research tool intended for private access by one owner or
a very small invited audience. It needs evidence-heavy tables, filters, organization drilldowns,
comparisons, exports, and clear uncertainty—not public SEO, user-generated writes, complex
accounts, or high concurrency. Phase 10 explicitly prohibits a framework rewrite without measured
evidence and owner authorization.

The repository was still public and no Streamlit Community Cloud URL had been deployed or
privacy-tested when this ADR was written. Hosted authentication, deployed memory, cold start,
reliability, and basic mobile usability are therefore validation gaps, not assumed successes.

## Evidence

The reproducible pre-change baseline on source release `data-2026-08-15` measured:

- 252.01 MiB presentation DuckDB;
- 1.792 s local Streamlit AppTest cold Home execution and 85.51 ms warm rerun;
- 332.62 ms employer search;
- 40.06 ms institution search;
- 490.87 ms organization detail;
- 39.04 ms comparison;
- 2.317 s / 147,938,133 bytes for the full employer CSV export;
- 122.88 ms / 2,600,285 bytes for the full institution CSV export.

The current quality-approved local V2 database is 276,312,064 bytes, and the minimum four-asset
prospective hosted transfer is 276,319,023 bytes. Current local query,
Streamlit, export, process-memory, and dependency-install measurements are recorded in
`outputs/reports/phase10/performance.json` and `performance.md`. Those reports label local and
deployed evidence separately. The V2 UAT runner exercises the real service and database, not an
empty fallback.

The final recorded V2 run measured 3.128 s local AppTest cold execution, 640.76 ms warm rerun,
370.56 ms employer search, 192.91 ms institution ranking, 705.42 ms detail, 57.10 ms comparison,
101.02 ms for a 3,851,703-byte filtered employer CSV, and 182.37 ms for a 163,667-byte filtered
institution CSV. The acceptance process peaked at 834,842,624 bytes working set; this includes the
runner, repeated queries, exports, hashing, and AppTest in one process and is not represented as
deployed steady-state memory. A separate isolated Windows 11/Python 3.12 dependency check occupied
593,736,400 bytes (566.23 MiB); its install duration was not reliably recorded, so no duration is
inferred.

Observed product capabilities:

- decision-first employer and institution ordering works over the real database;
- organization detail, parent/legal-entity drilldown, comparison, filtered CSV exports, official
  policy links/excerpts, raw counts, and partial-period warnings are supported;
- deep links use an organization ID query parameter;
- null evidence remains `UNKNOWN`, and high R&D does not override stronger immigration readiness;
- read-only DuckDB keeps presentation code behind the service layer;
- runtime bootstrap is restricted to four checksum-verified, quality-approved release assets and
  fails closed rather than serving an empty app;
- a lean app dependency path excludes ingestion, browser automation, OpenAI, and PDF tooling.

Known limitations and gaps:

- Community Cloud private access cannot be verified while the GitHub repository is public;
- the local V2 build must be published as a quality-approved release after privacy is established;
- deployed cold start, peak memory, outage recovery, and resource headroom are unmeasured;
- Streamlit's multipage navigation and query-parameter deep links are adequate locally but have not
  yet been observed in the hosted environment;
- dense tables need a basic desktop/mobile human usability check;
- policy and entity review remain offline operator workflows rather than in-app writes, which is
  intentional for V1/V2 safety;
- expected use is one owner or a very small invited group; no high-concurrency claim is made.

## Decision

Retain Streamlit and complete the owner-only private-deployment validation. There is no measured
hard blocker justifying a Next.js, React, FastAPI, Postgres, Supabase, or other migration in this
change. Local warm interactions, evidence drilldowns, comparisons, and exports support the intended
small-user, read-only workflow, while the repository's service boundary keeps a later UI change
possible without replacing evidence semantics.

This is not an unconditional platform endorsement. `KEEP_STREAMLIT_WITH_REVIEW_DATE` is used
because the decision rule requires private deployment evidence that does not yet exist.

## Review gates for 2026-09-30

Re-evaluate this ADR after at least 14 days of owner use, or on the review date, with:

1. repository privacy and Streamlit **Only specific people** access verified for owner, invited,
   signed-out, and non-invited cases;
2. at least three cold restarts and five warm sessions measured on the published V2 release;
3. deployed peak memory and startup reliability recorded, including one valid-cache outage test;
4. desktop and basic mobile checks of navigation, filters, tables, deep links, evidence excerpts,
   comparison, and exports;
5. actual expected-user count and maintenance/cost observations documented.

Consider a separate migration proposal only if those measurements demonstrate a hard blocker:
private access cannot be secured, the real database is unreliable within resource limits, required
interactions cannot be implemented, expected concurrency changes materially, or persistent state
and navigation problems obstruct the primary workflow. A future public multi-user product, complex
writes/accounts, or SEO requirement would also trigger a new architecture decision; none is
currently in scope.

## Consequences

- No framework rewrite occurs in Phase 10.
- Community Cloud remains the first deployment target.
- The owner must complete privacy, release publication, deployment, and hosted validation before
  anyone describes the application as private or deployed.
- The service/query layer and read-only database remain the portability boundary.
- Unmeasured hosted evidence stays visible in the performance and UAT reports until replaced by
  observed values.
