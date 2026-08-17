# Product A complete execution plan

**Plan status:** `COMPLETE`
**Branch:** `codex/phase-10-decision-readiness-deployment`  
**Target:** Update draft PR #10 in place  
**Created:** 2026-08-16  
**Active product:** Historical Sponsorship Intelligence (Product A only)

## Goal

Complete a local-first Streamlit product that ranks and explains U.S. employers, universities,
and research institutions using observed technical H-1B and PERM history from FY2022 onward,
with USCIS corroboration and separate supplemental E-Verify, OPT, IPEDS, and HERD context.
Historical policy extraction remains preserved but cannot affect ratings, default rankings,
quality gates, releases, deployment readiness, or ordinary build credentials.

## Assumptions that affect implementation

- The existing clean Phase 10 branch is the only implementation branch; no new product branch or
  architecture phase will be created.
- Existing cached official raw artifacts are reusable when URL, checksum, period, and schema
  validation agree. Real-file validation changed the LCA acquisition scope: in addition to cached
  Q4 and FY2026-Q3 files, eleven selected official FY2022–FY2025 coverage files are required.
  FY2023 Q1 was downloaded during fail-closed source-shape investigation but is not selected because
  the cumulative Q2 file already covers it.
- Existing V1/V2 policy-era score outputs may remain as explicitly historical sidecars, but the
  active processed metrics, presentation views, service methods, UI, quality report, acceptance
  runner, and release metadata use the Product A score version.
- A source-valid resolved zero is rated as no observed history. A missing/invalid source or
  unresolved identity is `Unrated`; it is never converted into one star.
- Policy evidence remains queryable only as supplemental, incomplete evidence and is not required
  to complete this plan.

## Pre-Product-A baseline assessment

The following assessment records the repository state found before Product A implementation. It is
retained as execution history, not as a description of current active behavior. Resolution and
remaining verification state are recorded in the work breakdown and completion record below.

### Implemented and reusable

- `configs/sources.yaml` is the single typed official-source registry.
- DOL discovery records every official candidate and preserves both FY2024 PERM form variants. The
  execution patch now distinguishes reviewed historical LCA coverage segments from current
  cumulative snapshots and resolves only the active discovery selection downstream.
- Immutable, content-addressed raw downloads, pre-normalization raw manifests, SHA-256 provenance,
  schema fingerprints, fail-closed required columns, deterministic duplicate handling, and cached
  artifact reuse are implemented under `src/sponsor_intel/sources/`.
- Official adapters exist for DOL LCA, DOL PERM, USCIS H-1B, IPEDS, HERD full and short forms,
  E-Verify targeted lookup, and ICE/SEVP positive-only OPT evidence.
- Conservative entity resolution preserves raw names, legal entities, parent mappings, legal
  employer locations, review queues, and reviewed overrides.
- Deterministic SOC/title role classification and a reviewed gold set exist.
- Polars processed tables, DuckDB presentation views, a parameterized service layer, Streamlit
  pages, exports, release bundling, deployment bootstrap, and quality reporting already exist.
- The local ignored data build is non-empty: 225,996 employer rows, 227,863 legal entities, 431
  parents, 5,985 institutions, and 1,239,005 DOL records in the Phase 10 baseline.
- Baseline verification is green: Ruff format, Ruff lint, Pyright, 147 pytest tests (7 skipped),
  and six available live official-source contract tests pass. The charged OpenAI contract is
  intentionally skipped and is not required by Product A.

### Product B behavior identified for disabling

These were baseline Product B paths. Product A implementation has removed them from ratings,
default ordering, quality/release gates, ordinary credentials, and scheduled workflows; retained
policy extraction is manual supplemental maintenance only.

- `configs/scoring_v2.yaml` and `src/sponsor_intel/scoring/engine.py` actively constructed policy
  support, research-pathway scores, policy blockers, and decision-readiness tiers.
- `src/sponsor_intel/quality/report.py` made reviewed-policy coverage and policy evidence critical
  publication gates and fingerprinted policy outputs into the active build ID.
- `src/sponsor_intel/database/builder.py` changed institution readiness based on policy and the
  quality gate.
- `src/sponsor_intel/services/explorer.py`, `app/Home.py`, Research Institutions, Organization
  Detail, Compare, Evidence Review, and Data Health exposed policy-dependent default behavior.
- `.github/workflows/refresh_policies.yml` was scheduled and required OpenAI credentials.
- README, operations, UAT, scoring, data dictionary, release text, and PR #10 described the
  policy-dependent product as active at baseline.

