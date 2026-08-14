"""DOL PERM disclosure adapter."""

from sponsor_intel.sources.dol_base import DolDisclosureAdapter


class DolPermAdapter(DolDisclosureAdapter):
    """Ingest PERM disclosure records."""

    expected_source_id = "dol_perm"
