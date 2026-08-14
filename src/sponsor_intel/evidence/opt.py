"""Conservative entity reconciliation for positive-only OPT observations."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import polars as pl

from sponsor_intel.entity_resolution.models import EntityResolutionConfig
from sponsor_intel.entity_resolution.normalization import normalize_name
from sponsor_intel.evidence.io import write_parquet_atomic
from sponsor_intel.evidence.models import OptBuildSummary
from sponsor_intel.sources.manifests import ArtifactManifestStore


def _reference_names(
    *,
    data_root: Path,
    config: EntityResolutionConfig,
) -> dict[str, list[dict[str, str | None]]]:
    references: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    legal = pl.read_parquet(data_root / "resolved" / "legal_entities.parquet")
    parents = pl.read_parquet(data_root / "resolved" / "parent_organizations.parquet")
    aliases = pl.read_parquet(data_root / "resolved" / "entity_aliases.parquet")

    for row in parents.iter_rows(named=True):
        normalized = normalize_name(row["canonical_name"], config)
        references[normalized].append(
            {
                "legal_entity_id": None,
                "parent_organization_id": row["parent_organization_id"],
                "organization_id": row["parent_organization_id"],
                "match_method": "EXACT_PARENT_NAME",
            }
        )
    for row in legal.iter_rows(named=True):
        normalized = row.get("normalized_legal_name") or normalize_name(row["legal_name"], config)
        organization_id = row["parent_organization_id"] or row["legal_entity_id"]
        references[normalized].append(
            {
                "legal_entity_id": row["legal_entity_id"],
                "parent_organization_id": row["parent_organization_id"],
                "organization_id": organization_id,
                "match_method": "EXACT_LEGAL_NAME",
            }
        )
    for row in aliases.filter(pl.col("legal_entity_id").is_not_null()).iter_rows(named=True):
        normalized = row.get("alias_normalized") or normalize_name(row["alias_raw"], config)
        organization_id = row["parent_organization_id"] or row["legal_entity_id"]
        references[normalized].append(
            {
                "legal_entity_id": row["legal_entity_id"],
                "parent_organization_id": row["parent_organization_id"],
                "organization_id": organization_id,
                "match_method": "EXACT_OBSERVED_ALIAS",
            }
        )
    return references


def _resolve_name(
    employer_name: str,
    *,
    references: dict[str, list[dict[str, str | None]]],
    config: EntityResolutionConfig,
) -> dict[str, object]:
    normalized = normalize_name(employer_name, config)
    candidates = references.get(normalized, [])
    for preferred_method in (
        "EXACT_PARENT_NAME",
        "EXACT_LEGAL_NAME",
        "EXACT_OBSERVED_ALIAS",
    ):
        preferred = [
            candidate for candidate in candidates if candidate["match_method"] == preferred_method
        ]
        if preferred:
            candidates = preferred
            break
    by_organization: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    for candidate in candidates:
        organization_id = candidate["organization_id"]
        if organization_id is not None:
            by_organization[organization_id].append(candidate)
    if not by_organization:
        return {
            "employer_name_normalized": normalized,
            "legal_entity_id": None,
            "parent_organization_id": None,
            "organization_id": None,
            "match_method": "NO_EXACT_MATCH",
            "match_confidence": 0.0,
            "review_status": "NEEDS_REVIEW",
            "review_reason": "Official report name has no unique exact entity reference",
        }
    if len(by_organization) > 1:
        return {
            "employer_name_normalized": normalized,
            "legal_entity_id": None,
            "parent_organization_id": None,
            "organization_id": None,
            "match_method": "AMBIGUOUS_EXACT_MATCH",
            "match_confidence": 0.0,
            "review_status": "NEEDS_REVIEW",
            "review_reason": "Exact name points to multiple organizations",
        }

    organization_id, matches = next(iter(by_organization.items()))
    parents = {item["parent_organization_id"] for item in matches if item["parent_organization_id"]}
    legal_ids = {item["legal_entity_id"] for item in matches if item["legal_entity_id"]}
    methods = {item["match_method"] for item in matches}
    method = (
        "EXACT_PARENT_NAME"
        if "EXACT_PARENT_NAME" in methods
        else "EXACT_LEGAL_NAME"
        if "EXACT_LEGAL_NAME" in methods
        else "EXACT_OBSERVED_ALIAS"
    )
    return {
        "employer_name_normalized": normalized,
        "legal_entity_id": next(iter(legal_ids)) if len(legal_ids) == 1 else None,
        "parent_organization_id": next(iter(parents)) if len(parents) == 1 else None,
        "organization_id": organization_id,
        "match_method": method,
        "match_confidence": 1.0 if method != "EXACT_OBSERVED_ALIAS" else 0.99,
        "review_status": "NOT_REQUIRED",
        "review_reason": None,
    }


class OptEvidenceBuilder:
    """Link official positive observations without inferring anything from absence."""

    def __init__(
        self,
        *,
        data_root: Path = Path("data"),
        output_root: Path = Path("outputs"),
        resolution_config_path: Path = Path("configs/entity_resolution.yaml"),
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.config = EntityResolutionConfig.from_yaml(resolution_config_path)

    def build(self) -> OptBuildSummary:
        manifest = ArtifactManifestStore(self.output_root / "manifests" / "source_artifacts.jsonl")
        records = [record for record in manifest.records() if record.source_id == "sevp_opt"]
        if not records:
            raise ValueError("No ingested sevp_opt source artifact is available")
        latest_year = max(record.fiscal_year for record in records)
        latest = max(
            (record for record in records if record.fiscal_year == latest_year),
            key=lambda record: record.retrieved_at,
        )
        raw = pl.read_parquet(latest.parquet_path)
        references = _reference_names(data_root=self.data_root, config=self.config)
        resolutions = pl.DataFrame(
            [
                {"employer_name_raw": name}
                | _resolve_name(name, references=references, config=self.config)
                for name in raw["employer_name_raw"].unique(maintain_order=True).to_list()
            ]
        )
        observations = raw.join(resolutions, on="employer_name_raw", how="left").sort(
            ["report_year", "rank", "program_type"]
        )
        if observations.filter(~pl.col("is_positive")).height:
            raise ValueError("OPT evidence must remain positive-only")
        review = (
            resolutions.filter(pl.col("review_status") == "NEEDS_REVIEW")
            .join(
                raw.filter(pl.col("program_type") == "OPT_OR_STEM_OPT").select(
                    "employer_name_raw", "report_year", "rank", "reported_count", "source_url"
                ),
                on="employer_name_raw",
                how="left",
            )
            .sort(["report_year", "rank"])
        )
        observations_path = self.data_root / "processed" / "opt_employer_observations.parquet"
        review_path = self.output_root / "review" / "opt_entity_review.parquet"
        write_parquet_atomic(observations, observations_path)
        write_parquet_atomic(review, review_path)
        employer_resolutions = observations.filter(pl.col("program_type") == "OPT_OR_STEM_OPT")
        return OptBuildSummary(
            report_year=latest_year,
            employer_count=employer_resolutions.height,
            observation_count=observations.height,
            linked_employer_count=employer_resolutions.filter(
                pl.col("organization_id").is_not_null()
            ).height,
            review_employer_count=review.height,
            observations_path=observations_path,
            review_path=review_path,
        )