Retained policy code and data are now labeled `Supplemental`, `Incomplete`, and
`Not used in sponsorship ratings`. The manual policy workflow remains available, but no scheduled
or ordinary Product A path invokes it.

### Confirmed baseline defects and incomplete behavior

These statements describe the pre-change build. Their implementation fixes are complete; the final
global-supersession real rebuild and full verification remain tracked below.

- Local DOL LCA/PERM data selected FY2026 Q2; live official discovery on 2026-08-16 selected FY2026
  Q3. USCIS had already selected FY2026 Q3.
- The DOL landing page describes cumulative/fiscal-year disclosure data, and each LCA record layout
  describes an October–September reporting period, but the linked archive files are inconsistent:
  FY2022 and FY2024–FY2025 have four exact-quarter files, while FY2023 uses cumulative Q1–Q2 plus
  exact Q3 and Q4. The official archive provides no separate annual LCA file. Completed historical
  coverage therefore uses only those reviewed, observed-quarter-validated segments. Across all
  selected fiscal years, a stable `CERTIFIED` case later published as `CERTIFIED-WITHDRAWN` is
  collapsed to its latest state; arbitrary conflicts fail. Current FY2026 continues to use one
  latest cumulative snapshot.
- Active LCA metrics counted every technical LCA without restricting `visa_class = H-1B` or positive
  statuses. H-1B1, E-3, denied, rejected, and withdrawn rows could affect the H-1B score.
- `CERTIFIED-WITHDRAWN` was not separated/half-weighted.
- PERM used `starts_with("CERTIFIED")`; `CERTIFIED-EXPIRED` therefore received full rather than
  half weight.
- Active-year consistency included the partial fiscal year.
- Fixed count caps and letter grades were used instead of deterministic build-level p95 `log1p`
  caps and whole stars.
- H-1B and PERM formulas differed from Product A, and a no-record resolved employer was treated
  as missing rather than a resolved zero.
- Processed source reads omitted several already-present fields needed for legal address, worksite,
  wages, visa class, form version, and rating explanations.
- Case `organization_id` preferred the parent, so parented petitioner evidence was not
  attached to the legal entity in presentation data. The explorer exposed a parent rollup but not
  a separately rated parented legal entity.
- The institution default was research-pathway/policy readiness rather than observed
  sponsorship history.
- The role gold rule expected a computing assistant professor to be technical, contrary to
  Product A's faculty exclusion; postdoctoral exclusions were also not uniformly strong.
- Data Health did not expose the complete selected-artifact URL/checksum/schema record.
- Streamlit smoke used a tiny Phase 10 fixture and did not prove that the real database was nonzero.
- Product A specification, acceptance runner, and required Product A reports did not exist.

### Existing source and generated-data state

| Source | Cached selection | State before execution |
|---|---|---|
| DOL LCA | FY2022-Q4 through FY2025-Q4; FY2026-Q2 partial | Initial cache is incomplete because Q4 is quarter-only; live discovery selects exact FY2022/FY2024/FY2025 quarters, FY2023 cumulative Q1–Q2 plus exact Q3/Q4, and FY2026-Q3 |
| DOL PERM | FY2022-Q4, FY2023-Q4, both FY2024-Q4 forms, FY2025-Q4, FY2026-Q2 partial | Valid cached artifacts; live discovery now selects FY2026-Q3 |
| USCIS H-1B | FY2022-FY2025 complete; FY2026-Q3 partial | Current official selection cached; duplicate manifest identity must not duplicate processed rows |
| IPEDS | HD2025 finalized directory plus dictionary | 5,985 institutions; IC characteristics are not yet represented separately |
| HERD | Full and short files for 2022, 2023, 2024 | 2,736 institution-year rows before IPEDS reconciliation; overlap fails closed |
| E-Verify | Bounded cached lookups | Supplemental only; no bulk refresh planned |
| ICE/SEVP | Official 2024 Top 200 PDF | 589 positive observations; absence remains unknown |

Ignored local outputs include a 276 MB DuckDB and a valid Phase 10 release bundle. The only remote
release is `data-2026-08-15`, which is a V1 release targeted at `main`. The GitHub repository is
currently public, so no new data release will be published.

### Files expected to change

- Product authority and plans: `PRODUCT_A_SPEC.md`, `SPEC.md`, `AGENTS.md`, this plan.
- Source contract/config and selection tests: `configs/sources.yaml`, source discovery/normalization
  modules only where a confirmed defect requires it, and source tests.
