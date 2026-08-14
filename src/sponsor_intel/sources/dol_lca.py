"""DOL LCA disclosure adapter."""

from sponsor_intel.sources.dol_base import DolDisclosureAdapter


class DolLcaAdapter(DolDisclosureAdapter):
    """Ingest LCA (H-1B, H-1B1, E-3) disclosure records."""

    expected_source_id = "dol_lca"
