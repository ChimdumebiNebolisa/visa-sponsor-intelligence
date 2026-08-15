# Source registry

`configs/sources.yaml` is the typed registry for authoritative inputs. Each entry declares its authority, official landing page and domains, fiscal-year floor, cadence, expected formats, schema/parser versions, resource limits, logical column aliases, and ordered source-schema fingerprints.

## DOL disclosure sources

| ID | Authority | Coverage | Canonical snapshot rule | Limitation |
|---|---|---|---|---|
| `dol_lca` | U.S. Department of Labor | FY2022 onward | Latest published quarter in each FY | A certified LCA is not an approved H-1B petition. |
| `dol_perm` | U.S. Department of Labor | FY2022 onward | Latest published quarter in each FY; preserve FY2024 old/new forms | Historical PERM activity is not a future promise. |

Artifacts are discovered from the official DOL performance page. URLs are never synthesized. The downloader permits HTTPS only, validates every redirect against `dol.gov`, enforces byte and XLSX expansion limits, rejects HTML/error payloads, records SHA-256 and response metadata, and atomically promotes content-addressed raw files.

Quarterly LCA files are cumulative fiscal-year snapshots. Ingesting Q1 through Q4 together would duplicate cases, so all matching links are recorded in the discovery report while only the latest quarter per fiscal year is selected for normalization.

Schema fingerprints are keyed by source variant and fiscal year. A changed or previously unseen optional layout produces a visible warning; loss of a required logical field fails closed. The official FY2024 PERM new-form layout omits SOC code/title, so those canonical values are preserved as unknown rather than treated as negative evidence. Later PERM layouts restore those fields.

## Federal petition and institution sources

| ID | Authority | Coverage | Canonical selection | Limitation |
|---|---|---|---|---|
| `uscis_h1b` | U.S. Citizenship and Immigration Services | FY2022 through the current published quarter | One fiscal-year-filtered full-data Tableau CSV per year | Petition decisions are separate from DOL LCA/PERM evidence and are not worker counts. |
| `ipeds` | National Center for Education Statistics | Latest institutional directory | Highest published `HDYYYY.zip` with its matching dictionary | UNITID identifies the institution reported by IPEDS; it is not a legal-employer or parent-system identifier. |
| `herd` | National Center for Science and Engineering Statistics | Survey years 2022 onward | Standard and short-form microdata for every available year | R&D activity is institution-level context, not sponsorship evidence. |

The USCIS page embeds a Tableau workbook. The dashboard's obvious CSV endpoint inherits the currently displayed fiscal-year filter, so the adapter requests the official `H1BPublic` sheet once per fiscal year and rejects any artifact containing a different year. USCIS may return HTTP 403 to non-browser landing-page requests. In that case discovery emits a warning and uses the reviewed `published_through_fiscal_year` and `published_through_quarter` values in the registry; the official Tableau artifact remains checksum-verified and schema-gated. Updating those registry values requires rechecking the public hub.

IPEDS discovery records every eligible institutional-directory link but selects only the latest directory with a matching official dictionary. The normalized institution identity is `ipeds:<UNITID>`, and the six-digit source UNITID is preserved unchanged.

HERD standard and short-form archives are disjoint survey populations and are both required for each year. Expenditure values in the microdata are reported in thousands of dollars and are converted to whole U.S. dollars in the canonical observation table. Computing R&D and engineering R&D use the form-specific official questionnaire cells. Personnel is populated only where the standard form provides the total headcount.

HERD-to-IPEDS reconciliation permits exact UNITID joins only. Every match is written to `outputs/reports/institutions/herd_ipeds_join_review.parquet`; unmatched historical or closed institutions remain `NEEDS_REVIEW`. There is no name-based fallback.

## ICE SEVP positive employer report

| ID | Authority | Coverage | Canonical selection | Limitation |
|---|---|---|---|---|
| `sevp_opt` | ICE Student and Exchange Visitor Program | Latest published Top 200 employer report, currently 2024 | Highest eligible official report year | Positive, incomplete employer observations only; absence means `UNKNOWN`. |

ICE currently returns HTTP 403 to the bounded non-browser client. Discovery therefore records a warning and uses the reviewed official PDF URL and publication year in `configs/sources.yaml`; download still occurs from `ice.gov` in Chromium, followed by the same byte limit, PDF signature, SHA-256, immutable raw path, manifest, and validation gates. Updating the fallback requires rechecking the official SEVIS What's New page.

The four-page 2024 report must contain exactly 200 ranked employers. It is normalized into one positive row per available `OPT_OR_STEM_OPT`, `OPT`, or `STEM_OPT` count. Blank program cells remain absent/null evidence and are never converted to zero.

## Official institution policy sources

Policy discovery starts from each candidate's IPEDS `official_domain`. `configs/policy_sources.yaml` may add reviewed official pages; otherwise official sitemaps are searched before a domain-filtered OpenAI web-search fallback. Every result is revalidated against the institution domain and the actual page is fetched. Official campus/system scope is retained rather than inferred across legal employers.

Accepted evidence must be an HTTPS institution or reviewed official system page with a fetched exact excerpt. Search-result snippets, attorney pages, aggregators, forums, social media, and general institution-type assumptions are never policy evidence. Institution policy is time-sensitive and discretionary even after review, so source URL, retrieval date, page currency, fact scope, and supporting excerpt remain visible.