- Active scoring: a Product A scoring config and model/engine, metrics pipeline, quality report,
  database builder, service layer, Streamlit components/pages.
- Active workflows/docs: government and policy workflows, Makefile, README, scoring, source,
  operations, data dictionary, UAT/user workflow, deployment/release language.
- Verification: focused unit/integration/Streamlit tests, real-data smoke support,
  `scripts/run_product_a_acceptance.py`, and the nine required Product A reports.

### Files to preserve unless a direct defect requires change

- Raw artifacts, source caches, generated DuckDB/Parquet data, and secrets remain ignored.
- Policy discovery/fetch/extraction/review implementation and reviewed policy records remain
  historically preserved and are not rewritten.
- Release bootstrap security, authorization forwarding protections, and public-repository release
  guards remain intact.
- Conservative entity matching thresholds and existing reviewed parent/legal overrides remain
  unchanged except for evidence-backed corrections found during validation.
- E-Verify and OPT lookup/parsing rules remain unchanged except for user-facing labels.

## Final architecture

```text
Official source discovery
→ immutable raw downloads
→ schema validation
→ normalized Parquet
→ technical-role classification
→ legal-entity resolution
→ employer and institution aggregation
→ hidden deterministic Product A scores
→ 1–5 star ratings
→ DuckDB presentation views
→ Streamlit explorer
→ validation and release bundle
```

The record identity path is always:

```text
raw source employer observation
→ petitioning legal entity (evidence remains attached here)
→ reviewed/deterministic parent rollup (separate presentation row)
```

The active build never depends on policy outputs or an OpenAI API key. HERD creates a separate
Research Scale only. E-Verify, OPT, IPEDS context, HERD, potential cap exemption, and policy cannot
change H-1B, green-card, or Overall Sponsorship stars.

## Source acquisition matrix

| Source | Official landing page | Exact datasets and years | Selection and cumulative rule | Expected schema / normalized output | Limitations and freshness | Required tests |
|---|---|---|---|---|---|---|
| DOL LCA | `https://www.dol.gov/agencies/eta/foreign-labor/performance` | LCA disclosure plus record layout; exact FY2022/FY2024/FY2025 quarters, FY2023 cumulative Q1–Q2 plus exact Q3/Q4, and latest FY2026 cumulative snapshot; dynamic later years | Prefer one annual artifact; absent an annual file, completed years require an explicitly reviewed segment contract covering Q1–Q4 exactly once; observed quarters must match declared bounds; only a stable chronological certified-to-certified-withdrawn supersession is collapsed | Required case/status/employer/title/SOC/worksite; normalize visa class, legal address, dates, NAICS, workers, wages, prevailing wage, segment flag/bounds, artifact URL/checksum metadata into staged/resolved LCA Parquet | DOL documentation describes cumulative/full-year coverage but archived file shapes vary by year; immutable staged updates can repeat a case; a certified LCA is not a petition approval; current period partial | annual preference, complete segment selection, observed-quarter equality, tested supersession collapse/arbitrary-conflict failure, current latest-quarter selection, schema/checksum/manifests, H-1B-only/status weighting |
| DOL PERM | Same DOL page | PERM disclosure plus layouts; FY2022 onward, including both FY2024 ETA-9089 variants | Same one-snapshot rule; keep every form variant for the selected period; normalize then union; stable case identity and conflicting duplicates fail | Normalize case/status/dates/legal address/title/SOC/NAICS/wages/worksite/education/major/experience/form version/provenance | Certification is labor certification, not a green card; current period partial | multiple form variants, no quarter duplication, form version, schema drift, status weighting, conflicting duplicate failure |
| USCIS H-1B Employer Data Hub | `https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub` and official archive | Official CSV preferred, FY2022-FY2025 and FY2026-Q3 current, dynamic later years | One fiscal-year selection; registry period fallback only when the landing page blocks automation | Employer name/legal location, initial/continuing approvals and denials, partial flag, URL/checksum/retrieval metadata | Employer-level only; never title-specific or called technical approvals | official CSV contract, period fallback warning, no duplicate processed selection, exact UI label |
| IPEDS | `https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx?year=-1` | Latest finalized HD directory and IC characteristics with dictionaries/value labels | Finalized data is primary; provisional data must be separately labeled and cannot silently replace finalized identity | UNITID, identity/aliases/location/site/control/sector/offering/category/status/system/year/finalization | Establishes higher-education identity, not cap exemption | latest finalized selection, HD+IC reconciliation, provisional preservation, UNITID contract |
| NSF/NCSES HERD | `https://ncses.nsf.gov/explore-data/microdata/higher-education-research-development` | Full and short public-use files for 2022, 2023, 2024, dynamic later years | Select both variants per survey year; one institution-year after reconciliation; retain both form labels | HERD/NCSES/IPEDS IDs, name/location/year/form, R&D totals/federal/computing/engineering/personnel/imputation where available | Short forms have limited fields; latest year drives Research Scale; immigration stars unaffected | full+short discovery, institution-year dedup/overlap failure, exact UNITID join, Research Scale independence |
| E-Verify | `https://www.e-verify.gov/e-verify-employer-search` | Existing cache and explicitly bounded targeted lookups only | No full-universe lookup; preserve search/match/status/date/location/confidence/ambiguity | Supplemental observation and review tables | Enrollment is not sponsorship; no-match is unknown | bounded lookup/cache, ambiguity, status labels, score independence |
| ICE/SEVP OPT | `https://www.ice.gov/sevis/whats-new` | Existing FY2022-onward official reports; current cached 2024 Top 200 | Positive observations only; no complete-universe inference | Employer/program/year/count/rank/source/provenance | Absence is `UNKNOWN` | positive-only parser, no negative inference, score independence |

