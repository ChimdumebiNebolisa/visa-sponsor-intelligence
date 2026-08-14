"""Conservative legal-entity candidate scoring and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl
from rapidfuzz import fuzz, process

from sponsor_intel.entity_resolution.models import (
    EntityOverrides,
    EntityResolutionConfig,
    MatchStatus,
)
from sponsor_intel.entity_resolution.normalization import (
    core_name,
    legal_suffix,
    name_acronym,
    normalize_city,
    normalize_name,
    normalize_postal_code,
    normalize_state,
    stable_id,
)

_TOKEN_COUNT_STOPWORDS = {"AND", "AT", "OF", "THE"}
_SCOPE_TOKENS = {
    "AFFILIATE",
    "AFFILIATES",
    "ATHLETIC",
    "BRANCH",
    "CAMPUS",
    "DBA",
    "FOUNDATION",
    "HEALTH",
    "HOSPITAL",
    "HOSPITALS",
    "OFFICE",
    "RESEARCH",
    "SYSTEM",
}


@dataclass(slots=True)
class CandidateEntity:
    legal_entity_id: str
    legal_name: str
    normalized_legal_name: str
    core_name: str
    legal_suffix: str
    acronym: str
    parent_organization_id: str | None
    city: str
    state: str
    postal_code: str
    organization_type: str
    institution_id: str | None
    created_by: str
    review_status: str
    occurrence_count: int = 0


@dataclass(frozen=True, slots=True)
class MatchFeatures:
    score: float
    token_similarity: float
    character_similarity: float
    state_agreement: bool | None
    city_agreement: bool | None
    postal_agreement: bool | None
    location_agreement: bool


@dataclass(frozen=True, slots=True)
class ResolutionTables:
    legal_entities: pl.DataFrame
    parent_organizations: pl.DataFrame
    aliases: pl.DataFrame
    review_queue: pl.DataFrame


def score_pair(
    left_name: str,
    right_name: str,
    *,
    left_city: str = "",
    right_city: str = "",
    left_state: str = "",
    right_state: str = "",
    left_postal_code: str = "",
    right_postal_code: str = "",
) -> MatchFeatures:
    token_similarity = fuzz.token_set_ratio(left_name, right_name) / 100
    character_similarity = fuzz.ratio(left_name, right_name) / 100
    state_agreement = left_state == right_state if left_state and right_state else None
    city_agreement = left_city == right_city if left_city and right_city else None
    postal_agreement = (
        left_postal_code == right_postal_code if left_postal_code and right_postal_code else None
    )
    score = 0.60 * token_similarity + 0.25 * character_similarity
    score += 0.08 if state_agreement else 0
    score += 0.05 if city_agreement else 0
    score += 0.02 if postal_agreement else 0
    location_agreement = bool(
        state_agreement and (city_agreement is True or postal_agreement is True)
    )
    return MatchFeatures(
        score=round(score, 6),
        token_similarity=round(token_similarity, 6),
        character_similarity=round(character_similarity, 6),
        state_agreement=state_agreement,
        city_agreement=city_agreement,
        postal_agreement=postal_agreement,
        location_agreement=location_agreement,
    )


def _block_keys(core: str, acronym: str, state: str, postal_code: str) -> set[str]:
    keys: set[str] = set()
    compact = core.replace(" ", "")
    if state and compact:
        keys.add(f"state_prefix:{state}:{compact[:5]}")
    if state and len(acronym) >= 3:
        keys.add(f"state_acronym:{state}:{acronym}")
    if postal_code:
        keys.add(f"postal:{postal_code}")
    return keys


def _location_conflicts(observation: dict[str, Any], candidate: CandidateEntity) -> bool:
    state = str(observation["state"])
    city = str(observation["city"])
    if state and candidate.state and state != candidate.state:
        return True
    return bool(state and candidate.state and city and candidate.city and city != candidate.city)


def _legal_suffix_conflicts(
    normalized_name: str,
    candidate: CandidateEntity,
    config: EntityResolutionConfig,
) -> bool:
    observed = legal_suffix(normalized_name, config)
    return bool(observed and candidate.legal_suffix and observed != candidate.legal_suffix)


def _fuzzy_legal_scope_compatible(left_core: str, right_core: str) -> bool:
    """Reject fuzzy matches that add or remove organization-scope words."""

    left_tokens = [token for token in left_core.split() if token not in _TOKEN_COUNT_STOPWORDS]
    right_tokens = [token for token in right_core.split() if token not in _TOKEN_COUNT_STOPWORDS]
    while left_tokens and len(left_tokens[0]) == 1:
        left_tokens.pop(0)
    while right_tokens and len(right_tokens[0]) == 1:
        right_tokens.pop(0)
    left_scope = set(left_tokens) & _SCOPE_TOKENS
    right_scope = set(right_tokens) & _SCOPE_TOKENS
    return len(left_tokens) == len(right_tokens) and left_scope == right_scope


def resolve_observations(
    observations: pl.DataFrame,
    ipeds: pl.DataFrame,
    config: EntityResolutionConfig,
    overrides: EntityOverrides,
) -> ResolutionTables:
    """Resolve distinct source observations without fuzzy parent merges."""

    entities: dict[str, CandidateEntity] = {}
    parents: dict[str, dict[str, Any]] = {
        item.parent_organization_id: {
            "parent_organization_id": item.parent_organization_id,
            "canonical_name": item.canonical_name,
            "organization_type": item.organization_type,
            "headquarters_state": normalize_state(item.headquarters_state),
            "is_staffing_or_consulting": item.is_staffing_or_consulting,
            "created_by": "MANUAL_OVERRIDE",
            "review_status": MatchStatus.MANUAL_OVERRIDE.value,
            "notes": item.notes,
        }
        for item in overrides.parent_organizations
    }

    exact_index: dict[str, set[str]] = {}
    block_index: dict[str, set[str]] = {}

    def index_entity(entity: CandidateEntity, *, include_fuzzy: bool = True) -> None:
        entities[entity.legal_entity_id] = entity
        exact_index.setdefault(entity.normalized_legal_name, set()).add(entity.legal_entity_id)
        if include_fuzzy:
            for key in _block_keys(
                entity.core_name, entity.acronym, entity.state, entity.postal_code
            ):
                block_index.setdefault(key, set()).add(entity.legal_entity_id)

    for item in overrides.legal_entities:
        normalized = normalize_name(item.legal_name, config)
        index_entity(
            CandidateEntity(
                legal_entity_id=item.legal_entity_id,
                legal_name=item.legal_name,
                normalized_legal_name=normalized,
                core_name=core_name(normalized, config),
                legal_suffix=legal_suffix(normalized, config),
                acronym=name_acronym(normalized),
                parent_organization_id=item.parent_organization_id,
                city=normalize_city(item.city),
                state=normalize_state(item.state),
                postal_code=normalize_postal_code(item.postal_code),
                organization_type=item.organization_type,
                institution_id=None,
                created_by="MANUAL_OVERRIDE",
                review_status=MatchStatus.MANUAL_OVERRIDE.value,
            )
        )

    if not ipeds.is_empty():
        for row in ipeds.iter_rows(named=True):
            unitid = str(row["unitid"])
            normalized = normalize_name(str(row["instnm"]), config)
            system_name = row.get("f1sysnam")
            system_code = row.get("f1syscod")
            parent_id: str | None = None
            if system_name not in (None, "", "-2"):
                identifier = (
                    str(system_code)
                    if system_code not in (None, "", "-2")
                    else normalize_name(str(system_name), config)
                )
                parent_id = stable_id("parent_ipeds", identifier)
                parents[parent_id] = {
                    "parent_organization_id": parent_id,
                    "canonical_name": str(system_name),
                    "organization_type": "HIGHER_EDUCATION_SYSTEM",
                    "headquarters_state": None,
                    "is_staffing_or_consulting": False,
                    "created_by": "IPEDS_SYSTEM_IDENTIFIER",
                    "review_status": MatchStatus.DETERMINISTIC.value,
                    "notes": (
                        "Official IPEDS system relationship; campus legal identity "
                        "remains separate."
                    ),
                }
            index_entity(
                CandidateEntity(
                    legal_entity_id=f"legal_ipeds_{unitid}",
                    legal_name=str(row["instnm"]),
                    normalized_legal_name=normalized,
                    core_name=core_name(normalized, config),
                    legal_suffix=legal_suffix(normalized, config),
                    acronym=name_acronym(normalized),
                    parent_organization_id=parent_id,
                    city=normalize_city(row.get("city")),
                    state=normalize_state(row.get("stabbr")),
                    postal_code=normalize_postal_code(row.get("zip")),
                    organization_type="HIGHER_EDUCATION",
                    institution_id=f"ipeds:{unitid}",
                    created_by="IPEDS_UNITID",
                    review_status=MatchStatus.DETERMINISTIC.value,
                )
            )

    alias_overrides = {
        (item.source_id or "*", normalize_name(item.raw_name, config)): item
        for item in overrides.aliases
    }
    rejection_overrides = {
        (item.source_id or "*", normalize_name(item.raw_name, config)): item
        for item in overrides.rejections
    }
    alias_rows: list[dict[str, Any]] = []

    ordered = observations.sort(
        [
            "occurrence_count",
            "normalized_name",
            "state",
            "city",
            "source_id",
            "alias_raw",
            "postal_code",
            "observation_id",
        ],
        descending=[True, False, False, False, False, False, False, False],
    )
    for observation in ordered.iter_rows(named=True):
        normalized = str(observation["normalized_name"])
        source_id = str(observation["source_id"])
        raw_name = str(observation["alias_raw"] or "")
        override = alias_overrides.get((source_id, normalized)) or alias_overrides.get(
            ("*", normalized)
        )
        rejection = rejection_overrides.get((source_id, normalized)) or rejection_overrides.get(
            ("*", normalized)
        )
        status = MatchStatus.UNRESOLVED
        method = "NO_NAME"
        legal_entity_id: str | None = None
        candidate_id: str | None = None
        score = 0.0
        margin: float | None = None
        features: MatchFeatures | None = None
        reviewed_by: str | None = None
        reviewed_at: str | None = None
        resolution_reason: str | None = None

        if override is not None:
            legal_entity_id = override.legal_entity_id
            status = MatchStatus.MANUAL_OVERRIDE
            method = "REVIEWED_ALIAS"
            score = 1.0
            reviewed_by = override.reviewed_by
            reviewed_at = override.reviewed_at
            resolution_reason = override.reason
        elif normalized:
            exact_candidates = [
                entities[item] for item in sorted(exact_index.get(normalized, set()))
            ]
            location_exact = [
                item for item in exact_candidates if not _location_conflicts(observation, item)
            ]
            selected_exact = exact_candidates if len(exact_candidates) == 1 else location_exact
            if len(selected_exact) == 1:
                candidate = selected_exact[0]
                legal_entity_id = candidate.legal_entity_id
                status = MatchStatus.DETERMINISTIC
                method = "EXACT_NORMALIZED_NAME"
                score = 1.0
            else:
                core = str(observation["core_name"])
                acronym = str(observation["acronym"])
                candidate_ids: set[str] = set()
                for key in _block_keys(
                    core,
                    acronym,
                    str(observation["state"]),
                    str(observation["postal_code"]),
                ):
                    candidate_ids.update(block_index.get(key, set()))
                choices = {
                    item: entities[item].core_name
                    for item in sorted(candidate_ids)
                    if entities[item].core_name
                }
                shortlist = process.extract(
                    core,
                    choices,
                    scorer=fuzz.WRatio,
                    limit=config.candidate_limit,
                    score_cutoff=max(0, (config.review_threshold - 0.20) * 100),
                )
                scored: list[tuple[float, str, MatchFeatures]] = []
                for _choice, _rapid_score, entity_id in shortlist:
                    candidate = entities[entity_id]
                    candidate_features = score_pair(
                        core,
                        candidate.core_name,
                        left_city=str(observation["city"]),
                        right_city=candidate.city,
                        left_state=str(observation["state"]),
                        right_state=candidate.state,
                        left_postal_code=str(observation["postal_code"]),
                        right_postal_code=candidate.postal_code,
                    )
                    scored.append((candidate_features.score, entity_id, candidate_features))
                scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
                if scored:
                    score, candidate_id, features = scored[0]
                    runner_up = scored[1][0] if len(scored) > 1 else 0.0
                    margin = round(score - runner_up, 6)

                if rejection is not None and rejection.candidate_legal_entity_id in entities:
                    candidate_id = rejection.candidate_legal_entity_id
                    rejected_candidate = entities[candidate_id]
                    features = score_pair(
                        core,
                        rejected_candidate.core_name,
                        left_city=str(observation["city"]),
                        right_city=rejected_candidate.city,
                        left_state=str(observation["state"]),
                        right_state=rejected_candidate.state,
                        left_postal_code=str(observation["postal_code"]),
                        right_postal_code=rejected_candidate.postal_code,
                    )
                    score = features.score

                if rejection is not None and candidate_id == rejection.candidate_legal_entity_id:
                    status = MatchStatus.REJECTED
                    method = "REVIEWED_REJECTION"
                    reviewed_by = rejection.reviewed_by
                    reviewed_at = rejection.reviewed_at
                    resolution_reason = rejection.reason
                elif (
                    candidate_id is not None
                    and score >= config.high_confidence_threshold
                    and margin is not None
                    and margin >= config.minimum_margin
                    and not _legal_suffix_conflicts(normalized, entities[candidate_id], config)
                    and _fuzzy_legal_scope_compatible(core, entities[candidate_id].core_name)
                    and (
                        not config.location_agreement_required_for_fuzzy_auto
                        or (features is not None and features.location_agreement)
                    )
                ):
                    legal_entity_id = candidate_id
                    status = MatchStatus.HIGH_CONFIDENCE_AUTO
                    method = "FUZZY_NAME_WITH_LOCATION"
                elif candidate_id is not None and score >= config.review_threshold:
                    status = MatchStatus.REVIEW_REQUIRED
                    method = "FUZZY_CANDIDATE_UNMERGED"

            if legal_entity_id is None:
                legal_entity_id = stable_id(
                    "legal_source",
                    normalized,
                    str(observation["state"]),
                    str(observation["city"]),
                )
                if legal_entity_id not in entities:
                    entity_status = (
                        status
                        if status in {MatchStatus.REVIEW_REQUIRED, MatchStatus.REJECTED}
                        else MatchStatus.DETERMINISTIC
                    )
                    index_entity(
                        CandidateEntity(
                            legal_entity_id=legal_entity_id,
                            legal_name=raw_name,
                            normalized_legal_name=normalized,
                            core_name=str(observation["core_name"]),
                            legal_suffix=legal_suffix(normalized, config),
                            acronym=str(observation["acronym"]),
                            parent_organization_id=None,
                            city=str(observation["city"]),
                            state=str(observation["state"]),
                            postal_code=str(observation["postal_code"]),
                            organization_type="UNKNOWN",
                            institution_id=None,
                            created_by="SOURCE_NAME",
                            review_status=entity_status.value,
                            occurrence_count=int(observation["occurrence_count"]),
                        ),
                        include_fuzzy=False,
                    )
                if status is MatchStatus.UNRESOLVED:
                    status = MatchStatus.DETERMINISTIC
                    method = "NEW_SOURCE_LEGAL_ENTITY"
                    score = 1.0

        parent_id = (
            entities[legal_entity_id].parent_organization_id
            if legal_entity_id is not None
            else None
        )
        alias_id = stable_id(
            "alias",
            source_id,
            raw_name,
            str(observation["city"]),
            str(observation["state"]),
            str(observation["postal_code"]),
        )
        alias_row = {
            "alias_id": alias_id,
            "observation_id": observation["observation_id"],
            "alias_raw": raw_name,
            "alias_normalized": normalized,
            "core_name": observation["core_name"],
            "acronym": observation["acronym"],
            "legal_entity_id": legal_entity_id,
            "parent_organization_id": parent_id,
            "candidate_legal_entity_id": candidate_id,
            "source_id": source_id,
            "city": observation["city"],
            "state": observation["state"],
            "postal_code": observation["postal_code"],
            "occurrence_count": observation["occurrence_count"],
            "match_method": method,
            "match_score": score,
            "candidate_margin": margin,
            "match_status": status.value,
            "review_status": status.value,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
            "resolution_reason": resolution_reason,
            "token_similarity": features.token_similarity if features else None,
            "character_similarity": features.character_similarity if features else None,
            "location_agreement": features.location_agreement if features else None,
        }
        alias_rows.append(alias_row)
    legal_rows = [
        {
            "legal_entity_id": item.legal_entity_id,
            "legal_name": item.legal_name,
            "normalized_legal_name": item.normalized_legal_name,
            "parent_organization_id": item.parent_organization_id,
            "city": item.city or None,
            "state": item.state or None,
            "postal_code": item.postal_code or None,
            "country": "US",
            "organization_type": item.organization_type,
            "institution_id": item.institution_id,
            "created_by": item.created_by,
            "review_status": item.review_status,
        }
        for item in entities.values()
    ]
    alias_frame = pl.DataFrame(alias_rows).sort(["source_id", "alias_normalized", "alias_id"])
    review_frame = alias_frame.filter(
        pl.col("match_status").is_in(
            [MatchStatus.REVIEW_REQUIRED.value, MatchStatus.REJECTED.value]
        )
    ).sort(
        ["match_status", "match_score", "alias_normalized"],
        descending=[False, True, False],
    )
    parent_rows = list(parents.values())
    parent_frame = (
        pl.DataFrame(parent_rows)
        if parent_rows
        else pl.DataFrame(
            schema={
                "parent_organization_id": pl.String,
                "canonical_name": pl.String,
                "organization_type": pl.String,
                "headquarters_state": pl.String,
                "is_staffing_or_consulting": pl.Boolean,
                "created_by": pl.String,
                "review_status": pl.String,
                "notes": pl.String,
            }
        )
    )
    return ResolutionTables(
        legal_entities=pl.DataFrame(legal_rows).sort("legal_entity_id"),
        parent_organizations=parent_frame.sort("parent_organization_id"),
        aliases=alias_frame,
        review_queue=review_frame,
    )
