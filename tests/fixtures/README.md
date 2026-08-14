# Test fixtures

Store only small, sanitized, source-attributed fixtures here. Do not add API keys, full policy documents, or large government datasets.

`entity_resolution_gold.csv` is a manually curated organization-name pair set used to validate conservative matching behavior. It contains no private person-level data. Categories and minimum counts follow the Phase 3 specification; system/campus pairs are deliberate nonmatches.

`role_classification_gold.csv` contains 750 manually labeled role records across all DOL source years and six employer types. It is derived from reviewed title/SOC cases and contains no person-level data.