## Implementation work breakdown

Status values are `PENDING`, `IN_PROGRESS`, `COMPLETE`, and `BLOCKED`.

1. `COMPLETE` — Inspect specifications, repository, configs, adapters, data layers, tests,
   workflows, generated data, Git history, release state, and PR #10.  
   Verify: baseline checks and official discovery results recorded above.
2. `COMPLETE` — Establish Product A authority and durable execution memory.  
   Work: save this plan, create `PRODUCT_A_SPEC.md`, supersession notice in `SPEC.md`, concise
   Product A invariants in `AGENTS.md`, and update the plan status.  
   Verify: documentation contains scope, prohibitions, star semantics, and acceptance mapping.
3. `COMPLETE` — Correct source contracts and refresh selection.
   Work: patch only confirmed registry/discovery/normalization gaps, ingest the official files
   required by the reviewed FY2022–FY2025 LCA segment contracts plus official FY2026 Q3, preserve
   valid cached artifacts and all FY2024 PERM variants, and materialize selected artifact metadata.
   Verify: targeted source unit/integration tests, selected artifact matrix, and checksum/schema
   reports are complete; the final live-contract rerun remains in step 9;
   one latest cumulative current period and one annual or reviewed non-overlapping completed LCA
   segment set per source/year/variant.
4. `COMPLETE` — Correct classified evidence and identity scope.
   Work: enforce strong faculty/postdoc exclusions, preserve legal addresses versus worksites,
   attach cases to legal entities, create separate reviewed parent rollups, and keep ambiguity in
   review.  
   Verify: role/entity regression tests and representative legal/parent validations.
5. `COMPLETE` — Implement Product A aggregation, scoring, and stars.
   Work: status/visa-class weights, complete-year consistency, recency, family breadth, p95 log
   caps, USCIS 5% corroboration, 40/60 Overall score, zero-vs-Unrated, accessible star labels, and
   independent Research Scale.  
   Verify: deterministic scoring/unit tests, independence tests, real score distributions.
6. `COMPLETE` — Rebuild DuckDB/service/UI for Product A.
   Work: sponsorship-first sorts and filters, stars in primary tables, detail explanations,
   compare, exports, source provenance, partial warnings, and supplemental policy labeling.  
   Verify: integration/Streamlit tests, no raw SQL in pages, real nonzero database smoke.
7. `COMPLETE` — Remove Product B blockers from normal operations.
   Work: policy-independent quality/release/build ID, manual-only policy workflow, no OpenAI key in
   ordinary builds/releases, and Product A release metadata/workflow assertions.  
   Verify: clean policy-absent fixture passes Product A gates and release packaging.
8. `COMPLETE` — Build real-data reports and validate named organizations.
   Work: generate source selection, score distribution, organization validation, unresolved
   entity, and acceptance reports under `outputs/reports/product-a/`.  
   Verify: report schema/tests plus direct case/title/address/provenance checks for every required
   company and institution.
