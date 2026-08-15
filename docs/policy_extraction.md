# Policy extraction

Phase 7 ranks 200 candidate institutions from observed immigration activity, research expenditure, positive OPT evidence, E-Verify evidence, institution type, and the reviewed `manual_priority_institutions` list in `configs/policy_sources.yaml`. Candidate rank is a bounded discovery priority, not the Phase 8 product score.

## Evidence boundary

Discovery begins with the IPEDS official domain. Reviewed seeds and official sitemap results precede a bounded OpenAI web-search fallback restricted to that domain. A reviewed campus-specific university-system domain can be declared under `additional_official_domains` in `configs/policy_sources.yaml`. The pipeline fetches the actual official HTML or PDF before extraction, rejects every domain outside that per-institution allowlist, stores immutable content-addressed bytes and parsed text, retains retrieval metadata and hashes, and flags prompt-injection text. Search snippets and third-party pages are never evidence.

HTML parsing selects main content and preserves headings. PDF parsing uses the native text layer and page markers. Scanned PDFs are not automatically accepted.

## Structured extraction and caching

The extractor uses the official OpenAI Python SDK and Responses API with a strict Pydantic schema. It returns all 23 fact types for each document, uses only supplied document text, has no tools, and runs with `store=False`. `OPENAI_POLICY_MODEL` selects the runtime model; `OPENAI_API_KEY` is read from the secret environment or ignored `.env.local`.

Fetched documents are reusable for 24 hours from their retained processed metadata, which makes immediate recovery and acceptance replays network-free. After that window, the source is fetched again. The extraction cache key includes the parsed-text SHA-256, extractor version, and model. Unchanged bytes then read the validated cached response and make no extraction API call. A changed document hash creates new facts; prior documents and reviewed facts remain queryable with `valid_to` set and `is_current = false`, while publication views use only the active version.

## Review and publication

Every extracted fact starts as `NEEDS_REVIEW`, even when its evidence is exact. The pipeline verifies that the cited excerpt occurs in the fetched text after Unicode and whitespace normalization. Review decisions are stored separately in `data/review/policy_review_decisions.parquet` and overlaid without changing model output or evidence. The decision records reviewer identity, time, note, and explicit current-page confirmation; that confirmation can make an otherwise undated active page eligible for current product signals.

`policy review-exact` requires a newline-delimited list of fact IDs the operator already checked against the official URL, current document, institution scope, affirmative value, contradiction state, and exact excerpt. It admits only those requested exact `YES` facts with HTTPS official evidence and no contradiction. General-staff permanent-residence and cap-exemption facts are deliberately excluded and require individual review. `NO`, `LIMITED`, confidence below 0.85, contradictions, old or system-scoped policy, and scanned-document evidence also remain review-required.

Only `REVIEWED_ACCEPTED` facts that are current, HTTPS, and exact can enrich product metrics. DuckDB exposes all model facts in the review queue but organization detail displays accepted evidence only.

## Evaluation gates

`policy evaluate` compares accepted output with a manually reviewed JSONL benchmark containing at least 30 unique institutions. Publication requires at least 95 percent factual precision, full benchmark coverage, official HTTPS URLs and non-empty excerpts for every accepted fact, and zero accepted facts that fail exact-evidence verification. Ordinary tests mock OpenAI; the protected live contract is opt-in.
