# Sponsorship Intelligence Explorer: user workflow

## Purpose

Use this read-only explorer to discover and compare employers and research institutions with the
strongest **historical** technical H-1B, certified PERM, reviewed institutional-policy, and
research evidence. Scores describe evidence strength and completeness. They are not legal advice,
a prediction, or a sponsorship promise.

## Start with research institutions

1. Open **Home** and read the release tag, build date, latest complete fiscal year, and partial-year
   warning. Do not compare a partial fiscal year directly with a complete year.
2. Review the compact decision-readiness table, then open **Research Institutions**.
3. Keep the default **Best research pathway** sort for the evidence-readiness view. It orders by
   decision tier first, then immigration and policy evidence; research spending is a later
   tiebreaker.
4. Narrow the list with the research-staff permanent-residence, PERM, EB-1B, policy-review
   coverage, H-1B, green-card, sponsorship, and research-pathway filters. E-Verify and possible cap
   exemption are separate signals, not prerequisites for historical sponsorship ranking.
5. Select a result and use **Open organization detail**. Read **Why this ranks here** and **What
   remains unknown** before interpreting the score.

Decision tiers mean evidence readiness, not sponsorship probability:

- `TIER_1_REVIEWED`: complete H-1B and green-card history, all four core policy questions reviewed,
  current IPEDS/linkage prerequisites, and a passing current quality gate.
- `TIER_2_STRONG_HISTORY_POLICY_INCOMPLETE`: complete H-1B and green-card history, but fewer than
  four core policy questions reviewed.
- `TIER_3_PARTIAL_HISTORY`: some immigration evidence exists, but historical score coverage is
  incomplete.
- `TIER_4_INSUFFICIENT_EVIDENCE`: evidence is too sparse for a meaningful decision ranking.

No institution should be treated as fully policy-ready until all four core questions have been
reviewed: research-staff H-1B eligibility, research-staff permanent-residence eligibility, PERM
support, and EB-1B support.

## Research any employer

1. Open **All Employers**. The default **Strongest sponsorship history** sort combines 40% H-1B
   history and 60% green-card history while prioritizing complete coverage.
2. Filter by raw technical LCA/PERM counts, component scores, recency, organization type, state, or
   role family. Use E-Verify only as the separately labeled STEM OPT signal.
3. Inspect the raw counts beside the score. A certified LCA is not an approved H-1B petition;
   historical certified PERM activity is not a current sponsorship commitment.
4. Open the detail page to inspect legal entities, aliases, historical titles, yearly H-1B/PERM
   activity, status counts, worksite states, official policy excerpts, and source provenance.

## Read uncertainty correctly

- `UNKNOWN`: the product does not have sufficient evidence. It does not mean `NO` or zero.
- `NO`: reviewed official evidence explicitly supports a negative substantive value.
- `NOT_STATED`: a reviewer checked the official source and did not find the claim. It is reviewed,
  but it does not become negative evidence.
- `PARTIAL` or `INCOMPLETE_EVIDENCE`: a numerical score uses only available components. Check its
  coverage; no full letter grade should appear below the configured gate.
- `POTENTIALLY_CAP_EXEMPT_*`: a research signal for follow-up, not a verified legal conclusion.

## Compare and export

1. Open **Compare**, search for up to five organizations, and select the relevant legal or parent
   organization IDs.
2. Compare historical H-1B/PERM counts, score coverage, E-Verify/OPT, institution policy, research
   strength, and unknowns side by side.
3. Return to either explorer page, apply the intended filters, and download CSV or Parquet. The
   export preserves null evidence as null; it does not replace missing scores with zero.

## Verify evidence before using it

- Follow the official URL beside a policy fact and confirm the exact excerpt, retrieval date,
  scope, review status, and reviewer.
- Use the legal-entity and parent tables to distinguish the petitioning entity from its broader
  organization.
- Check **Data Health** for the current publication gate, warnings, partial periods, and source
  freshness.
- Treat a missing policy profile, E-Verify lookup, OPT observation, LCA, or PERM record as an
  evidence gap—not a refusal to sponsor.

## Current access status

The repository was public and no private Streamlit URL had been owner-verified when Phase 10 UAT
was recorded. Follow `docs/deployment/community-cloud.md`; do not describe the app as private or
deployed until both repository visibility and Streamlit sharing have been verified.