9. `COMPLETE` — Run full verification and real acceptance.
   Work: frozen sync, Ruff, Pyright, pytest, live contracts, real pipeline, quality, DuckDB,
   real-data Streamlit smoke, clean database restoration, Product A acceptance.  
   Verify: captured command results and zero critical Product A failures.
10. `COMPLETE` — Commit, push, and update PR #10.
    Work: logical commits, branch push, rewrite title/body with schema/data-quality effects and
    exact verification evidence; do not merge or publish a release.  
    Verify: clean status, remote SHA equals local SHA, PR #10 remains open draft with Product A
    description and current checks.

## Risk register

| Risk | Impact | Mitigation / verification |
|---|---|---|
| DOL documentation/file-shape mismatch or repeated case state | Missing or inflated counts and stars | Annual preference; otherwise explicit reviewed coverage segments with persisted bounds and exact observed quarters; collapse only stable chronological certified-to-certified-withdrawn supersession and fail every arbitrary conflict; one latest current cumulative snapshot |
| Source-page HTML changes | Missing or wrong artifacts | Official-domain discovery, recorded candidates, fail closed when layout is missing, live contracts |
| Schema drift | Silent corruption | Required logical columns fail; fingerprints/warnings visible; Q3 normalization reviewed before acceptance |
| Multiple PERM form versions | Lost or duplicated FY2024 records | Preserve both selected variants, record form version, normalize independently, conflict tests |
| USCIS mistaken for technical-role data | Misleading approval claim | Exact employer-level label, 5% corroboration ceiling, DOL remains role-level source |
| Legal entity/parent confusion | Wrong employer rating | Legal scope is primary; separate parent rollup rows; identity scope in UI/report |
| Legal address/worksite confusion | False merge | Resolution continues using employer legal address; worksite appears only as activity context |
| Role false positives | Inflated technical history | Overrides first; strong faculty/postdoc/intern/medical/sales/support exclusions before SOC inclusion; gold/regression tests |
| Missing evidence converted to zero | False negative | Explicit source-valid and identity-valid gates; `Unrated` separate from a resolved zero |
| Partial-FY inflation | Misleading consistency | Partial FY can affect recency only; never denominator/numerator of complete-year consistency; warnings everywhere |
| Large data/memory requirements | Failed local rebuild | Reuse validated cache, fetch only the missing selected historical LCA segments, use atomic outputs, run stages separately, and report exact resource blockers |
| Streamlit wrong/empty database | False completion | Real path forced in final smoke; positive employer/institution counts checked before health endpoint |
| Policy code blocks Product A | Credential/build failure | Remove policy from active score, gate, release, build ID, defaults; manual workflow only; policy-absent test |
| Public repository data publication | Exposure | Preserve publication guard; push code/PR only; do not publish or overwrite release assets |

## Acceptance matrix

