> **Superseded for active product behavior.** `PRODUCT_A_SPEC.md` is the authoritative current
> specification for Historical Sponsorship Intelligence (Product A). This file is retained as the
> original historical Product B/V1 specification and must not control active ratings, rankings,
> quality gates, releases, or required credentials.

# Sponsorship Intelligence Explorer

## Product and Engineering Specification

**Version:** 1.0  
**Status:** Approved for implementation  
**Owner:** Mitch  
**Primary development environment:** Codex Cloud connected to a private GitHub repository  
**Specification date:** 2026-08-13

---

## 1. Executive summary

Build a private, evidence-first web application that helps a user discover, rank, filter, compare, and inspect U.S. employers based on historical immigration sponsorship activity and, for universities and research institutions, institutional research strength and written immigration policies.

The application must answer questions such as:

- Which U.S. employers have repeatedly filed H-1B and PERM cases for relevant technical occupations since FY2022?
- Which employers are currently enrolled in E-Verify?
- Which employers have positive historical evidence of employing OPT or STEM OPT students?
- Which universities and research institutions have sponsored software, research engineering, AI/ML, data, systems, infrastructure, HPC, cloud, DevOps, and computer science research roles?
- Which universities state that research staff or general staff are eligible for H-1B or employment-based permanent residence sponsorship?
- Which institutions are potentially or demonstrably H-1B cap-exempt?
- What official evidence supports every conclusion?
- How recent, complete, and reliable is the evidence?

The product is **not a job tracker**. It will not scrape current openings, monitor applications, find recruiters, send outreach, or decide whether a specific posting will sponsor a specific person. Its purpose is employer and institution intelligence.

The system must never reduce immigration sponsorship to one unsupported binary field such as `sponsors = true`. It must preserve separate evidence for STEM OPT readiness, H-1B activity, PERM activity, research strength, institutional policy, cap-exemption status, source coverage, and uncertainty.

---

## 2. Product definition

### 2.1 One-sentence definition

> A private sponsorship intelligence explorer that ranks and explains U.S. companies, universities, hospitals, research institutes, national laboratory operators, nonprofits, and other employers using official immigration records, institutional data, and cited university policy evidence.

### 2.2 Primary user

The initial product is for one user:

- International undergraduate Computer Science student in the United States
- Expected graduation: May 2027
- Interested in maximizing long-term immigration optionality
- Targeting software engineering and research-adjacent technical work

### 2.3 Relevant technical role universe

The system must classify and analyze these role families:

1. Software engineering
2. Research software engineering
3. Research engineering
4. Machine learning and artificial intelligence engineering
5. Data engineering and data science
6. Systems and infrastructure engineering
7. High-performance computing
8. Cloud engineering
9. DevOps and site reliability engineering
10. Computer science research
11. Closely related computing and technical roles

Generic academic, medical, biological, administrative, sales, helpdesk, and unrelated engineering roles must not be counted as relevant technical roles unless a deterministic rule or reviewed override establishes that they belong to the target universe.

### 2.4 Employer universe

The database must cover all U.S. employers present in the authoritative input datasets, then classify each organization where possible as one of:

- `for_profit`
- `university_public`
- `university_private_nonprofit`
- `college_other`
- `research_nonprofit`
- `hospital_or_health_system`
- `academic_medical_center`
- `national_lab_or_operator`
- `government`
- `governmental_research_organization`
- `university_affiliated_nonprofit`
- `other_nonprofit`
- `staffing_or_consulting`
- `other`
- `unknown`

Universities must not be placed in a separate disconnected database. They are part of the same employer universe, with additional institution-specific fields and views.

---

## 3. Locked product decisions

| Decision | Requirement |
|---|---|
| Historical range | FY2022 onward |
| Employer scope | All U.S. employers found in authoritative sources |
| Technical role scope | SWE, research software, research engineering, AI/ML, data, systems/infrastructure, HPC, cloud/DevOps, CS research, and closely related roles |
| University policy scope | Rank all institutions, then enrich the top 150 to 250 relevant institutions in V1 |
| Default policy candidate count | 200, configurable |
| Government refresh cadence | Quarterly and on demand |
| University policy refresh cadence | Every 3 to 6 months and on demand |
| Repository | Private GitHub repository |
| Development agent | Codex Cloud |
| Analytical storage | DuckDB plus Parquet |
| Primary dataframe engine | Polars |
| Policy extraction | Automated OpenAI API pipeline using Structured Outputs |
| User interface | Private Streamlit application for V1 |
| Hosting | Local-first; deployment is optional and not required for V1 |
| Scores | Separate STEM OPT, H-1B, green-card history, policy, research, and composite scores |
| Job tracking | Explicitly out of scope |
| Missing evidence | `UNKNOWN`, not automatically `NO` |
| Employer identity | Legal entity and parent organization must remain separate |

---

## 4. Goals

### 4.1 Product goals

The application must:

1. Produce a searchable master employer universe from FY2022 onward.
2. Show recent and historical H-1B and PERM activity.
3. Distinguish all employer filings from filings for relevant technical roles.
4. Identify universities and research institutions through authoritative institution data.
5. measure research intensity using NSF HERD data.
6. Show positive OPT/STEM OPT employer observations where public data supports them.
7. Check E-Verify enrollment for prioritized employers.
8. Extract university H-1B and permanent-residence policies from official sources.
9. preserve evidence, source URLs, retrieval dates, and confidence for every policy conclusion.
10. Explain scores rather than presenting opaque rankings.
11. Allow filtered export to CSV and Parquet.
12. remain reproducible, testable, and refreshable.

### 4.2 Engineering goals

The implementation must:

- Be deterministic wherever possible.
- Be idempotent.
- Detect source schema drift.
- Preserve immutable raw source artifacts.
- Record checksums and retrieval metadata.
- Separate raw, normalized, resolved, aggregated, and presentation layers.
- Expose unresolved entity matches rather than silently guessing.
- Make all important thresholds configurable.
- Support local execution and CI execution.
- Avoid committing large raw datasets or secret values to Git.
- Support phased implementation through small, reviewable pull requests.

---

## 5. Non-goals

V1 must not include:

- Current job-posting ingestion
- New-grad or internship discovery
- Application tracking
- Recruiter discovery
- Outreach automation
- Email sending
- Resume matching
- Live visa legal advice
- A prediction that a specific employer will sponsor a specific candidate
- Automatic claims that an employer is legally cap-exempt based only on its name
- Automatic claims that an employer does not sponsor because no record was found
- A public multi-user SaaS product
- User authentication or billing
- A mobile-first interface
- Postgres, Supabase, or a public API
- Machine-learning-based entity resolution without a review path
- LLM-based bulk role classification
- Full scraping of every university in the United States
- Full scraping of the entire E-Verify employer universe if no supported bulk source exists

Possible future integrations with other job-search workflows are irrelevant to V1 and must not influence the initial architecture.

---

## 6. Evidence model and mandatory language

Every displayed conclusion must be labeled as one of:

| Evidence class | Meaning |
|---|---|
| `OBSERVED_GOVERNMENT_RECORD` | Directly observed in DOL, USCIS, ICE/SEVP, NCES, or NSF data |
| `OFFICIAL_INSTITUTION_POLICY` | Explicitly stated on an official institution domain or official document |
| `DERIVED_METRIC` | Calculated from source records using documented code |
| `REVIEWED_MAPPING` | Manually approved entity, role, or parent-organization mapping |
| `INFERENCE` | A bounded inference that must be visibly labeled |
| `UNKNOWN` | Evidence is absent, incomplete, unresolved, or not explicit |
| `EXPLICIT_NO` | An authoritative source explicitly says the relevant benefit or category is unavailable |

The user interface and exports must never blur these categories.

### 6.1 Required distinctions

The system must preserve these distinctions:

- E-Verify enrollment does not prove willingness to hire F-1 students.
- Historical OPT employment does not prove current hiring.
- A certified LCA is not an approved H-1B petition.
- USCIS H-1B petition history does not identify the exact current job policy.
- Historical PERM activity does not promise sponsorship for a current employee.
- A university's ability to file H-1B does not mean every title is eligible.
- A university's H-1B policy does not establish permanent-residence eligibility.
- A nonprofit label does not automatically establish cap exemption.
- An IPEDS match establishes an educational institution identity, not every immigration conclusion.
- Missing data means unknown unless an authoritative source explicitly establishes otherwise.
- A parent organization is not interchangeable with the legal petitioning entity.

---

## 7. User experience

### 7.1 V1 interface framework

Use Streamlit for V1.

Reasons:

- The product is primarily an analytical explorer.
- It needs dense tables, filters, charts, drilldowns, and exports.
- A local-first Streamlit application minimizes frontend overhead.
- The data and domain logic must remain separated from Streamlit so a future frontend replacement does not require rebuilding the pipeline.

The application must use a service/query layer. Streamlit pages must not contain raw SQL scattered through UI code.

### 7.2 Main pages

#### Page 1: Overview

Display:

- Dataset coverage by source
- Latest complete fiscal year
- Current partial fiscal year and quarter, if applicable
- Number of legal entities
- Number of parent organizations
- Number of institutions
- Number of relevant technical H-1B LCA records
- Number of relevant technical PERM records
- Number of institutions with reviewed policy evidence
- Number of unresolved entity matches
- Data freshness warnings
- Clear disclaimer that the product reports evidence, not guaranteed sponsorship

#### Page 2: All Employers

Provide a filterable table with:

- Parent organization
- Legal entity count
- Organization type
- State
- E-Verify status
- Known OPT observation
- H-1B activity score
- Relevant H-1B LCA count since FY2022
- USCIS initial H-1B approvals since FY2022
- Relevant certified PERM count since FY2022
- Last H-1B activity
- Last PERM activity
- Potential cap-exemption status
- Data confidence
- Overall immigration evidence score

Filters must include:

- Organization type
- State
- E-Verify status
- Known OPT status
- Potential cap-exemption status
- Minimum H-1B activity
- Minimum relevant H-1B cases
- Minimum relevant PERM cases
- Last observed activity year
- Technical role family
- Employer name or alias
- Evidence confidence
- Staffing/consulting exclusion

#### Page 3: Universities and Research Institutions

Provide a dedicated institution view with:

- Institution name
- Parent system
- Public or private status
- IPEDS UNITID
- State
- HERD total R&D
- HERD computer and information sciences R&D
- HERD engineering R&D
- Federal R&D
- Relevant H-1B activity
- Relevant PERM activity
- E-Verify status
- Potential or verified cap-exemption status
- Research-staff H-1B policy
- Research-staff permanent-residence policy
- General-staff permanent-residence policy
- PERM support
- EB-1B support
- Policy review status
- Research pathway score

Filters must include:

- Public/private
- State
- Minimum HERD total R&D
- Minimum computing R&D
- Minimum engineering R&D
- H-1B activity
- PERM activity
- Research-staff H-1B eligibility
- Research-staff permanent-residence eligibility
- General-staff permanent-residence eligibility
- PERM supported
- EB-1B supported
- Postdocs excluded
- Policy confidence
- Potential cap-exemption status

#### Page 4: Organization Detail

The detail page must show:

1. Identity
   - Canonical parent organization
   - Legal entities
   - Aliases
   - Locations
   - Organization type
   - Institution identifiers where applicable

2. Immigration history
   - H-1B trend by fiscal year
   - Initial and continuing petition totals where USCIS data supports them
   - Relevant LCA trend
   - Relevant PERM trend
   - Certified, denied, withdrawn, and other case statuses
   - Recent activity
   - Exact technical titles and SOC classifications
   - Worksite states
   - Wage distributions where available

3. STEM OPT signals
   - E-Verify status and retrieved date
   - Positive OPT/STEM OPT observations
   - Explicit note that these are not promises of sponsorship

4. University/research data
   - IPEDS profile
   - HERD metrics
   - Parent university system
   - Research percentile measures

5. Institution policy
   - Policy facts
   - Supporting excerpt
   - Official URL
   - Document title
   - Retrieved date
   - Model extraction version
   - Human-review status
   - Contradictions or unresolved ambiguity

6. Scores
   - Component values
   - Formula version
   - Coverage
   - Confidence
   - Explanation of what increased or reduced the score

7. Provenance
   - Source artifacts
   - Source fiscal years
   - Data freshness
   - Known limitations

#### Page 5: Compare

Allow comparison of up to five organizations across:

- E-Verify
- OPT evidence
- H-1B history
- Relevant H-1B activity
- PERM history
- Relevant PERM activity
- Research metrics
- Policy support
- Cap-exemption status
- Confidence and coverage

#### Page 6: Evidence Review

For manually reviewing:

- Ambiguous entity matches
- Ambiguous parent mappings
- Ambiguous role classifications
- Low-confidence policy facts
- Policy contradictions
- Expired policy documents
- Broken source URLs
- E-Verify lookup mismatches

#### Page 7: Data Health

Display:

- Source freshness
- Row counts
- Schema versions
- Failed validations
- Missing columns
- Duplicate cases
- Match coverage
- Role-classification coverage
- Policy extraction coverage
- Current source-manifest checksum
- Build ID

### 7.3 UI design requirements

The interface must be:

- Desktop-first
- Dense but readable
- Restrained and professional
- Built around tables, evidence, and comparison
- Free of decorative animations
- Free of glassmorphism
- Free of excessive gradients
- Free of giant typography
- Free of unexplained badges
- Explicit about unknown values
- Consistent in terminology
- Clear about the distinction between evidence and conclusion

Every score or status badge must provide an explanation or tooltip.

---

## 8. Technology stack

### 8.1 Core language and package management

- Python 3.12
- `uv` for dependency management and reproducible environments
- `pyproject.toml` as the single package definition
- Lockfile committed to Git

### 8.2 Data engineering

- Polars for primary dataframe transformations
- DuckDB for analytical SQL and application queries
- Parquet for persisted normalized and aggregated datasets
- PyArrow for Parquet interoperability and schema handling
- Pydantic for configuration and structured domain models
- `orjson` for fast JSON serialization where useful

### 8.3 Network and extraction

- `httpx` for HTTP requests
- `tenacity` for bounded retries
- `selectolax` for HTML parsing
- Beautiful Soup only where selectolax is insufficient
- `pypdf` or PyMuPDF for text-based PDFs
- Playwright only for pages that genuinely require browser rendering
- OCR only as a manually approved last resort

### 8.4 Entity matching and classification

- RapidFuzz for candidate similarity features
- Deterministic normalization and blocking rules
- Configuration-driven aliases and overrides
- No unsupervised automatic merge based only on fuzzy similarity

### 8.5 OpenAI policy extraction

- Official OpenAI Python SDK
- Responses API
- Structured Outputs with a strict JSON Schema
- Pydantic model for the extraction response
- Model selected through `OPENAI_POLICY_MODEL`
- No hard-coded model name in business logic
- Content hashing and cache keys to prevent repeat charges
- No API call when a document hash and extraction version are unchanged

### 8.6 Application

- Streamlit
- Plotly for interactive trends and distributions
- Streamlit caching for read-only app queries
- A separate query/service layer between Streamlit and DuckDB

### 8.7 Developer quality

- pytest
- pytest-cov
- Ruff
- Pyright
- pre-commit
- Typer for the command-line interface
- Structured logging
- GitHub Actions

---

## 9. Execution environment

### 9.1 Codex Cloud role

Codex Cloud is the primary software-development agent. It will:

- Read this specification
- Scaffold the repository
- Implement the pipeline in phases
- Run unit and integration tests
- Inspect data schemas
- Develop the Streamlit application
- Propose pull requests
- Fix defects and respond to review comments

Codex Cloud connects to the private GitHub repository and runs in an isolated cloud environment.

### 9.2 Codex Cloud internet configuration

Phase 1 should use a limited domain allowlist with `GET`, `HEAD`, and `OPTIONS` only where practical.

Initial government-domain list:

- `dol.gov`
- `**.dol.gov`
- `uscis.gov`
- `**.uscis.gov`
- `e-verify.gov`
- `**.e-verify.gov`
- `ice.gov`
- `**.ice.gov`
- `ed.gov`
- `**.ed.gov`
- `nces.ed.gov`
- `nsf.gov`
- `**.nsf.gov`
- `ncses.nsf.gov`

Add dependency domains through the Codex common-dependencies preset.

For policy discovery in Phase 2, the code must enforce its own official-domain restrictions. Codex Cloud access can be expanded only to the selected institution domains or temporarily broadened for development after review.

### 9.3 Secret-handling constraint

Codex Cloud secrets are available to setup scripts but are removed before the agent phase. Therefore:

- Do not rely on Codex Cloud to execute production OpenAI API policy-extraction jobs.
- Codex Cloud may build and test extraction code using mocks and recorded fixtures.
- Real API extraction must run locally or in GitHub Actions using an encrypted repository secret.
- `OPENAI_API_KEY` must never be committed, logged, written to a fixture, or placed in a non-secret Codex environment variable.

### 9.4 Full pipeline runners

Use:

1. **Codex Cloud** for development and non-secret network tests.
2. **Local CLI** for manual full refreshes and debugging.
3. **GitHub Actions** for scheduled government refreshes, policy refreshes, tests, and release artifacts.

