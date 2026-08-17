# Phase 10 real-data user-acceptance results

> These results distinguish passed observations from missing human policy review and owner-only deployment actions. No blocked item is reported as passed.

## Result

- Overall status: **COMPLETE_EXCEPT_FOR_HUMAN_OR_OWNER_ACTION**
- Code-testable contract checks: 16/16 passed
- UAT tasks passed: 16/18
- UAT tasks blocked on human review: 2
- UAT tasks failed: 0
- Source data release: `data-2026-08-15`
- Local quality build: `v2-dcdfeabb8229bb92`
- Score version: `['evidence_scores_v2_2026_08']`
- Metric version: `['scored_metrics_v2']`
- Database SHA-256: `104a218fe680c47ad1e2b9cb617ce47042f51c91f171bcbc07721701480121fa`

## Deployment truth

- Repository visibility: **PUBLIC**
- Local runtime bundle: `v2-dcdfeabb8229bb92` (evidence_scores_v2_2026_08)
- Published runtime build: `v1-df123235990f8fbc` (evidence_scores_v1_2026_08)
- Live URL: `NONE`
- Private access verified: **False**
- Status: **BLOCKED_OWNER_ACTION**

## V2 contract checks

| Check | Status | Evidence |
|---|---|---|
| `v2_schema` | **PASS** | All required V2 columns are present. |
| `quality_gate` | **PASS** | Build v2-dcdfeabb8229bb92: passed=True; critical_failures=0. |
| `v2_versions` | **PASS** | score_version=['evidence_scores_v2_2026_08']; metric_version=['scored_metrics_v2']. |
| `v1_reproducibility` | **PASS** | employer_scores_v1.parquet=present; institution_scores_v1.parquet=present |
| `v2_formula` | **PASS** | sponsorship={'h1b_history': 0.4, 'green_card_history': 0.6}; research_pathway={'sponsorship_history': 0.5, 'policy_support': 0.3, 'research_strength': 0.2}; core=('h1b_research_staff_eligible', 'pr_research_staff_eligible', 'perm_supported', 'eb1b_supported'). |
| `grade_gating` | **PASS** | Misleading grade rows: 0. |
| `everify_independence` | **PASS** | Employers scored for sponsorship with E-Verify UNKNOWN: 223,007. |
| `readiness_quality_prerequisite` | **PASS** | Tier 1/2 rows without QUALITY_GATE_PASSED: 0. |
| `uncertainty_semantics` | **PASS** | Typed policy contracts expose distinct UNKNOWN, NO, NOT_STATED, NEEDS_REVIEW, and REVIEWED_NOT_STATED states. |
| `nonzero_service` | **PASS** | Default employer and institution queries returned nonzero rows. |
| `decision_first_default` | **PASS** | The first five default IDs differ from research-activity ordering. |
| `policy_review_packet` | **PASS** | institutions=50; rows=200; pending=197. |
| `entity_audit` | **PASS** | companies=30; institutions=30; pending_human_review=60. |
| `classifier_audit` | **PASS** | changed_records=11662; sample=43; pending_review=43. |
| `deployment_package` | **PASS** | src\sponsor_intel\deployment\release_bootstrap.py=present; .streamlit\config.toml=present; .streamlit\secrets.example.toml=present; docs\deployment\community-cloud.md=present; app\requirements.txt=present |
| `local_v2_runtime_bundle` | **PASS** | Local bundle metadata reports build=v2-dcdfeabb8229bb92; score=evidence_scores_v2_2026_08; metric=scored_metrics_v2. |
| `published_v2_runtime_release` | **BLOCKED_OWNER_ACTION** | Remote release metadata reports build=v1-df123235990f8fbc; score=evidence_scores_v1_2026_08; metric=scored_metrics_v1. Publish the quality-approved V2 bundle only after repository privacy is established. |
| `private_deployment` | **BLOCKED_OWNER_ACTION** | repository=ChimdumebiNebolisa/visa-sponsor-intelligence; visibility=PUBLIC; live_url=NONE; private_access_verified=False. |

## Requested UAT tasks

