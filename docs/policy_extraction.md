# Supplemental institution policy extraction

> **Product A status:** Supplemental, incomplete, and not used in sponsorship ratings. This manual
> workflow is optional. Normal ingestion, metrics, ratings, quality, database, release, tests, and
> app startup require neither policy facts nor an OpenAI API key.

The repository retains a bounded institution-policy discovery/extraction/review pipeline from the
historical Product B work. Its output may help a user find official pages for independent
verification, but it does not establish that a particular job sponsors, that an institution is
cap-exempt, or that a policy will apply to a candidate.

## Evidence boundary

Candidate rank is a discovery priority, not a product score or explorer ordering signal. Discovery
begins with the IPEDS official domain. Reviewed seeds and official sitemap results precede an
optional domain-restricted search fallback. A reviewed campus-specific system domain may be added
to `configs/policy_sources.yaml`.

The pipeline fetches the actual official HTTPS HTML/PDF, rejects domains outside the per-institution
allowlist, stores content-addressed bytes and parsed text, retains retrieval metadata/hashes, and
flags prompt-injection text. Search snippets, attorney pages, aggregators, forums, social media,
institution type, and general assumptions are never policy evidence.

HTML parsing preserves main-content headings. PDF parsing uses the native text layer and page
markers; scanned PDFs are not automatically accepted.

## Explicit manual invocation

Only this optional workflow may read `OPENAI_API_KEY` and `OPENAI_POLICY_MODEL` from a secret
environment or ignored `.env.local`:

```bash
uv run sponsor-intel policy candidates
uv run sponsor-intel policy build --enrichment-limit 10
```

Use a reviewed small bound first. Do not add policy build to the normal government refresh or
release path. The supplemental policy workflow is manual-only and must not trigger release
publication.

The extractor uses the official OpenAI SDK with a strict Pydantic response schema, only the supplied
document text, no tools, and `store=False`. The cache key includes parsed-text SHA-256, extractor
version, and model. Unchanged bytes reuse the validated cached response. Changed documents create a
new temporal fact version while retaining prior evidence for audit.

## Review semantics

Every extracted fact starts `NEEDS_REVIEW`, even when its cited text is present. The pipeline
verifies exact excerpt occurrence after Unicode/whitespace normalization, but that mechanical check
is not human review.

Review decisions are stored separately from model output and record reviewer, time, note, current
page confirmation, institution/campus/system scope, and contradiction handling. The bounded
`policy review-exact` helper accepts only explicitly requested, manually checked, current,
affirmative facts with exact official HTTPS evidence and no contradiction. Cap-exemption and
general-staff permanent-residence conclusions require individual review. Negative/limited values,
low confidence, contradictions, stale/system-scoped evidence, and scanned documents remain in the
review queue.

Only current `REVIEWED_ACCEPTED` exact facts may appear in supplemental detail. Every display must
say:

- `Supplemental`
- `Incomplete`
- `Not used in sponsorship ratings`

Absence, extraction failure, or incomplete review remains `UNKNOWN`; it is never converted into a
negative conclusion.

## Supplemental evaluation

`uv run sponsor-intel policy evaluate` may compare accepted output with the manually reviewed
benchmark to assess the optional extractor. Its result governs only whether supplemental policy
facts are trustworthy enough to display. It is not a Product A quality or release gate, and a
failure must not alter employer/institution sponsorship scores, stars, sorting, or availability.

Ordinary tests use mocks. Any live OpenAI contract is explicit and opt-in; never expose the key in
logs, fixtures, committed files, or workflow artifacts.