---

## 10. High-level architecture

```text
                         AUTHORITATIVE SOURCES
                                  |
        +-------------------------+-------------------------+
        |                         |                         |
     DOL LCA                  USCIS H-1B                 DOL PERM
        |                         |                         |
        +-------------+-----------+-----------+-------------+
                      |                       |
                ICE / SEVP              E-Verify lookup
                      |                       |
                      +-----------+-----------+
                                  |
                            RAW SOURCE LAYER
                                  |
                       schema and checksum checks
                                  |
                          NORMALIZED STAGING
                                  |
                         ENTITY RESOLUTION
                                  |
                    +-------------+-------------+
                    |                           |
               IPEDS identity               HERD research
                    |                           |
                    +-------------+-------------+
                                  |
                        ROLE CLASSIFICATION
                                  |
                         EMPLOYER AGGREGATES
                                  |
                TOP INSTITUTION CANDIDATE SELECTION
                                  |
                   OFFICIAL POLICY DISCOVERY AND FETCH
                                  |
                    OPENAI STRUCTURED EXTRACTION
                                  |
                       HUMAN EVIDENCE REVIEW
                                  |
                        SCORES AND APP VIEWS
                                  |
                          STREAMLIT EXPLORER
```

---

## 11. Repository structure

```text
sponsorship-intelligence-explorer/
|
|-- AGENTS.md
|-- SPEC.md
|-- README.md
|-- CHANGELOG.md
|-- pyproject.toml
|-- uv.lock
|-- Makefile
|-- .env.example
|-- .gitignore
|-- .pre-commit-config.yaml
|
|-- configs/
|   |-- sources.yaml
|   |-- role_taxonomy.yaml
|   |-- entity_resolution.yaml
|   |-- scoring.yaml
|   |-- policy_schema.yaml
|   |-- policy_candidates.yaml
|   `-- institution_overrides.yaml
|
|-- src/
|   `-- sponsor_intel/
|       |-- __init__.py
|       |-- cli.py
|       |-- config.py
|       |-- logging.py
|       |
|       |-- sources/
|       |   |-- base.py
|       |   |-- registry.py
|       |   |-- dol_lca.py
|       |   |-- dol_perm.py
|       |   |-- uscis_h1b.py
|       |   |-- everify.py
|       |   |-- sevp_opt.py
|       |   |-- ipeds.py
|       |   `-- herd.py
|       |
|       |-- normalize/
|       |   |-- common.py
|       |   |-- names.py
|       |   |-- locations.py
|       |   |-- occupations.py
|       |   `-- statuses.py
|       |
|       |-- entities/
|       |   |-- models.py
|       |   |-- candidate_generation.py
|       |   |-- matching.py
|       |   |-- parent_mapping.py
|       |   |-- overrides.py
|       |   `-- review_queue.py
|       |
|       |-- roles/
|       |   |-- taxonomy.py
|       |   |-- classifier.py
|       |   |-- exclusions.py
|       |   `-- validation.py
|       |
|       |-- policies/
|       |   |-- candidate_ranker.py
|       |   |-- discover.py
|       |   |-- fetch.py
|       |   |-- parse.py
|       |   |-- schemas.py
|       |   |-- extract.py
|       |   |-- review.py
|       |   `-- refresh.py
|       |
|       |-- metrics/
|       |   |-- employer_metrics.py
|       |   |-- institution_metrics.py
|       |   |-- trends.py
|       |   `-- coverage.py
|       |
|       |-- scoring/
|       |   |-- stem_opt.py
|       |   |-- h1b.py
|       |   |-- green_card.py
|       |   |-- research.py
|       |   |-- policy.py
|       |   |-- composite.py
|       |   `-- explain.py
|       |
|       |-- db/
|       |   |-- schema.py
|       |   |-- build.py
|       |   |-- migrations.py
|       |   |-- queries.py
|       |   `-- views.py
|       |
|       |-- services/
|       |   |-- explorer.py
|       |   |-- organization_detail.py
|       |   |-- comparison.py
|       |   `-- exports.py
|       |
|       `-- quality/
|           |-- checks.py
|           |-- reports.py
|           `-- issues.py
|
|-- app/
|   |-- Home.py
|   |-- components/
|   |   |-- filters.py
|   |   |-- evidence.py
|   |   |-- score_card.py
|   |   `-- tables.py
|   `-- pages/
|       |-- 1_All_Employers.py
|       |-- 2_Research_Institutions.py
|       |-- 3_Organization_Detail.py
|       |-- 4_Compare.py
|       |-- 5_Evidence_Review.py
|       `-- 6_Data_Health.py
|
|-- sql/
|   |-- schema/
|   |-- transforms/
|   |-- metrics/
|   `-- views/
|
|-- data/
|   |-- raw/
|   |-- staging/
|   |-- resolved/
|   |-- processed/
|   |-- cache/
|   `-- fixtures/
|
|-- db/
|   `-- immigration.duckdb
|
|-- outputs/
|   |-- reports/
|   |-- exports/
|   `-- manifests/
|
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- contracts/
|   |-- regression/
|   `-- fixtures/
|
|-- docs/
|   |-- data_dictionary.md
|   |-- source_registry.md
|   |-- entity_resolution.md
|   |-- role_taxonomy.md
|   |-- scoring.md
|   |-- policy_extraction.md
|   `-- operations.md
|
`-- .github/
    `-- workflows/
        |-- ci.yml
        |-- refresh_government_data.yml
        |-- refresh_policies.yml
        `-- publish_data_release.yml
```

Raw, staging, cache, DuckDB, and large generated artifacts must be ignored by Git. Small test fixtures, schemas, manifests, and manually reviewed override files should be committed.

---

## 12. Source registry

All sources must be represented in `configs/sources.yaml` and exposed through a typed source registry.

Each source configuration must include:

```yaml
id: dol_lca
authority: U.S. Department of Labor
landing_page: https://www.dol.gov/agencies/eta/foreign-labor/performance
minimum_fiscal_year: 2022
refresh_cadence: quarterly
expected_formats:
  - xlsx
  - csv
  - zip
official_domains:
  - dol.gov
  - "*.dol.gov"
partial_year_supported: true
```

Each source adapter must discover current download URLs from the official landing page or a maintained official manifest. Do not rely on an unofficial mirror.

### 12.1 Source meanings and limitations

| Source | Primary use | Meaning | Mandatory limitation |
|---|---|---|---|
| DOL LCA disclosure data | Job-level H-1B history | Employer submitted an LCA and DOL issued a recorded determination | A certified LCA is not an approved H-1B petition |
| USCIS H-1B Employer Data Hub | Petition outcomes and volumes | Employer-level initial and continuing petition approvals or denials | Does not prove sponsorship for a specific title or current posting |
| DOL PERM disclosure data | Employment-based permanent-residence history | Employer used the PERM labor-certification process | Historical activity is not a future promise |
| E-Verify Employer Search | STEM OPT readiness signal | Employer is currently found as enrolled in E-Verify | Does not prove willingness to hire or sponsor an F-1 student |
| ICE/SEVP reports | Positive OPT employment history | Employer appears in a public OPT/STEM OPT report | Public data is incomplete and absence is not negative evidence |
| NCES IPEDS | Institution identity and characteristics | Institution is present in federal postsecondary data | Does not establish immigration eligibility |
| NSF HERD | Research strength | Institution reported separately accounted-for R&D | Research expenditure does not establish hiring or sponsorship |
| Official institution policy | Title/category eligibility | Institution explicitly states internal sponsorship rules | Policy can change and may still be discretionary |

### 12.2 Official source entry points

- DOL foreign labor performance data: <https://www.dol.gov/agencies/eta/foreign-labor/performance>
- USCIS H-1B Employer Data Hub: <https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub>
- E-Verify Employer Search: <https://www.e-verify.gov/e-verify-employer-search>
- ICE SEVIS program and reports: <https://www.ice.gov/sevis>
- NCES IPEDS data: <https://nces.ed.gov/ipeds/use-the-data>
- NSF HERD institution-level microdata: <https://ncses.nsf.gov/explore-data/microdata/higher-education-research-development>
- OpenAI Structured Outputs: <https://developers.openai.com/api/docs/guides/structured-outputs>
- Codex Cloud: <https://developers.openai.com/codex/cloud>
- Codex Cloud internet access: <https://developers.openai.com/codex/cloud/internet-access>

---

## 13. Source-adapter contract

Every source adapter must implement the same conceptual contract:

```python
class SourceAdapter(Protocol):
    def discover(self, context: SourceContext) -> list[SourceArtifactCandidate]: ...
    def download(self, candidate: SourceArtifactCandidate) -> DownloadedArtifact: ...
    def fingerprint(self, artifact: DownloadedArtifact) -> ArtifactFingerprint: ...
    def validate_raw(self, artifact: DownloadedArtifact) -> ValidationResult: ...
    def normalize(self, artifact: DownloadedArtifact) -> NormalizedDataset: ...
    def validate_normalized(self, dataset: NormalizedDataset) -> ValidationResult: ...
    def persist(self, dataset: NormalizedDataset) -> PersistedDataset: ...
