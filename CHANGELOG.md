# Changelog

All notable project changes will be documented here.

## Unreleased

- Added the Phase 0 repository foundation, typed configuration, structured logging, CLI, service boundary, Streamlit shell, tests, and CI.
- Added Phase 1 official DOL LCA/PERM landing-page discovery, bounded immutable downloads, raw and complete manifests, FY2022 onward canonical snapshot selection, schema fingerprints, normalized Parquet outputs, and live opt-in contract tests.
- Added deterministic handling for exact duplicate source rows and repeated PERM decision dates while preserving source-row provenance and failing on conflicting duplicate cases.
- Preserved the FY2024 PERM new-form SOC omission as unknown with explicit data-quality evidence.
- Added Phase 2 USCIS H-1B, IPEDS directory, and HERD microdata adapters; fiscal-year-filtered petition evidence; canonical UNITID institution identities; exact-identifier HERD reconciliation; and explicit unmatched review outputs.
- Added Phase 3 conservative legal-entity resolution, reviewed parent rollups, IPEDS system relationships, immutable resolved-source mirrors, explicit match statuses, an unmerged review queue, audited alias/rejection overrides, and deterministic outputs.
- Added a 200-pair entity-resolution gold set spanning technology, universities, systems, hospitals, research laboratories, and staffing/consulting, with precision and parent/legal-collapse acceptance checks.
- Added Phase 4 versioned SOC/title role classification, reviewed overrides, technical families, strong exclusions, combined evidence rules, complete classified DOL mirrors, and an explicit ambiguity review queue.
- Added a 750-record role benchmark across all source years and six employer types, with precision, recall, family-accuracy, and low-confidence routing gates.
- Added Phase 5 processed employer, institution, case, and source-health metrics with parent/legal reconciliation, raw DOL/USCIS/IPEDS/HERD evidence, and explicit FY2026 partial-period warnings.
- Added the DuckDB presentation database, all ten required views, parameterized read-only services, employer and research-institution filters, organization drilldowns, and filtered CSV/Parquet exports.
