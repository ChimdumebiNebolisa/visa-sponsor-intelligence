# Phase 10 user-acceptance protocol

## Scope

This protocol validates the primary workflow against the restored real source release and the
current quality-approved local V2 build:

> Discover and compare employers and research institutions with the strongest historical
> technical H-1B, PERM, institutional-policy, and research evidence.

It does not use the empty-database fallback. It distinguishes observable product failures from
human policy-review work and owner-only privacy/deployment actions.

## Run

From the repository root, after metrics, quality, and DuckDB have been rebuilt:

```powershell
py -m uv run python scripts/run_v2_acceptance.py `
  --source-release-tag data-2026-08-15
```

The runner writes:

- `outputs/reports/phase10/uat-results.json`
- `outputs/reports/phase10/uat-results.md`
- `outputs/reports/phase10/performance.json`
- `outputs/reports/phase10/performance.md`

Use `--skip-streamlit` only for a diagnostic rerun. Use `--reset-history` only when intentionally
starting a new audit history; ordinary reruns retain materially changed task attempts so a failed
observation remains visible after a fix.

After an owner deployment, supply the live URL and only set the verification flag after testing an
authorized owner, an invited user, a signed-out browser, and a non-invited account:

```powershell
py -m uv run python scripts/run_v2_acceptance.py `
  --source-release-tag data-2026-08-15 `
  --live-url https://REDACTED.streamlit.app `
  --private-access-verified `
  --deployed-runtime-memory-bytes 123456789
```

## Status meanings

- `PASS`: the real database/service produced evidence satisfying the task.
- `FAIL`: an observable implementation or data-integration defect remains.
- `BLOCKED_HUMAN_REVIEW`: the query path works, but the required reviewed policy evidence does not
  yet exist. This is not a pass.
- `BLOCKED_OWNER_ACTION`: repository or Streamlit ownership/privacy UI is required. This is not a
  pass.
- `NOT_RUN`: a prerequisite such as the V2 schema was unavailable, so the runner made no inference.

The process exits nonzero for failed/not-run code-testable checks or UAT tasks. Human-review and
owner-action blockers remain explicit without being mislabeled as software test failures.

## Deterministic representative selection

The runner does not hand-pick only convenient organizations. It selects:

- the first institution under the default decision-first ranking;
- the strongest non-institution national-laboratory/research-laboratory name satisfying the
  evidence rule;
- the strongest complete-history, non-institution legal name with a corporate suffix;
- a parent organization with multiple linked legal entities;
- the first real audit conflict held for review;
- the highest research-activity institution satisfying the weak/unknown green-card rule.

The selected IDs and raw evidence are persisted in JSON.

## Required tasks

The automated-real-data portion executes all 18 requested tasks:

1. complete core-policy review;
2. research-staff permanent-residence eligibility;
3. PERM support;
4. EB-1B support;
5. strong H-1B history with incomplete policy;
6. high research spending with weak/unknown green-card evidence;
7. strong private-company H-1B and PERM histories;
8. university/laboratory/private-company comparison;
9. parent and legal-entity drilldown;
10. ambiguous-entity review routing;
11. E-Verify-independent sponsorship ranking;
12. `UNKNOWN` preservation;
13. visible partial FY2026 labeling;
14. filtered institution export;
15. filtered employer export;
16. official policy URL and exact excerpt;
17. score explanation and raw-count reconciliation;
18. decision-first ordering over high R&D alone.

## Manual browser supplement

After a private deployment, the owner must also verify items the local service runner cannot prove:

- cold reboot and verified-cache recovery during a temporary GitHub outage;
- invited versus non-invited access and signed-out denial;
- sidebar release/build/fiscal labels on each page;
- usable navigation and deep links in a fresh browser session;
- filters, tables, download controls, and evidence excerpts at desktop and basic mobile widths;
- absence of tokens, authorization headers, detailed traces, ingestion, OpenAI, or Playwright work
  in hosted logs;
- deployed peak memory and platform stability.

Record these measurements by rerunning the script with its deployment arguments. Do not edit a
blocked result into a pass by hand.

## Current interpretation

The machine-readable report is authoritative. The initial V2 run discovered that the entity-review
view still filtered legacy status values and hid 21,117 reviewable aliases. The view was fixed,
DuckDB was rebuilt, and task 10 passed on rerun; both the failed and passing observations remain in
its `attempts` array. The other code-testable tasks passed. Tasks 1 and 2 remain human-review blocked
because no institution yet has all four core questions reviewed and no reviewed research-staff
permanent-residence `YES` exists.