```

### 13.1 Required source-artifact metadata

Store:

- Source ID
- Authority
- Landing-page URL
- Direct download URL
- Retrieval timestamp
- Fiscal year
- Fiscal quarter or reporting period
- Whether the period is partial or complete
- File name
- MIME type
- Byte size
- SHA-256 checksum
- Record-layout URL
- Parser version
- Schema version
- Row count
- Column count
- Validation status
- Build ID

### 13.2 Download behavior

- Use streaming downloads.
- Use bounded timeouts.
- Retry only safe idempotent requests.
- Write to a temporary path, verify checksum and file type, then atomically move.
- Refuse HTML error pages saved with spreadsheet extensions.
- Cache unchanged source artifacts.
- Never overwrite a raw artifact with different bytes under the same manifest identity.
- Keep FY2022 onward only, but preserve current partial-year files.
- Mark partial current-year data clearly.

### 13.3 Schema drift

For every source:

- Store expected required columns.
- Maintain aliases for renamed fields by fiscal year.
- Fail the pipeline when required fields disappear.
- Warn when new optional fields appear.
- Generate a machine-readable schema-diff report.
- Require a reviewed parser update before accepting incompatible drift.

---

## 14. Data layers

### 14.1 Raw

Immutable source files exactly as downloaded.

### 14.2 Staging

Source-specific normalized Parquet tables with:

- snake_case columns
- normalized types
- source artifact ID
- raw employer name
- raw title
- fiscal year and reporting period
- no entity merges

### 14.3 Resolved

Records linked to:

- legal entity
- parent organization
- institution where applicable
- role classification
- match confidence
- match method
- manual-review status

### 14.4 Processed

Aggregated employer, institution, title, trend, policy, and score tables.

### 14.5 Presentation

DuckDB views optimized for Streamlit.

No transformation may skip directly from raw data to presentation output.

---

## 15. Core data model

### 15.1 `source_artifacts`

Key fields:

- `source_artifact_id`
- `source_id`
- `authority`
- `landing_page_url`
- `download_url`
- `retrieved_at`
- `fiscal_year`
- `fiscal_quarter`
- `is_partial_period`
- `sha256`
- `bytes`
- `row_count`
- `schema_version`
- `parser_version`
- `validation_status`
- `build_id`

### 15.2 `parent_organizations`

Key fields:

- `parent_organization_id`
- `canonical_name`
- `organization_type`
- `headquarters_state`
- `is_staffing_or_consulting`
- `created_by`
- `review_status`
- `notes`

### 15.3 `legal_entities`

Key fields:

- `legal_entity_id`
- `legal_name`
- `normalized_legal_name`
- `parent_organization_id`
- `city`
- `state`
- `postal_code`
- `country`
- `organization_type`
- `institution_id`
- `created_by`
- `review_status`

### 15.4 `entity_aliases`

Key fields:

- `alias_id`
- `alias_raw`
- `alias_normalized`
- `legal_entity_id`
- `parent_organization_id`
- `source_id`
- `match_method`
- `match_score`
- `review_status`

### 15.5 `institutions`

Key fields:

- `institution_id`
- `ipeds_unitid`
- `official_name`
- `system_name`
- `control`
- `sector`
- `city`
- `state`
- `official_domain`
- `highest_degree`
- `active_status`
- `legal_entity_id`
- `parent_organization_id`
- `match_confidence`
- `review_status`

### 15.6 `lca_cases`

Key fields:

- `case_id`
- `source_artifact_id`
- `fiscal_year`
- `case_status`
- `employer_name_raw`
- `legal_entity_id`
- `parent_organization_id`
- `job_title_raw`
- `job_title_normalized`
- `soc_code`
- `soc_title`
- `role_family`
- `technical_role`
- `role_confidence`
- `worksite_city`
- `worksite_state`
- `wage_from`
- `wage_to`
- `wage_unit`
- `full_time`
- `employment_start_date`
- `employment_end_date`

### 15.7 `h1b_petition_summaries`

Key fields:

- `source_artifact_id`
- `fiscal_year`
- `employer_name_raw`
- `legal_entity_id`
- `parent_organization_id`
- `initial_approvals`
- `initial_denials`
- `continuing_approvals`
- `continuing_denials`
- `naics_code`
- `state`
- `city`
- `zip_code`

### 15.8 `perm_cases`

Key fields:

- `case_id`
- `source_artifact_id`
- `fiscal_year`
- `case_status`
- `decision_date`
- `employer_name_raw`
- `legal_entity_id`
- `parent_organization_id`
- `job_title_raw`
- `job_title_normalized`
- `soc_code`
- `soc_title`
- `role_family`
- `technical_role`
- `role_confidence`
- `worksite_city`
- `worksite_state`
- `minimum_education`
- `major_field`
- `experience_required`
- `foreign_worker_education` only if public and appropriate
- `priority_date` only if present in the public source
- `case_outcome_group`

### 15.9 `opt_observations`

Key fields:

- `source_artifact_id`
- `observation_year`
- `employer_name_raw`
- `legal_entity_id`
- `parent_organization_id`
- `program_type`
- `reported_count`
- `rank`
- `is_positive_observation`
- `coverage_note`

Absence from this table must never be interpreted as a negative.

### 15.10 `everify_observations`

Key fields:

- `lookup_id`
- `queried_name`
- `legal_entity_id`
- `parent_organization_id`
- `enrollment_status`
- `enrollment_date`
- `workforce_size`
- `hiring_site_count`
- `state`
- `matched_name`
- `retrieved_at`
- `match_confidence`
- `review_status`
- `source_url`

Use statuses:

- `CONFIRMED_ACTIVE`
- `CONFIRMED_INACTIVE`
- `NO_MATCH`
- `AMBIGUOUS`
- `NOT_CHECKED`
- `ERROR`

`NO_MATCH` is not equivalent to `CONFIRMED_INACTIVE`.

### 15.11 `herd_observations`

Key fields:

- `institution_id`
- `survey_year`
- `total_rd`
- `federal_rd`
- `business_funded_rd`
- `institution_funded_rd`
- `computer_information_sciences_rd`
- `engineering_rd`
- `rd_personnel` where available
- `source_artifact_id`

### 15.12 `policy_documents`

Key fields:

- `policy_document_id`
- `institution_id`
- `document_type`
- `title`
- `url`
- `official_domain`
- `retrieved_at`
- `http_status`
- `content_type`
- `content_sha256`
- `text_sha256`
- `published_or_updated_date`
- `raw_path`
- `parsed_text_path`
- `is_current`
- `parse_status`

### 15.13 `policy_facts`

Key fields:

- `policy_fact_id`
- `institution_id`
- `policy_document_id`
- `fact_type`
- `fact_value`
- `qualifier`
- `supporting_excerpt`
- `section_or_page`
- `source_url`
- `retrieved_at`
- `extractor_version`
- `model_name`
- `model_response_id`
- `confidence`
- `human_review_status`
- `reviewer_note`
- `contradiction_group_id`

### 15.14 `employer_metrics`

Key fields:

- Counts by FY and rolling window
- Relevant LCA counts
- Relevant certified PERM counts
- USCIS approval and denial totals
- Active years
- Last observed activity
- Relevant title distribution
- Role-family distribution
- State distribution
- OPT positive observation
- E-Verify status
- Source coverage
- Metric version

### 15.15 `employer_scores`

Key fields:

- `parent_organization_id`
- `score_version`
- `stem_opt_score`
- `h1b_history_score`
- `green_card_history_score`
- `policy_support_score`
- `research_strength_score`
- `immigration_evidence_score`
- `research_pathway_score`
- `score_confidence`
- `coverage_ratio`
- `explanation_json`
- `calculated_at`

### 15.16 `data_quality_issues`

Key fields:

- `issue_id`
- `build_id`
- `severity`
- `category`
- `source_id`
- `record_reference`
- `message`
- `details_json`
- `created_at`
- `resolved_at`
- `resolution_note`

---

## 16. Entity resolution

Entity resolution is the highest-risk component of the project.

### 16.1 Mandatory identity layers

The system must maintain:

```text
raw employer name
        |
     legal entity
        |
 parent organization