| # | Final requirement | Implementation location | Automated test | Real-data validation / report evidence |
|---:|---|---|---|---|
| 1 | Complete plan written and executed | This file | acceptance plan-status check | `acceptance.md/json` |
| 2 | Product A authoritative | `PRODUCT_A_SPEC.md`, `SPEC.md`, `AGENTS.md` | documentation assertion | acceptance scope section |
| 3 | Product B policy inactive/non-blocking | scoring, quality, services, UI, workflows | policy-absent build and score-independence tests | quality/acceptance checks |
| 4 | No OpenAI key required | CLI/workflows/config/release | clean-env metrics/quality/release test | acceptance command evidence |
| 5 | Valid adapters reused | `src/sponsor_intel/sources/` | existing adapter/contract suite | source-selection report |
| 6 | Confirmed adapter defects repaired | registry/discovery/normalizers | targeted regressions | source-selection discrepancies |
| 7 | FY2022+ LCA reproducibly selected | DOL discovery/pipeline | selection/manifest tests | source-selection rows/checksums |
| 8 | FY2022+ PERM reproducibly selected | DOL discovery/pipeline | selection/variant tests | source-selection rows/checksums |
| 9 | Completed years use one annual artifact or the verified complete LCA partition set | DOL selection | annual preference, incomplete-set, date-bound, and overlap tests | selected-artifact partition provenance and uniqueness |
| 10 | Current FY uses latest cumulative snapshot | DOL selection | latest-quarter test | FY2026-Q3 evidence |
| 11 | No cumulative-quarter duplication | ingestion/acceptance | duplicate selection and case-key tests | acceptance counts |
| 12 | H-1B1/E-3 excluded from stars | metrics/scoring | visa-class regression | validation counts |
| 13 | USCIS label is employer-level | services/UI/docs | UI/service label test | validation report |
| 14 | Latest finalized IPEDS identity present | IPEDS/config/institution tables | finalized/provisional test | source-selection row |
| 15 | HERD full/short FY2022+ correct | HERD/institution tables | pair and institution-year tests | source-selection and research examples |
| 16 | Technical classification deterministic/tested | taxonomy/classifier | role unit/gold tests | classification corrections |
| 17 | Legal entities distinct from parents | resolution/metrics/views | identity/rollup tests | Amazon legal + parent validation |
| 18 | H-1B History 1–5 stars | Product A scoring/UI | formula/star/accessibility tests | score distribution |
| 19 | Green Card History 1–5 stars | Product A scoring/UI | formula/star/accessibility tests | score distribution |
| 20 | Overall Sponsorship 1–5 stars | Product A scoring/UI | 40/60 and gating tests | score distribution |
| 21 | Research Scale separate 1–5 stars | Product A scoring/UI | percentile and independence tests | institution validation |
| 22 | No observed distinct from Unrated | scoring/service/UI | resolved-zero/source-missing tests | distribution/status counts |
| 23 | Supplemental evidence cannot alter stars | scoring | E-Verify/OPT/HERD/IPEDS/policy invariance tests | before/after validation assertions |
| 24 | Universities sorted by sponsorship | services/UI | default-order integration test | top institution report |
| 25 | Ratings expose evidence/explanation | metrics/service/detail UI | explanation test | named-organization validation |
| 26 | Partial period visibly identified | services/all UI pages | Streamlit warning tests | source/validation reports |
| 27 | Real-data validation complete | acceptance runner | report-schema test | `validation.md/csv` |
| 28 | Real-data acceptance passes | acceptance runner | self-checks | `acceptance.md/json` |
| 29 | Ruff passes | repository | required command | acceptance verification table |
| 30 | Pyright passes | repository | required command | acceptance verification table |
| 31 | Pytest passes | tests | required command | acceptance verification table |
| 32 | Network contracts pass/outage exact | `tests/contracts` | opt-in network suite | acceptance/source discrepancy |
| 33 | Quality gates pass | Product A quality reporter | quality fixture/regression | real quality JSON |
| 34 | DuckDB build passes | database builder | integration build test | real DB counts/views |
| 35 | Streamlit loads real nonzero DB | smoke script | fixture and real-path modes | real smoke command/counts |
| 36 | PR #10 accurately describes Product A | GitHub PR #10 | `gh pr view` inspection | final SHA/title/body/status |

## Completion record

Product A authority, source contracts, entity/role handling, scoring, DuckDB, services, UI, policy
decoupling, quality controls, and the exact nine-report acceptance family are complete. The final
real build is `product-a-f234013f5f00ac6c`: 36 selected official artifacts, 272,320 employer rows,
6,163 institutions, zero critical quality failures, and 28/28 independent real-data acceptance
checks passed at `2026-08-16T23:26:54.121178+00:00`. The final frozen checks are: 94 locked
packages; Ruff format and lint passed; Pyright reported zero errors and warnings; pytest reported
248 passed and 7 skipped at 82% coverage; six live official-source contracts passed and the
separately charged OpenAI policy contract remained opt-in/skipped; the real Streamlit smoke loaded
272,320 employers and 6,163 institutions. The local release bundle was rebuilt and checksum-verified
without publication; inactive V1/V2 score sidecars are excluded.

The principal source-plan deviation is evidence-driven. DOL describes quarterly disclosure data as
cumulative, but the selected official historical LCA workbooks did not consistently contain complete
fiscal-year rows. The implementation therefore fails closed to annual artifacts when available or to
the reviewed, date-bounded FY2022–FY2025 segment maps documented above. It also collapses 71,151
stable global `CERTIFIED` to `CERTIFIED-WITHDRAWN` case updates to their latest state while preserving
immutable staging and a supersession ledger. This deviation prevents both missing-quarter evidence
and cumulative double counting and is covered by source, entity, and independent acceptance checks.

No implementation, local-verification, or Git handoff item is blocked. The logical Product A commits
were pushed to the existing branch, and draft PR #10 was rewritten in place with current scope,
schema/data-quality effects, real results, verification, and owner-only actions. Release publication,
PR merge, repository visibility changes, and hosted deployment remain explicit owner actions and are
not part of automated completion.
