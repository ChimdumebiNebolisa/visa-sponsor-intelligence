# Evidence-strength scoring

Phase 8 implements deterministic, nullable evidence-strength indicators. The sole formula source
is `configs/scoring.yaml`; the current version is `evidence_scores_v1_2026_08`. A score summarizes
historical evidence. It is not legal advice, an eligibility decision, or a probability of future
sponsorship.

## Shared rules

- Missing evidence stays null. It is not converted to zero and is not silently reweighted.
- A zero is permitted only for an authoritative negative, currently confirmed inactive E-Verify.
- Case volumes use `100 * log(1 + observed) / log(1 + cap)`, clipped to 0-100.
- Recency starts at 100 in the 2026 reference year and loses the configured amount per elapsed
  year, never falling below zero.
- Every component exposes its raw inputs, score, coverage, confidence, grade or status, and a
  plain-language explanation in the processed metrics and explorer.
- Component coverage is the sum of configured weights for available inputs. Composite coverage is
  the configured weighted sum of the component coverage values, with missing components retained
  as zero coverage. Composite scores require every configured component and never reweight merely
  missing evidence.
- Scores and score labels reproduce from the checked-in YAML and carry `score_version`.

## STEM OPT readiness

Confirmed active E-Verify contributes 85 points. A positive recent OPT/STEM OPT observation adds
15, capped at 100. A positive OPT observation without a confirmed E-Verify result receives 55.
Confirmed inactive E-Verify is the explicit current-readiness blocker and receives zero. No-match,
ambiguous, unchecked, and failed E-Verify lookups remain unknown. E-Verify and positive-only OPT
coverage weights are 70% and 30%.

Statuses are `STRONG` at 80+, `MODERATE` at 60+, `LIMITED` below 60, `UNKNOWN` without qualifying
evidence, and `EXPLICIT_BLOCKER` for confirmed inactive E-Verify.

## H-1B history

The H-1B history score uses only observed DOL/USCIS history:

| Component | Weight | Rule |
|---|---:|---|
| Relevant LCA volume | 30% | Log-scaled to a 1,000-record cap |
| USCIS initial approvals | 25% | Log-scaled to a 500-approval cap |
| Active fiscal years | 15% | Linear to five years |
| Recency | 15% | 25 points removed per year since last LCA/USCIS activity |
| Relevant technical share | 10% | Relevant LCA divided by all LCA records |
| Approval ratio | 5% | Initial approvals divided by initial decisions only when at least 10 decisions exist |

The approval-ratio denominator safeguard prevents a tiny sample from dominating. Potential cap
exemption remains a separate field and is never mixed into this score.

## Green-card history

| Component | Weight | Rule |
|---|---:|---|
| Relevant certified PERM volume | 35% | Log-scaled to a 500-record cap |
| Active fiscal years | 20% | Linear to five years |
| Recency | 15% | 25 points removed per year since last PERM activity |
| Relevant technical share | 15% | Relevant certified PERM divided by all PERM records |
| Exact-title repetition | 15% | Most repeated exact technical title divided by relevant certified PERM |

No observed PERM history produces an unknown score, not a refusal label. Reviewed institution
policy is displayed and scored separately.

## Research strength

Institutions with linked HERD observations are ranked within the current build. The score combines
total R&D percentile (35%), computing R&D percentile (25%), engineering R&D percentile (20%), and
federal R&D percentile (20%). Missing short-form fields stay null and reduce coverage. No linked
HERD observation produces an unknown score.

## Reviewed policy support

Only current `REVIEWED_ACCEPTED` facts with an exact verified excerpt, current validity, and an
official HTTPS source may contribute. Unreviewed, rejected, expired, unknown, and not-stated facts
do not contribute and reduce coverage. The YAML maps each supported fact type and enum value to a
0-100 evidence value. Positive eligibility/support facts score upward; explicit exclusions,
waiting periods, minimum-duration requirements, and discretionary language score downward. The
result is normalized only across observed reviewed facts and must always be read with its coverage.

Fact weights are: research-staff H-1B 18%, research-staff permanent residence 18%, general-staff
H-1B 6%, general-staff permanent residence 6%, PERM 16%, EB-1B 14%, temporary-position exclusion
6%, grant-funded exclusion 4%, waiting period 4%, minimum appointment duration 4%, and policy
discretion 4%.

## Composites and grades

Immigration evidence requires all three components and uses STEM OPT readiness 20%, H-1B history
35%, and green-card history 45%. Research pathway requires all three components and uses
immigration evidence 45%, research strength 25%, and reviewed policy support 30%.

Grades are A+ at 90+, A at 80+, B at 70+, C at 60+, D at 40+, and F below 40. An unavailable
composite is `UNKNOWN`. Confidence labels are `HIGH` at 85%+ coverage, `MODERATE` at 60%+,
`LIMITED` above zero, and `UNKNOWN` at zero.

## Reproduction

```bash
uv run sponsor-intel scores build
uv run sponsor-intel db build
uv run pytest tests/unit/test_scoring.py tests/integration/test_metrics_explorer.py
```

The score build writes `data/processed/employer_scores.parquet` and embeds the same score columns
beside raw evidence in `employer_metrics.parquet` and `institution_metrics.parquet`. The Compare
page shows up to five organizations with raw values, coverage, confidence, version, and
explanations.