```

Example:

```text
Amazon.com Services LLC --------\
Amazon Web Services Inc. --------> Amazon
Amazon Development Center U.S. -/
```

The underlying immigration evidence must remain attached to the legal entity that appears in the source. Parent-level aggregation is a derived view.

### 16.2 Normalization

Candidate-generation normalization may:

- Unicode-normalize
- uppercase
- collapse whitespace
- standardize punctuation
- standardize common abbreviations
- standardize legal suffixes for matching features
- normalize city, state, and ZIP
- produce token and acronym forms

It must never destroy or overwrite the raw name.

### 16.3 Match features

Use:

- Exact normalized-name equality
- Name-token similarity
- Address agreement
- City and state agreement
- ZIP agreement
- Repeated co-occurrence across sources
- IPEDS official name and aliases
- Known university-system relationships
- Manually reviewed alias tables
- Distinctive domain information where available

### 16.4 Match statuses

- `DETERMINISTIC`
- `HIGH_CONFIDENCE_AUTO`
- `REVIEW_REQUIRED`
- `UNRESOLVED`
- `REJECTED`
- `MANUAL_OVERRIDE`

### 16.5 Default threshold policy

Thresholds must be configurable and calibrated. Initial defaults:

- Deterministic exact match with no conflicting known legal-employer location: accept
- Exact normalized name with a conflicting known legal-employer location: review required and remain unmerged
- Candidate score at least 0.97, unique candidate, location agreement, and margin of at least 0.05: high-confidence auto-match
- Score from 0.80 to below 0.97: review required
- Score below 0.80: unresolved

No parent-organization merge may be made solely from fuzzy name similarity.

### 16.6 Manual overrides

Use committed review files:

```yaml
aliases:
  - raw_name: "THE UNIVERSITY OF MICHIGAN"
    legal_entity_id: "..."
    reviewed_by: "mitch"
    reviewed_at: "2026-..."
    reason: "Verified official entity and location"

rejections:
  - raw_name: "UT SYSTEM"
    candidate_legal_entity_id: "..."
    reason: "System entity is not interchangeable with campus entity"
