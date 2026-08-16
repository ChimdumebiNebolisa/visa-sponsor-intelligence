# Product A sponsorship ratings

`configs/scoring_product_a.yaml` and score version `product_a_scores_v1` define the active rating
contract. Historical V1/V2 sidecars may remain for reproducibility, but letter grades, policy
support, STEM OPT readiness, and research-pathway scores are not Product A ratings.

Ratings summarize observed official evidence. They are not legal advice, petition approvals,
eligibility decisions, probabilities, or promises that an employer will sponsor a particular job.

## Evidence admitted to ratings

| Rating | Admitted evidence | Excluded from the rating |
|---|---|---|
| H-1B History | Technical DOL LCA rows where visa class is exactly `H-1B`; employer-level USCIS H-1B initial approvals as limited corroboration | H-1B1, E-3, unsuccessful LCA statuses, E-Verify, OPT, institution type, HERD, cap-exemption context, policy |
| Green Card Sponsorship History | Technical DOL PERM rows | Unsuccessful PERM statuses, E-Verify, OPT, institution type, HERD, cap-exemption context, policy |
| Overall Sponsorship | Resolved H-1B History and Green Card Sponsorship History | Any supplemental evidence and any unresolved component |

Immigration evidence remains attached to the petitioning legal entity. A parent-organization rating
is a separately identified rollup of reviewed constituent legal entities; it never replaces a
legal-entity rating.

## Shared component rules

- Hidden component and composite scores range from 0 to 100. They exist for deterministic sorting,
  testing, versioning, and audit. Primary product tables display whole stars and accessible labels.
- Count components use `log1p` and a deterministic 95th-percentile cap calculated over eligible
  resolved employers in that build. Each calculated cap is persisted in score metadata.
- Complete-year consistency is the number of complete fiscal years with positive qualifying
  activity divided by the number of complete covered fiscal years since FY2022.
- The current partial fiscal year can affect recency. It does not enter complete-year consistency,
  is not annualized, and is never compared as if complete.
- Recency is 1.00 for activity in the current partial year or latest complete year, 0.75 one
  complete year earlier, 0.50 two complete years earlier, 0.25 three complete years earlier, and
  0 thereafter.
- Breadth is the number of distinct normalized qualifying job families, capped at five.
- Reviewed role overrides have priority. Ambiguous classifications do not silently become
  qualifying evidence.

## H-1B History

Only technical `H-1B` LCA rows qualify. H-1B1 and E-3 rows remain queryable for audit, but cannot
alter this score.

| Component | Weight | Rule |
|---|---:|---|
| Weighted relevant LCA volume | 45% | `CERTIFIED` = 1.0; `CERTIFIED-WITHDRAWN` = 0.5; all unsuccessful statuses = 0; `log1p` normalized to the build's 95th-percentile cap |
| Complete-year consistency | 25% | Positive complete years divided by complete covered years since FY2022 |
| Recency | 15% | Shared recency schedule above |
| Relevant family breadth | 10% | Distinct normalized qualifying families divided by five, capped at 1 |
| USCIS initial approvals | 5% | Employer-level H-1B initial approvals, `log1p` normalized to the build's 95th-percentile cap |

USCIS data is employer-level and is not title-specific. Its exact product label is
`Employer-level H-1B initial approvals`. It corroborates role-level DOL evidence; it does not turn
an employer with no qualifying technical LCA history into observed technical H-1B history.

## Green Card Sponsorship History

The user-facing name deliberately says history, not approvals. A certified labor certification is
not a green-card approval and does not promise future sponsorship.

| Component | Weight | Rule |
|---|---:|---|
| Weighted relevant PERM volume | 45% | `CERTIFIED` = 1.0; `CERTIFIED-EXPIRED` = 0.5; denied, withdrawn, and other unsuccessful statuses = 0; `log1p` normalized to the build's 95th-percentile cap |
| Complete-year consistency | 25% | Positive complete years divided by complete covered years since FY2022 |
| Recency | 15% | Shared recency schedule above |
| Relevant family breadth | 15% | Distinct normalized qualifying families divided by five, capped at 1 |

## Overall Sponsorship

Overall Sponsorship uses:

- 40% H-1B History
- 60% Green Card Sponsorship History

Both component histories must be resolved. The implementation does not silently reweight a single
available component into a partial Overall rating.

## Star mapping and missingness

| Hidden score/evidence state | Primary display |
|---:|---|
| 80–100 | 5 stars (`5 out of 5 stars`) |
| 65–<80 | 4 stars (`4 out of 5 stars`) |
| 45–<65 | 3 stars (`3 out of 5 stars`) |
| 25–<45 | 2 stars (`2 out of 5 stars`) |
| >0–<25 | 1 star (`1 out of 5 stars`) |
| 0 with valid required source coverage and resolved identity | `No observed technical … history` |
| Missing/invalid required coverage or unresolved identity | `Unrated` |

A validated zero is evidence that the covered official records contained no qualifying observation
under the Product A rules. It is not missing data and is never displayed as one star. `Unrated`
means the product cannot make the calculation; it is not a negative conclusion.

Every rating exposes an explanation, coverage/source state, score version, and the raw ingredients
needed to reproduce it. The UI pairs `Why this rating` with `What this does not prove`.

## Research Scale

Research Scale is a separate 1–5-star institution context rating among matched institutions with
latest-year HERD evidence. It prioritizes computer and information sciences R&D, then engineering
R&D, with total R&D as secondary context or fallback. Missing HERD evidence stays `Unrated`.

Research Scale never changes H-1B History, Green Card Sponsorship History, or Overall Sponsorship,
and must not be called Sponsorship Potential.

## Reproduce and inspect

```bash
uv run sponsor-intel metrics build
uv run sponsor-intel quality report
uv run sponsor-intel db build
uv run python scripts/run_product_a_acceptance.py
```

Inspect `outputs/reports/product-a/score-distribution.{md,json}` for build-level score/star
distributions and persisted count caps, and `validation.{md,csv}` for named legal/parent scope
checks. Re-running unchanged evidence, reviewed mappings, and configuration must produce identical
ratings.
