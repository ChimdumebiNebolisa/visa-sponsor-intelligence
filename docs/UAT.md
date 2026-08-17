# Product A user-acceptance protocol

## Scope

This protocol validates the real local Product A build, not an empty database or CI fixture. It
checks the primary workflow:

> Discover, explain, and compare employers and research institutions by observed technical H-1B
> and PERM sponsorship history from FY2022 onward.

Policy review, repository privacy, PR merge, release publication, and hosted deployment are not
acceptance prerequisites. Policy is supplemental and cannot block Product A.

## Prerequisites and run

From the repository root:

```powershell
py -m uv run sponsor-intel metrics build
py -m uv run sponsor-intel quality report
py -m uv run sponsor-intel db build
py -m uv run python scripts/smoke_streamlit.py --database db/immigration.duckdb
py -m uv run python scripts/run_product_a_acceptance.py
```

The acceptance runner must reject a missing, empty, fallback, non-Product-A, or critically failed
database. Automated failures produce a nonzero exit code.

## Required report family

The runner writes:

```text
outputs/reports/product-a/
  source-selection.md
  source-selection.json
  score-distribution.md
  score-distribution.json
  validation.md
  validation.csv
  unresolved-entities.csv
  acceptance.md
  acceptance.json
```

The JSON/CSV files are the machine-readable evidence. Markdown is a human-readable rendering, not
a place to hand-edit failed checks into passes.

## Automated acceptance checks

Acceptance verifies at least:

1. all active metrics/rows use `product_a_metrics_v1` and `product_a_scores_v1`;
2. selected source artifacts have official URLs, checksums, periods, schema versions, and
   complete/partial labels;
3. each completed DOL LCA year uses one annual artifact or an explicitly reviewed, non-overlapping
   segment contract covering Q1-Q4 exactly once; the current partial year uses one latest cumulative
   snapshot; and repeated LCA case IDs are checked globally across selected fiscal years, permitting
   only exactly two chronological rows with stable visa class/legal-employer identity and a
   `CERTIFIED` to `CERTIFIED-WITHDRAWN` transition;
4. DOL PERM uses one final annual/Q4 period per completed fiscal year and one highest cumulative
   current partial period, while preserving both FY2024 form variants;
5. only technical `H-1B` LCA rows affect H-1B ratings; H-1B1/E-3 and unsuccessful statuses do not;
6. PERM status weights distinguish `CERTIFIED`, `CERTIFIED-EXPIRED`, and unsuccessful outcomes;
7. the current partial year affects recency but not complete-year consistency or annualized counts;
8. legal-entity and parent-rollup rows are separate and internally reconcilable;
9. rating formulas, 95th-percentile count caps, star bands, accessible labels, and explanations
   reproduce from stored ingredients;
10. a valid zero is `No observed … history`, an invalid/missing calculation is `Unrated`, and zero
    never becomes one star;
11. Overall requires both H-1B and PERM histories and uses 40%/60%;
12. E-Verify, OPT, IPEDS/HERD, cap-exemption context, and policy cannot alter sponsorship ratings;
13. institution default ranking is observed sponsorship history, not HERD or policy;
14. Research Scale is separate, 1–5 stars, and uses latest matched HERD evidence;
15. employer and institution explorers, detail, comparison, Data Health, and exports return nonzero
    real evidence through the service layer; and
16. Product A quality has zero critical failures without an OpenAI key or policy facts.
17. confirmed records remain rated under `PARTIAL_ENTITY_COVERAGE`, excluded candidates do not
    change the counts, the exact partial-coverage warning is visible, and only
    `UNRESOLVED_IDENTITY` causes identity-based `Unrated`.

## Named real-data validation

For every representative row, inspect raw employer names, legal identity, optional parent
relationship, legal address versus worksite, qualifying LCA/PERM counts, employer-level USCIS
initial approvals, role decisions, stars, explanation, source provenance, and supplemental-evidence
independence. Never force an ambiguous identity.

### Companies

- Microsoft
- Google
- one Amazon legal entity and the separate Amazon parent rollup
- Meta
- IBM

For Microsoft, Google/Alphabet, Amazon, Meta, and IBM, validate the reviewed legal entity and parent
rollup separately, verify the committed primary-source mapping metadata, and confirm that excluded
exact-name/location conflicts remain inspectable.
- Smart Data Solutions, only when confidently resolved
- at least two smaller technical employers selected deterministically from real results

### Institutions

- Massachusetts Institute of Technology
- Carnegie Mellon University
- Rice University
- University of Michigan
- University of Illinois Urbana-Champaign
- University of Washington
- one institution with high HERD activity but weak observed sponsorship
- one institution with stronger observed sponsorship but lower Research Scale

Each validation row records `PASS`, `FAIL`, `UNRESOLVED`, or `NOT_APPLICABLE` with concrete
evidence. `UNRESOLVED` is honest uncertainty; it must not be rewritten as a match or a valid zero.

## Manual application checks

Against `db/immigration.duckdb`, verify:

- Home shows the Product A disclaimer, build/score version, source freshness, latest complete FY,
  partial warning, and top observed employers/institutions.
- All Employers defaults to Overall hidden score, Green Card hidden score, H-1B hidden score,
  latest observed year, then name, while displaying stars and raw counts.
- Universities and Research Institutions defaults to sponsorship history and has no internal-policy
  filters.
- Organization Detail shows accessible star labels, `Why this rating`, `What this does not prove`,
  legal/parent scope, yearly/raw evidence, provenance, and uncertainty.
- Compare supports up to five legal/parent scopes without collapsing them.
- Data Health shows selected artifacts, official URLs, checksums, periods, rows, schema versions,
  build ID, active versions, coverage, and supplemental evidence separately.
- A certified LCA is not called a petition approval, PERM is not called a green-card approval, and
  USCIS is labeled `Employer-level H-1B initial approvals`.
- Partial periods remain visibly labeled on every page where their data appears.

## Owner-only deployment supplement

After, and only after, the owner makes the repository private, publishes a verified Product A
release, configures Community Cloud, and restricts sharing, manually test authorized, invited,
signed-out, and non-invited access; cold/warm verified-cache startup; logs/secrets; desktop/mobile
navigation; exports; query latency; and peak memory. Record these as deployment evidence, not local
Product A acceptance.

Until those actions are complete, report deployment as `BLOCKED_OWNER_ACTION`. Do not publish a
release or describe the app as private while the repository remains public.