```

Manual review must be auditable.

### 16.7 Entity-resolution validation

Create a gold dataset containing at least:

- 50 large technology employers
- 50 universities
- 25 university systems
- 25 hospitals or medical centers
- 25 research institutes or national-lab operators
- 25 staffing/consulting organizations

Acceptance targets:

- At least 99 percent precision for auto-accepted legal-entity matches
- Zero known parent/legal-entity collapses in the gold set
- Every ambiguous case routed to review
- Regression tests for all reviewed aliases

---

## 17. Role classification

### 17.1 Output

Every LCA and PERM record must receive:

- `technical_role`
- `role_family`
- `role_confidence`
- `classification_method`
- `classification_version`
- `review_status`

### 17.2 Role families

Use:

- `software_engineering`
- `research_software`
- `research_engineering`
- `machine_learning_ai`
- `data_engineering`
- `data_science`
- `systems_infrastructure`
- `hpc`
- `cloud`
- `devops_sre`
- `computer_science_research`
- `technical_management_related`
- `other_computing`
- `not_relevant`
- `ambiguous`

### 17.3 Classification order

1. Exact reviewed title override
2. Evaluate strong positive and strong exclusion title evidence
3. Strong exclusion before broad SOC-only inclusion unless the complete title independently establishes technical work
4. SOC-code family mapping
5. Strong positive title patterns when SOC evidence did not decide the role
6. Combined SOC plus title rules
7. Ambiguous review queue

Do not use an LLM for bulk classification in V1.

### 17.4 Important exclusions

Exclude or flag:

- Generic `Research Scientist` without computing evidence
- Generic `Engineer` without computing evidence
- Medical residents and physicians
- Biological or chemical research
- Faculty outside computing
- Sales engineering
- Recruiters
- Helpdesk and low-level IT support
- Business analysts without technical evidence
- Project managers without technical evidence
- Generic managers
- Internships, if the purpose is permanent employer behavior and the source record is not relevant
- Staffing placements where the petitioning entity is a staffing firm, unless explicitly included by filter

### 17.5 Validation

Create a manually labeled sample of at least 750 records across all source years and employer types.

Acceptance targets:

- At least 95 percent precision for `technical_role = true`
- At least 90 percent recall for the agreed target universe
- At least 90 percent correct role-family assignment
- All low-confidence cases visible in the review interface

---

## 18. E-Verify ingestion strategy

Do not assume a bulk E-Verify export exists.

### 18.1 V1 strategy

1. Build the employer universe from DOL, USCIS, IPEDS, and HERD first.
2. Prioritize E-Verify lookups for:
   - Employers with relevant H-1B or PERM activity
   - Top institutions
   - Employers manually requested by the user
3. Use the official E-Verify Employer Search.
4. Cache results.
5. Apply strict rate limiting.
6. Store lookup evidence and retrieved date.
7. Route ambiguous results to review.
8. Recheck prioritized employers quarterly.

### 18.2 Prohibited behavior

- Do not hammer the search endpoint.
- Do not bypass access controls.
- Do not infer active enrollment from an old screenshot or third-party site.
- Do not mark `NO_MATCH` as `NO`.
- Do not match solely on a short employer name.

---

## 19. OPT and STEM OPT evidence

Public SEVP employer information is incomplete.

### 19.1 Data treatment

Use only positive observations such as:

- Employer appeared in an official top-employer report
- Employer count was published
- Employer rank was published

Represent absence as `UNKNOWN`.

### 19.2 Display language

Allowed:

- "Observed in official OPT employer data for 2024."
- "Positive historical OPT evidence."

Not allowed:

- "Does not hire OPT students" based only on absence.
- "OPT sponsor" without qualification.

---

## 20. University and research-institution policy pipeline

### 20.1 Candidate selection

After government data, IPEDS, HERD, entity resolution, and role classification are stable, rank institutions for policy enrichment.

Select the top 200 by default, configurable from 150 to 250, using:

- Relevant H-1B LCA volume
- Relevant PERM volume
- Recent activity
- HERD total R&D
- Computing R&D
- Engineering R&D
- Positive OPT evidence
- E-Verify confirmation
- Institution type
- Manual priority list

Candidate selection is not the final score.

### 20.2 Policy document types

Discover:

- H-1B sponsorship policy
- Permanent-residence sponsorship policy
- International scholar policy
- Faculty immigration policy
- Research staff immigration policy
- General staff immigration policy
- Postdoctoral appointment immigration policy
- Employment-based permanent-residence procedures
- EB-1B guidance
- PERM guidance
- Appointment eligibility tables
- Department administrator guidance
- University-system policy

### 20.3 Source restrictions

Policy facts may only be extracted from:

- The official institution domain
- The official university-system domain
- An official affiliated international office
- An official HR policy document
- An official PDF hosted by the institution

Third-party attorney summaries, forums, social media, aggregators, and search-result snippets are not acceptable evidence.

### 20.4 Discovery procedure

1. Start from the official domain stored in IPEDS.
2. Inspect `robots.txt` and sitemaps where available.
3. Search known paths and on-site search interfaces.
4. Use keyword combinations:
   - H-1B sponsorship
   - permanent residence
   - permanent residency
   - green card sponsorship
   - employment-based permanent residence
   - research staff immigration
   - staff immigration
   - international scholars
   - EB-1B
   - PERM
5. Use OpenAI web search only as a bounded fallback for finding official-domain pages.
6. Fetch the actual source page before extraction.
7. Store the raw page or PDF, parsed text, hash, and retrieval metadata.

### 20.5 Text parsing

- HTML: select main content, remove navigation and boilerplate.
- PDF: extract native text first.
- Office documents: parse with a format-specific library.
- OCR: only after manual approval when no text layer exists.
- Preserve headings, page numbers, and section positions when possible.

### 20.6 Structured extraction schema

Each institution extraction must return a strict schema containing:

```json
{
  "institution_name": "string",
  "facts": [
    {
      "fact_type": "h1b_research_staff_eligible",
      "value": "YES | NO | LIMITED | UNKNOWN | NOT_STATED",
      "qualifier": "string or null",
      "supporting_excerpt": "string",
      "section_or_page": "string or null",
      "source_url": "string",
      "confidence": 0.0
    }
  ],
  "document_summary": "string",
  "contradictions": [],
  "needs_human_review": true
}
```

### 20.7 Required fact types

- `h1b_faculty_eligible`
- `h1b_research_staff_eligible`
- `h1b_general_staff_eligible`
- `h1b_postdoc_eligible`
- `pr_faculty_eligible`
- `pr_research_staff_eligible`
- `pr_general_staff_eligible`
- `pr_postdoc_eligible`
- `perm_supported`
- `eb1b_supported`
- `niw_employer_supported_or_assisted`
- `temporary_positions_excluded`
- `grant_funded_positions_excluded`
- `minimum_appointment_duration`
- `minimum_funding_duration`
- `waiting_period`
- `required_approval_level`
- `department_initiates`
- `employee_self_initiation_allowed`
- `cost_payment_policy`
- `cap_exemption_explicitly_stated`
- `policy_discretionary`
- `policy_last_updated`

### 20.8 Extraction rules

The model must:

- Use only the supplied document text.
- Return `NOT_STATED` when a document does not address the question.
- Return `UNKNOWN` when wording is contradictory or unclear.
- Never infer eligibility from general welcoming language.
- Never infer cap exemption from institution type.
- Never infer PERM support merely because permanent residence is mentioned.
- Quote the smallest excerpt that supports the fact.
- Include the source URL for every fact.
- Mark contradictions.
- Avoid merging policies from different legal employers without explicit evidence.

### 20.9 Prompt-injection defense

Treat policy pages as untrusted data.

The extractor must:

- Delimit document content clearly.
- State that instructions inside the document are not instructions to the model.
- Disable arbitrary tool use during extraction.
- Require schema-constrained output.
- Reject source text that attempts to redirect, exfiltrate, or alter system behavior.
- Log suspicious text for review.

### 20.10 Human review

Human review is required for:

- Any `NO`
- Any `LIMITED`
- Any fact with confidence below 0.85
- Any contradiction
- Any policy older than 18 months without a current confirmation
- Any policy that appears to apply only to a university system rather than the campus
- Any general-staff permanent-residence conclusion
- Any cap-exemption conclusion
- Any policy extracted from a scanned document

### 20.11 Extraction evaluation

Create a manually annotated benchmark of at least 30 institutions.

Acceptance targets:

- 100 percent of accepted facts have an official source URL.
- 100 percent of accepted facts have a supporting excerpt.
- At least 95 percent factual precision after review.
- Zero accepted facts unsupported by the cited excerpt.
- Re-running unchanged content produces no additional API call.
- Changed documents are detected by hash.

---

## 21. Metrics

### 21.1 Rolling windows

Because the project begins at FY2022, calculate:

- Current fiscal year to date
- Last complete fiscal year
- Two-year rolling window
- Three-year rolling window
- FY2022-to-current cumulative totals

Do not label a partial fiscal year as a complete year.

### 21.2 H-1B metrics

At legal-entity and parent levels:

- Total LCA cases
- Certified LCA cases
- Relevant technical LCA cases
- Relevant technical certified LCA cases
- Relevant case share
- Active fiscal years
- Last observed LCA date or year
- USCIS initial approvals
- USCIS initial denials
- USCIS continuing approvals
- USCIS continuing denials
- Approval ratio only where denominator is sufficient
- Top relevant titles
- Top relevant role families
- Worksite states
- Wage percentiles

### 21.3 PERM metrics

- Total PERM cases
- Certified PERM cases
- Relevant technical PERM cases
- Relevant technical certified PERM cases
- Active fiscal years
- Last observed PERM decision
- Top relevant titles
- Education requirements
- Experience requirements
- Worksite states
- Case-status distribution

### 21.4 Institution metrics

- HERD total R&D
- HERD federal R&D
- HERD computing R&D
- HERD engineering R&D
- Percentile ranks
- Relevant H-1B cases
- Relevant PERM cases
- Policy coverage
- E-Verify status
- Potential or verified cap-exemption status

### 21.5 Confidence and coverage

Every aggregate must include:

- Number of contributing source records
- Source coverage
- Entity-match coverage
- Role-classification coverage
- Policy-review status
- Last updated
- Whether current FY is partial

---

## 22. Scoring

Scores must be implemented only after the raw metrics pass validation.

All formulas must live in `configs/scoring.yaml`, have a version, and be explained in `docs/scoring.md`.

### 22.1 General rules

- Use log scaling for case volumes.
- Use recency and active-year consistency.
- Do not let one very large historical year dominate.
- Do not convert missing evidence into zero unless an authoritative negative exists.
- Expose score coverage and confidence.
- Preserve raw metrics next to scores.
- Never call the final score "chance of sponsorship."
- Display scores as evidence-strength indicators.

### 22.2 STEM OPT readiness score

Proposed logic:

- Confirmed active E-Verify: strong positive basis
- Confirmed inactive E-Verify: explicit negative for current STEM OPT readiness
- Positive recent OPT/STEM OPT observation: additional positive evidence
- No E-Verify match: unknown, not zero
- Ambiguous match: no score until review

Output:

- `score`: nullable 0 to 100
- `status`: `STRONG`, `MODERATE`, `LIMITED`, `UNKNOWN`, or `EXPLICIT_BLOCKER`
- `confidence`
- `explanation`

### 22.3 H-1B history score

Components:

- Recent USCIS initial approvals
- Relevant certified LCA volume
- Number of active fiscal years
- Recency
- Relevant technical share
- Denial information with minimum-volume safeguards

Potential cap exemption must be shown as a separate status, not silently mixed into history.

### 22.4 Green-card history score

Components:

- Relevant certified PERM volume
- Active fiscal years
- Recency
- Relevant technical share
- Exact-title repetition
- Institution policy support, displayed as a separate component

No PERM record means `UNKNOWN` or weak observed history, not a categorical refusal.

### 22.5 Research strength score

For institutions with HERD data:

- Total R&D percentile
- Computing R&D percentile
- Engineering R&D percentile
- Federal R&D percentile

If detailed field data is unavailable because of short-form reporting, show coverage limits.

### 22.6 Policy support score

Map reviewed official policy facts:

- Explicit research-staff H-1B eligibility
- Explicit research-staff permanent-residence eligibility
- General-staff eligibility
- PERM support
- EB-1B support
- Temporary or grant-funded exclusions
- Waiting periods
- Required appointment duration
- Discretionary language

Unknown or not stated must not be scored as no.

### 22.7 Composite scores

#### Immigration evidence score

For all employers:

```text
20% STEM OPT readiness
35% H-1B history
45% green-card history
```

Reweight only when components are genuinely not applicable, not merely missing. Otherwise show insufficient coverage.

#### Research pathway score

For universities and research institutions:

```text
45% immigration evidence
25% research strength
30% reviewed policy support
```

The interface must show component values and the score version.

### 22.8 Grade bands

Suggested display:

- 90 to 100: A+
- 80 to 89: A
- 70 to 79: B
- 60 to 69: C
- 40 to 59: D
- Below 40: F
- Insufficient coverage: Unknown

These are product labels, not legal assessments.

---

## 23. Cap-exemption representation

Use:

- `VERIFIED_CAP_EXEMPT`
- `POTENTIALLY_CAP_EXEMPT_HIGHER_ED`
- `POTENTIALLY_CAP_EXEMPT_AFFILIATED_NONPROFIT`
- `POTENTIALLY_CAP_EXEMPT_RESEARCH_ORG`
- `NOT_CAP_EXEMPT`
- `UNKNOWN`

Rules:

- An IPEDS institution can be marked `POTENTIALLY_CAP_EXEMPT_HIGHER_ED`.
- `VERIFIED_CAP_EXEMPT` requires explicit official evidence or a reviewed legal classification.
- A nonprofit or hospital name alone is insufficient.
- The UI must display the difference between potential and verified.
- Cap exemption is attached to the legal petitioner, not merely the parent brand.

---

## 24. DuckDB and Parquet outputs

Required outputs:

```text
data/processed/
  parent_organizations.parquet
  legal_entities.parquet
  institutions.parquet
  lca_cases_resolved.parquet
  h1b_petitions_resolved.parquet
  perm_cases_resolved.parquet
  opt_observations.parquet
  everify_observations.parquet
  herd_observations.parquet
  policy_documents.parquet
  policy_facts.parquet
  employer_metrics.parquet
  institution_metrics.parquet
  employer_scores.parquet

db/
  immigration.duckdb
