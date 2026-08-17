# Product A user workflow

## Purpose

Use this read-only explorer to research observed technical H-1B and PERM sponsorship history from
FY2022 onward. It helps compare evidence; it does not determine current job eligibility or promise
future sponsorship.

> Ratings summarize observed historical evidence from official sources. They are not sponsorship
> guarantees or legal advice. Verify the exact position and current employer policy before relying
> on the result.

## Start on Home

1. Read the build and score version, source freshness, latest complete fiscal year, and current
   partial-period warning.
2. Confirm the database is a nonzero Product A build (`product_a_metrics_v1` /
   `product_a_scores_v1`).
3. Use the top observed employers or institutions as entry points, then read the methodology and
   limitations before interpreting stars.

Do not compare a current partial fiscal year with a complete year as if both covered the same
duration. Partial activity can affect recency; it is not annualized or counted in complete-year
consistency.

## Research an employer

1. Open **All Employers**. The default order uses hidden Overall score, Green Card score, H-1B
   score, latest observed year, then employer name.
2. Filter by minimum whole stars, organization type, state, job family, latest year, E-Verify
   context, or minimum qualifying LCA/PERM counts.
3. Read displayed stars together with raw qualifying counts and latest year. Hidden numbers support
   deterministic sorting; they are not probabilities and are not shown as percentages in primary
   tables.
4. Open **Organization Detail** and inspect `Why this rating`, `What this does not prove`, yearly
   activity, raw titles/families/statuses/locations/wages, source artifacts, and warnings.
5. Confirm whether the row is a petitioning `LEGAL_ENTITY` or a separate `PARENT_ROLLUP`.

A certified LCA is not an approved H-1B petition. `CERTIFIED-WITHDRAWN` receives partial historical
weight but is not a current promise. PERM is labeled observed employer-sponsored PERM history; even
a certified PERM record is not a green-card approval.

## Research universities and institutions

1. Open **Universities and Research Institutions**. The default order is observed Overall, Green
   Card, and H-1B sponsorship history—not R&D spending or internal policy.
2. Inspect the institution, petitioning legal employer, optional parent, control/type, three
   sponsorship ratings, qualifying counts, employer-level initial approvals, latest year,
   E-Verify context, higher-education context, Research Scale, and coverage.
3. Use Research Scale as separate HERD context only. High research activity cannot raise or fill a
   sponsorship rating.
4. Treat `Higher-education institution; exact cap-exempt status requires verification.` as a prompt
   for follow-up, not a legal determination.

There are no internal-policy filters in the Product A institution explorer. Retained policy
evidence is supplemental, incomplete, and not used in stars.

## Read rating states correctly

- `N out of 5 stars`: a positive hidden score mapped to the documented whole-star band.
- `No observed technical H-1B history`: identity and LCA coverage are valid, but no qualifying
  technical H-1B evidence was observed.
- `No observed technical PERM history`: identity and PERM coverage are valid, but no qualifying
  technical PERM evidence was observed.
- `No observed technical sponsorship history`: both histories resolve to valid zero.
- `Unrated`: required coverage or identity is missing/invalid. It is unknown, not a zero or refusal.
- `UNKNOWN`: supplemental evidence is absent or inconclusive. It is not `NO`.

Zero is never displayed as one star. Overall Sponsorship requires both H-1B and PERM histories; the
explorer does not silently reweight one available component into a partial Overall rating.

## Understand the three ratings

- **H-1B History:** 45% weighted qualifying technical `H-1B` LCA volume, 25% complete-year
  consistency, 15% recency, 10% family breadth, and 5% employer-level USCIS initial approvals.
  H-1B1 and E-3 remain queryable but are not scored.
- **Green Card Sponsorship History:** 45% weighted qualifying technical PERM volume, 25%
  complete-year consistency, 15% recency, and 15% family breadth.
- **Overall Sponsorship:** 40% H-1B History and 60% Green Card Sponsorship History, only when both
  resolve.

See [scoring.md](scoring.md) for count normalization, status weights, recency, star bands, and
Research Scale.

## Compare and export

1. Open **Compare**, search, and select up to five organization scopes.
2. Include both a legal entity and its parent rollup when you need to understand organization-level
   aggregation; never assume they are interchangeable.
3. Compare ratings, raw/yearly evidence, role families, source coverage, E-Verify context, and
   Research Scale side by side.
4. Apply filters in the employer or institution explorer before exporting CSV/Parquet. Null and
   unrated values remain null/unrated; exports do not replace them with zero.

## Verify evidence before acting

- Trace counts to official artifacts, URLs, checksums, periods, statuses, and raw rows.
- Distinguish legal-employer address from worksite location.
- Check title/SOC classification, especially exclusions and ambiguous review rows.
- Use exact legal/parent scope labels and relationship evidence.
- Check Data Health for source selection, schema drift, current partial periods, coverage, quality,
  build ID, and score version.
- Treat E-Verify, positive-only OPT, IPEDS/HERD, possible cap exemption, and retained policy as
  supplemental context that cannot alter sponsorship stars.
- Verify the exact current position and employer policy independently before making a decision.

## Current access status

The repository is public and a private Community Cloud deployment has not been owner-verified. Do
not publish a new data release, share existing release data as private, or describe the app as
private/deployed until the owner completes
[the deployment runbook](deployment/community-cloud.md). Changing visibility later does not retract
copies of already-public assets.
