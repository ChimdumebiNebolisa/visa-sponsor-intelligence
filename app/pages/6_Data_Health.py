"""Data-health page shell."""

from app.components.foundation import render_foundation_page

render_foundation_page(
    "Data Health",
    "Source freshness, schema validation, coverage, checksums, build metadata, and quality issues "
    "will be visible here.",
)