```

Required DuckDB presentation views:

- `vw_employer_explorer`
- `vw_institution_explorer`
- `vw_organization_detail`
- `vw_h1b_trends`
- `vw_perm_trends`
- `vw_relevant_titles`
- `vw_policy_evidence`
- `vw_entity_review_queue`
- `vw_policy_review_queue`
- `vw_data_health`

---

## 25. Command-line interface

Use Typer.

Required commands:

```bash
uv run sponsor-intel sources list
uv run sponsor-intel sources discover --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_lca --from-fy 2022
uv run sponsor-intel ingest --source dol_perm --from-fy 2022
uv run sponsor-intel ingest --source uscis_h1b --from-fy 2022
uv run sponsor-intel ingest --source ipeds
uv run sponsor-intel ingest --source herd
uv run sponsor-intel entities resolve
uv run sponsor-intel roles classify
uv run sponsor-intel everify lookup --priority top
uv run sponsor-intel metrics build
uv run sponsor-intel policies rank-candidates --limit 200
uv run sponsor-intel policies discover
uv run sponsor-intel policies fetch
uv run sponsor-intel policies extract
uv run sponsor-intel policies review-export
uv run sponsor-intel scores build
uv run sponsor-intel db build
uv run sponsor-intel quality report
uv run sponsor-intel refresh government
uv run sponsor-intel refresh policies
uv run sponsor-intel app
```

Makefile aliases may include:

```bash
make setup
make test
make lint
make typecheck
make ingest-government
make build-db
make refresh
make app
```

---

## 26. Configuration

### 26.1 `.env.example`

```dotenv
OPENAI_API_KEY=
OPENAI_POLICY_MODEL=
SPONSOR_INTEL_DATA_DIR=./data
SPONSOR_INTEL_DB_PATH=./db/immigration.duckdb
SPONSOR_INTEL_LOG_LEVEL=INFO
POLICY_CANDIDATE_LIMIT=200
```

### 26.2 No secrets in configuration files

- `.env` ignored
- GitHub Actions secret: `OPENAI_API_KEY`
- Never print secrets
- Never write API request headers to logs
- Redact environment values in error reports

### 26.3 Configuration precedence

1. CLI arguments
2. Environment variables
3. YAML configuration
4. Safe defaults

---

## 27. Testing strategy

### 27.1 Unit tests

Test:

- Name normalization
- Location normalization
- Source schema mapping
- Role patterns
- Exclusion patterns
- Score components
- Policy schema validation
- Hashing and cache behavior
- Partial-year handling

### 27.2 Contract tests

For each official source:

- Landing page reachable
- Expected artifact discoverable
- File type valid
- Required columns present
- Minimum row-count sanity check
- Record-layout link or schema reference present where applicable

Contract tests should be allowed to skip in offline CI and run in a scheduled network-enabled workflow.

### 27.3 Integration tests

Use small official-data samples or sanitized fixtures to test:

- Ingestion through Parquet
- Entity resolution
- Role classification
- Metrics build
- DuckDB view creation
- Streamlit query services
- Policy extraction with mocked OpenAI output
- Policy extraction with an optional live test in a protected workflow

### 27.4 Regression tests

Every corrected:

- Entity match
- Parent mapping
- Role classification
- Policy fact
- Source schema change

must receive a regression test or committed reviewed override.

### 27.5 Data-quality tests

Examples:

- Case IDs are unique within source scope.
- Fiscal year is at least 2022.
- Current partial year is marked partial.
- Every resolved case has match metadata.
- Every role classification has a version.
- Every policy fact has evidence.
- Every accepted policy source is on an approved official domain.
- No `UNKNOWN` is rendered as `NO`.
- Parent totals equal the sum of linked legal entities after deduplication.
- No source artifact changes checksum without a new artifact version.

---

## 28. Continuous integration and refresh

### 28.1 `ci.yml`

On every pull request:

- `uv sync --frozen`
- Ruff format check
- Ruff lint
- Pyright
- Unit tests
- Integration tests using fixtures
- Coverage report
- Build a fixture DuckDB
- Smoke-test Streamlit imports

### 28.2 Government refresh workflow

Run quarterly and manually.

Steps:

1. Discover current official artifacts.
2. Download FY2022 onward only.
3. Validate checksums and schemas.
4. Normalize.
5. Resolve entities using existing reviewed mappings.
6. Classify roles.
7. Build metrics.
8. Build DuckDB.
9. Run quality gates.
10. Generate data-quality report.
11. Publish versioned data artifacts if all critical checks pass.
12. Do not publish on failed critical checks.

### 28.3 Policy refresh workflow

Run every four months by default and manually.

Steps:

1. Re-rank candidate institutions.
2. Discover official policy URLs.
3. Fetch only changed documents.
4. Parse text.
5. Call OpenAI only for changed or new documents.
6. Validate Structured Output.
7. Route required facts to human review.
8. Publish only reviewed facts to the main application dataset.
9. Preserve old facts with validity dates.

### 28.4 Data release strategy

Do not commit large DuckDB or raw files to Git.

Publish successful full builds as private GitHub Release assets:

- `immigration.duckdb`
- processed Parquet bundle
- source manifest
- checksums
- data-quality report
- build metadata

Tag format:

```text
data-YYYY-MM-DD
```

Local app startup may use an already built local database. Automatic download of the latest private release is optional and can be added after V1.

---

## 29. Logging and observability

Use structured logs with:

- Timestamp
- Build ID
- Source ID
- Artifact ID
- Stage
- Record count
- Duration
- Status
- Error category

Do not log:

- API keys
- Full request headers
- Entire policy documents
- Sensitive environment variables

Generate one build summary containing:

- Sources processed
- New artifacts
- Unchanged artifacts
- Rows ingested
- Rows rejected
- Entity match rates
- Role-classification rates
- Policy extraction counts
- API cost estimate
- Quality-gate results

---

## 30. Performance targets

For a locally built V1 database:

- App startup after cache: under 5 seconds
- Employer table filter response: under 2 seconds
- Organization detail query: under 1 second
- Comparison query: under 1 second
- Export of a filtered employer table: under 10 seconds
- No full raw-data scan from Streamlit
- All user-facing queries run against processed tables or presentation views

Targets are measured on a typical modern laptop and may be adjusted after the first full build.

---

## 31. Implementation phases

Codex must implement one phase per pull request unless the phase is very small. No giant end-to-end pull request.

### Phase 0: Repository foundation

Deliver:

- Repository structure
- `SPEC.md`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `uv.lock`
- CLI skeleton
- Configuration loading
- Structured logging
- CI
- Minimal Streamlit shell
- Test fixtures

Acceptance:

- `uv sync --frozen` works
- `make test` works
- `make lint` works
- Streamlit shell starts
- No source ingestion yet

### Phase 1: DOL LCA and PERM ingestion

Deliver:

- Source discovery
- Raw download manifests
- FY2022 onward filters
- Schema normalization
- Parquet outputs
- Data-quality checks
- Contract tests

Acceptance:

- All available FY2022 onward artifacts ingest reproducibly
- Required columns validated
- Partial current FY labeled
- No entity merging yet

### Phase 2: USCIS H-1B, IPEDS, and HERD

Deliver:

- USCIS H-1B adapter
- IPEDS adapter
- HERD adapter
- Institution table
- Source registry documentation

Acceptance:

- Institution identities preserve UNITID
- HERD joins are explicit and reviewed
- USCIS and DOL records remain separate evidence types

### Phase 3: Entity resolution

Deliver:

- Normalization
- Candidate blocking
- Match scoring
- Legal-entity registry
- Parent-organization registry
- Manual review queue
- Reviewed override files
- Gold-set validation

Acceptance:

- Auto-match precision target met
- Parent/legal separation verified
- Top known employers and institutions manually inspected
- Ambiguous matches unresolved rather than guessed

### Phase 4: Role classification

Deliver:

- YAML taxonomy
- SOC mappings
- Title rules
- Exclusions
- Validation set
- Review queue

Acceptance:

- Precision and recall targets met
- Relevant case counts look plausible
- Generic research and medical false positives controlled

### Phase 5: Metrics and raw explorer

Deliver:

- Employer metrics
- Institution metrics
- DuckDB views
- All Employers page
- Research Institutions page
- Organization Detail page
- Export

Acceptance:

- Raw metrics visible before scoring
- Evidence classes visible
- Current partial FY warnings visible
- App queries meet performance targets

### Phase 6: E-Verify and OPT positive evidence

Deliver:

- Prioritized E-Verify lookup
- Cache
- Rate limiting
- Review queue
- OPT report ingestion
- Positive-only semantics

Acceptance:

- No-match is not displayed as no
- Ambiguous employer names require review
- Lookup evidence and retrieval date visible

### Phase 7: Institution policy pipeline

Deliver:

- Candidate ranker
- Official-domain discovery
- Document fetch and parsing
- OpenAI Structured Output extraction
- Content-hash cache
- Human review queue
- Benchmark and evaluation

Acceptance:

- Top 200 candidates generated
- Every accepted fact has official evidence
- Extraction precision target met
- API calls are skipped for unchanged documents
- Real API execution works locally or in GitHub Actions

### Phase 8: Scoring and comparison

Deliver:

- Versioned score configuration
- Component scores
- Confidence and coverage
- Explanations
- Compare page
- Score documentation

Acceptance:

- No score hides missing coverage
- Raw evidence remains visible
- Score outputs reproduce from configuration
- No score is described as a legal probability

### Phase 9: Operations and hardening

Deliver:

- Scheduled workflows
- Private release artifacts
- Data health page
- Operations documentation
- Failure recovery
- Changelog
- Full acceptance run

Acceptance:

- A clean environment can reproduce the build
- A scheduled refresh can complete
- Failed quality gates block publication
- V1 definition of done is satisfied

---

## 32. V1 definition of done

V1 is complete only when:

1. The private repository is reproducible from a clean environment.
2. FY2022 onward DOL LCA, DOL PERM, and USCIS H-1B data are ingested.
3. IPEDS and HERD are joined with reviewed institution mappings.
4. Legal entities and parent organizations remain separate.
5. Relevant technical roles are classified at the agreed quality level.
6. The app can search and filter all employers.
7. The app has a dedicated research-institution view.
8. Employer detail pages show raw evidence and trends.
9. E-Verify is checked for prioritized employers with correct uncertainty semantics.
10. Positive OPT observations are displayed without treating absence as negative.
11. The top 150 to 250 institutions are eligible for policy enrichment.
12. At least 100 institutions have reviewed policy evidence in V1.
13. Every accepted policy fact has an official URL and excerpt.
14. Separate component scores and confidence are shown.
15. The app has no job-tracker features.
16. Data quality and freshness are visible.
17. A full build produces a DuckDB database and processed Parquet outputs.
18. CI passes.
19. A quarterly refresh workflow exists.
20. Documentation explains all important limitations.

---

## 33. Critical prohibitions for Codex

Codex must not:

- Create a binary `sponsors` column.
- Treat missing records as a refusal.
- Treat E-Verify as sponsorship willingness.
- Treat an LCA as an approved H-1B petition.
- Treat historical PERM as a promise.
- Declare cap exemption from a name or nonprofit label.
- Merge legal entities based only on fuzzy name similarity.
- Collapse university systems into campuses.
- Mix parent-level and legal-entity-level evidence without attribution.
- Let an LLM perform silent entity resolution.
- Let an LLM classify millions of titles in V1.
- Accept policy facts without exact supporting evidence.
- Use third-party sponsorship databases as authoritative sources.
- Use Kaggle or unofficial mirrors when an official source exists.
- Commit raw government datasets.
- Commit secrets.
- run real OpenAI API extraction from Codex Cloud using an exposed key.
- Build job tracking.
- Build a public API.
- Add Postgres or Supabase before V1 requires it.
- Add a polished frontend before data quality is validated.
- hide source failures behind cached outputs.
- compare partial FY counts to complete FY counts without a warning.

---

## 34. `AGENTS.md` requirements

The repository must include an `AGENTS.md` containing at least:

```markdown
# Codex instructions

