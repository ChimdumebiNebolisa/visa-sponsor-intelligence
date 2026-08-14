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