| # | Task | Status | Evidence |
|---:|---|---|---|
| 1 | Find research institutions with complete core-policy review | **BLOCKED_HUMAN_REVIEW** | The real release returned zero institutions with 100% core review. The top-50 packet retains 197 pending core rows; no result was fabricated. |
| 2 | Filter for research-staff permanent-residence eligibility | **BLOCKED_HUMAN_REVIEW** | The real release returned zero rows where research_staff_permanent_residence_policy=YES. The top-50 packet retains 197 pending core rows; no result was fabricated. |
| 3 | Filter for PERM support | **PASS** | Found 9 result(s); every returned row has perm_support=YES. |
| 4 | Filter for EB-1B support | **PASS** | Found 1 result(s); every returned row has eb1b_support=YES. |
| 5 | Find institutions with strong technical H-1B history but incomplete policy evidence | **PASS** | Found 144 institution(s) with H-1B score >=60 and core review below 100%. |
| 6 | Find high-research institutions with weak or unknown green-card evidence | **PASS** | Found 660 institution(s) after sorting by research activity and applying the weak/unknown green-card rule. |
| 7 | Find private companies with strong technical H-1B and PERM histories | **PASS** | Found 5 non-institution legal names with a corporate suffix, complete sponsorship coverage, and nonzero technical LCA/PERM history. |
| 8 | Compare a university, a research nonprofit/lab, and a private company | **PASS** | Comparison returned 3/3 unique rows selected by deterministic evidence/type rules. |
| 9 | Open a parent organization and inspect its legal entities | **PASS** | Selected parent detail exposed 96 legal entities. |
| 10 | Confirm an ambiguous organization does not silently merge | **PASS** | Presentation review queue rows=21117; independently selected real-data audit conflicts held for human review=2. |
| 11 | Confirm missing E-Verify does not suppress H-1B/PERM ranking | **PASS** | Default sponsorship ranking contains 1993 of the first 2,000 employers with E-Verify UNKNOWN and nonzero technical LCA/PERM evidence. |
| 12 | Confirm UNKNOWN is not displayed or exported as NO or zero | **PASS** | Observed E-Verify display=UNKNOWN; nullable score CSV='sponsorship_history_score'. |
| 13 | Confirm partial FY2026 evidence is visibly labeled | **PASS** | latest_complete_fy=2025; partial_fy=2026; partial_quarter=2; message=Processed government and institution metrics are available. FY2026 is partial and must not be compared directly with complete years. |
| 14 | Export a filtered institution result | **PASS** | Exported 163,667 bytes and 123 data rows for LCA>=5 and PERM>=1. |
| 15 | Export a filtered employer result | **PASS** | Exported 3,851,703 bytes and 4,403 data rows for LCA>=10 and PERM>=1. |
| 16 | Verify every policy claim in detail links to an official source and exact excerpt | **PASS** | The selected detail exposed 4 reviewed current fact(s); all have HTTPS source links and nonempty exact excerpts, and the view gates on exact_excerpt_verified. |
| 17 | Verify ranking explanations match underlying raw counts | **PASS** | Formula match=True; detail trend/count match=True; stored explanation present=True. |
| 18 | Verify high R&D alone does not override stronger immigration evidence | **PASS** | Found a lower-R&D institution ranked earlier because it has complete sponsorship coverage and stronger green-card evidence. The earlier row is Tier 2 (policy incomplete), so this does not claim a Tier 1 decision-ready comparison. |

## Representative selection

Organizations were selected deterministically from the real database: the highest default-ranked university; the strongest non-institution national-lab/research-lab name matching the evidence rule; and the strongest complete-history non-institution legal name with a corporate suffix. This avoids hand-picking only convenient cases.

## Before-and-after failures

Every task retains an `attempts` array in `uat-results.json`. A later rerun appends a materially changed observation, so an original failure remains visible after a fix. No task in this report has a fabricated remediation attempt.

## Remaining human and owner work

- Core policy packet rows still pending review: 197.
- Role-classification sample rows still pending manual review: 43.
- Entity audit rows still pending human review: 60.
- The repository owner must make the repository private, deploy `app/Home.py` on Python 3.12, publish the quality-approved V2 runtime bundle, set the read-only release token in Streamlit secrets, configure restricted sharing, and verify invited/non-invited access.