1. Read SPEC.md before changing architecture or behavior.
2. Implement one approved phase at a time.
3. Do not broaden scope without explicit approval.
4. Use only authoritative source domains for production ingestion.
5. Preserve raw names and source provenance.
6. Never turn missing evidence into a negative conclusion.
7. Keep legal entities and parent organizations separate.
8. Route ambiguous entity matches to review.
9. Add tests for every parser, rule, override, and fixed defect.
10. Run Ruff, Pyright, and pytest before finishing.
11. Do not commit raw data, generated databases, API keys, or secrets.
12. Do not use OpenAI API calls in ordinary tests.
13. Use fixtures and mocks for extraction tests.
14. Summarize schema changes and data-quality effects in every pull request.
15. Stop and report when an official source schema changes unexpectedly.
```

---

## 35. Initial Codex Cloud setup

### 35.1 Repository

Create a private repository named:

```text
sponsorship-intelligence-explorer
```

### 35.2 Codex environment setup script

Initial setup:

```bash
set -euo pipefail

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv sync --frozen
```

After the lockfile exists, Codex should use the locked environment.

### 35.3 Maintenance script

```bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
uv sync --frozen
```

### 35.4 Environment variables

Safe non-secret environment variables may include:

```text
SPONSOR_INTEL_DATA_DIR=/tmp/sponsor-intel-data
SPONSOR_INTEL_DB_PATH=/tmp/sponsor-intel-data/immigration.duckdb
SPONSOR_INTEL_LOG_LEVEL=INFO
```

Do not place `OPENAI_API_KEY` in a persistent non-secret environment variable.

---

## 36. First Codex task

After committing this specification, give Codex this task:

> Read `SPEC.md` in full and implement **Phase 0 only**.  
>  
> Create the repository foundation, Python package, `uv` configuration, CLI skeleton, typed configuration loader, structured logging, test layout, CI workflow, minimal Streamlit multipage shell, `AGENTS.md`, and documentation stubs.  
>  
> Do not ingest any external source yet. Do not add Postgres, Supabase, a public API, job tracking, scraping, scoring, or OpenAI API calls.  
>  
> Add tests for configuration, CLI startup, package imports, and the Streamlit query-service boundary. Run Ruff, Pyright, and pytest.  
>  
> At completion, report:
> 1. files created,
> 2. commands to run,
> 3. tests executed,
> 4. architecture assumptions,
> 5. anything in `SPEC.md` that is technically blocked or ambiguous.
>  
> Keep the pull request small and reviewable.

---

## 37. Risks and mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Bad entity resolution | Confident but false employer rankings | High auto-match precision, legal/parent separation, review queue, gold set |
| Source schema drift | Silent corruption | Contract tests, schema manifests, fail closed |
| Incomplete OPT data | False negative conclusions | Positive-only semantics |
| E-Verify matching ambiguity | Wrong STEM OPT status | Priority lookups, location matching, review |
| University policy ambiguity | False eligibility claims | Strict extraction schema, evidence excerpts, human review |
| Prompt injection in policy pages | Unsafe extraction behavior | Untrusted-content handling, no tool use, strict outputs |
| Score overconfidence | Misleading decisions | Raw metrics, confidence, coverage, versioned formulas |
| Partial FY comparisons | Misleading trends | Partial-period labels and warnings |
| Large raw datasets | Repository bloat | Raw data ignored, release assets, Parquet |
| API cost growth | Expensive policy refresh | Hash cache, candidate limit, changed-doc extraction only |
| Codex scope creep | Delayed useful output | Phase gates and `AGENTS.md` |
| University/legal-entity confusion | Wrong cap-exemption or policy | Campus, system, operator, and legal-entity separation |

---

## 38. Assumptions that may be changed later

These are defaults, not permanent architectural constraints:

1. Streamlit is the V1 interface.
2. The app is local-first.
3. The policy candidate limit is 200.
4. Processed datasets are published as private GitHub Release assets.
5. The OpenAI extraction model is configured at runtime.
6. A future frontend may replace Streamlit.
7. A future service or Postgres layer may be added only when another consumer exists.
8. Current job-posting integration remains outside this product.

Changing these assumptions must not weaken evidence provenance, entity resolution, or uncertainty handling.

---

## 39. Source and platform notes

The official sources support the core architecture:

- DOL publishes public disclosure data for LCA and PERM programs.
- USCIS provides downloadable H-1B employer data.
- E-Verify provides a public enrolled-employer search.
- IPEDS provides downloadable institution data.
- NSF provides downloadable HERD institution-level microdata.
- ICE/SEVP publishes OPT-related reports, but public employer coverage is not exhaustive.
- Codex Cloud can connect to GitHub repositories and use configurable internet access.
- Codex Cloud secrets are setup-only, which is why real API extraction belongs in local or GitHub Actions execution.
- OpenAI Structured Outputs can enforce the policy-extraction JSON Schema.

The application must still validate every source during implementation rather than assuming a static URL or schema.

---

## 40. Final product standard

The finished product is successful when it lets the user compare a private company, university, research institute, hospital, or national-laboratory operator using the same evidence framework while preserving the differences that matter.

The application must make it possible to say:

> This employer has repeated recent technical H-1B activity, repeated technical PERM activity, current E-Verify evidence, and high source coverage.

or:

> This university has strong research activity and likely cap-exempt status, but its official policy limits permanent-residence sponsorship to faculty, so a general staff role should not be treated as a strong green-card pathway.

or:

> This employer has no observed PERM record in the available FY2022 onward data. That is weak or unknown historical evidence, not proof that the employer refuses sponsorship.

That evidence-first standard is more important than the number of features, the appearance of the interface, or the speed of implementation.
