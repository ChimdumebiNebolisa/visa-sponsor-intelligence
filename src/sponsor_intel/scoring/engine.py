"""Deterministic nullable evidence-strength formulas driven by scoring.yaml."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import polars as pl

from sponsor_intel.scoring.models import Band, CompositeConfig, ScoringConfig, ScoringV2Config


def _band(value: pl.Expr, bands: list[Band], *, unknown: str = "UNKNOWN") -> pl.Expr:
    expression = pl.lit(unknown)
    for band in reversed(bands):
        expression = pl.when(value >= band.minimum).then(pl.lit(band.label)).otherwise(expression)
    return expression


def _log_score(value: pl.Expr, *, cap: int, observed: pl.Expr) -> pl.Expr:
    return (
        pl.when(observed)
        .then(((value.cast(pl.Float64).clip(0).log1p() / math.log1p(cap)) * 100).clip(0, 100))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )


def _weighted_component(
    components: Mapping[str, pl.Expr],
    weights: Mapping[str, float],
    *,
    eligible: pl.Expr,
) -> tuple[pl.Expr, pl.Expr]:
    observed_weight = pl.sum_horizontal(
        [
            pl.when(components[name].is_not_null()).then(pl.lit(weight)).otherwise(pl.lit(0.0))
            for name, weight in weights.items()
        ]
    )
    weighted_value = pl.sum_horizontal(
        [
            pl.when(components[name].is_not_null())
            .then(components[name] * weight)
            .otherwise(pl.lit(0.0))
            for name, weight in weights.items()
        ]
    )
    total_weight = sum(weights.values())
    score = (
        pl.when(eligible & (observed_weight > 0))
        .then((weighted_value / observed_weight).clip(0, 100).round(2))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    coverage = (observed_weight / total_weight).clip(0, 1).round(4)
    return score, coverage


def _stem_opt_columns(frame: pl.DataFrame, config: ScoringConfig | ScoringV2Config) -> pl.DataFrame:
    formula = config.stem_opt_readiness
    active = pl.col("everify_status") == "CONFIRMED_ACTIVE"
    inactive = pl.col("everify_status") == "CONFIRMED_INACTIVE"
    opt = pl.col("known_opt_observation") == "OBSERVED_POSITIVE"
    score = (
        pl.when(inactive)
        .then(pl.lit(formula.everify_inactive_score))
        .when(active)
        .then(
            pl.when(opt)
            .then(pl.lit(min(100.0, formula.everify_active_score + formula.opt_bonus)))
            .otherwise(pl.lit(formula.everify_active_score))
        )
        .when(opt)
        .then(pl.lit(formula.opt_only_score))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )
    coverage = (
        pl.when(active | inactive)
        .then(pl.lit(formula.evidence_weights["everify"]))
        .otherwise(pl.lit(0.0))
        + pl.when(opt).then(pl.lit(formula.evidence_weights["opt"])).otherwise(pl.lit(0.0))
    ).round(4)
    status = (
        pl.when(inactive)
        .then(pl.lit("EXPLICIT_BLOCKER"))
        .otherwise(_band(score, formula.status_bands))
    )
    explanation = (
        pl.when(inactive)
        .then(pl.lit("A reviewed E-Verify lookup reports confirmed inactive enrollment."))
        .when(active & opt)
        .then(pl.lit("Confirmed active E-Verify plus a positive recent OPT/STEM OPT observation."))
        .when(active)
        .then(
            pl.lit(
                "Confirmed active E-Verify; absence from the positive-only OPT report is unknown."
            )
        )
        .when(opt)
        .then(
            pl.lit("Positive recent OPT/STEM OPT observation; current E-Verify status is unknown.")
        )
        .otherwise(
            pl.lit(
                "Insufficient current E-Verify or positive OPT evidence; missing evidence "
                "was not scored as zero."
            )
        )
    )
    return frame.with_columns(
        score.round(2).alias("stem_opt_readiness_score"),
        status.alias("stem_opt_readiness_status"),
        coverage.alias("stem_opt_readiness_coverage"),
        coverage.alias("stem_opt_readiness_confidence"),
        explanation.alias("stem_opt_readiness_explanation"),
    )


def _h1b_columns(frame: pl.DataFrame, config: ScoringConfig | ScoringV2Config) -> pl.DataFrame:
    formula = config.h1b_history
    lca_observed = pl.col("lca_case_count") > 0
    uscis_observed = pl.col("uscis_employer_year_rows") > 0
    eligible = lca_observed | uscis_observed
    active_years = pl.max_horizontal(
        pl.when(lca_observed).then(pl.col("lca_active_years")).otherwise(None),
        pl.when(uscis_observed).then(pl.col("uscis_active_years")).otherwise(None),
    )
    last_year = pl.max_horizontal("last_lca_activity_year", "last_uscis_activity_year")
    decision_count = pl.col("initial_approvals") + pl.col("initial_denials")
    components = {
        "relevant_lca_volume": _log_score(
            pl.col("relevant_lca_count"),
            cap=formula.relevant_lca_volume_cap,
            observed=lca_observed,
        ),
        "initial_approvals": _log_score(
            pl.col("initial_approvals"),
            cap=formula.initial_approval_volume_cap,
            observed=uscis_observed,
        ),
        "active_years": pl.when(active_years.is_not_null())
        .then((active_years / formula.active_year_cap * 100).clip(0, 100))
        .otherwise(None),
        "recency": pl.when(last_year.is_not_null())
        .then(
            (
                100 - (config.reference_year - last_year).clip(0) * formula.recency_penalty_per_year
            ).clip(0, 100)
        )
        .otherwise(None),
        "technical_share": pl.when(lca_observed)
        .then((pl.col("relevant_lca_count") / pl.col("lca_case_count") * 100).clip(0, 100))
        .otherwise(None),
        "approval_ratio": pl.when(
            uscis_observed & (decision_count >= formula.approval_ratio_minimum_denominator)
        )
        .then((pl.col("initial_approvals") / decision_count * 100).clip(0, 100))
        .otherwise(None),
    }
    score, coverage = _weighted_component(components, formula.weights, eligible=eligible)
    return frame.with_columns(
        score.alias("h1b_history_score"),
        score.alias("h1b_activity_score"),
        coverage.alias("h1b_history_coverage"),
        coverage.alias("h1b_history_confidence"),
        _band(score, config.grade_bands).alias("h1b_history_grade"),
        pl.when(eligible)
        .then(
            pl.lit(
                "Weighted observed USCIS approvals, relevant LCA volume, active years, "
                "recency, technical share, and safeguarded approval ratio."
            )
        )
        .otherwise(
            pl.lit("No observed LCA or USCIS petition history; the H-1B history score is unknown.")
        )
        .alias("h1b_history_explanation"),
    )


def _green_card_columns(
    frame: pl.DataFrame, config: ScoringConfig | ScoringV2Config
) -> pl.DataFrame:
    formula = config.green_card_history
    observed = pl.col("perm_case_count") > 0
    relevant = pl.col("relevant_certified_perm_count")
    components = {
        "relevant_perm_volume": _log_score(
            relevant,
            cap=formula.relevant_perm_volume_cap,
            observed=observed,
        ),
        "active_years": pl.when(observed)
        .then((pl.col("perm_active_years") / formula.active_year_cap * 100).clip(0, 100))
        .otherwise(None),
        "recency": pl.when(pl.col("last_perm_activity_year").is_not_null())
        .then(
            (
                100
                - (config.reference_year - pl.col("last_perm_activity_year")).clip(0)
                * formula.recency_penalty_per_year
            ).clip(0, 100)
        )
        .otherwise(None),
        "technical_share": pl.when(observed)
        .then((relevant / pl.col("perm_case_count") * 100).clip(0, 100))
        .otherwise(None),
        "exact_title_repetition": pl.when(
            (relevant > 0) & pl.col("top_perm_technical_title_count").is_not_null()
        )
        .then((pl.col("top_perm_technical_title_count") / relevant * 100).clip(0, 100))
        .otherwise(None),
    }
    score, coverage = _weighted_component(components, formula.weights, eligible=observed)
    return frame.with_columns(
        score.alias("green_card_history_score"),
        coverage.alias("green_card_history_coverage"),
        coverage.alias("green_card_history_confidence"),
        _band(score, config.grade_bands).alias("green_card_history_grade"),
        pl.when(observed)
        .then(
            pl.lit(
                "Weighted relevant certified PERM volume, active years, recency, technical "
                "share, and repeated exact titles."
            )
        )
        .otherwise(
            pl.lit("No observed PERM history; absence was retained as unknown rather than refusal.")
        )
        .alias("green_card_history_explanation"),
    )


def _composite_columns(
    frame: pl.DataFrame,
    *,
    name: str,
    formula: CompositeConfig,
    score_columns: Mapping[str, str],
    confidence_columns: Mapping[str, str],
    config: ScoringConfig | ScoringV2Config,
) -> pl.DataFrame:
    component_scores = {key: pl.col(score_columns[key]) for key in formula.weights}
    all_present = pl.all_horizontal([value.is_not_null() for value in component_scores.values()])
    any_present = pl.any_horizontal([value.is_not_null() for value in component_scores.values()])
    eligible = all_present if formula.require_all_components else any_present
    score, _ = _weighted_component(component_scores, formula.weights, eligible=eligible)
    coverage = pl.sum_horizontal(
        [
            pl.col(confidence_columns[key]).fill_null(0.0) * weight
            for key, weight in formula.weights.items()
        ]
    )
    confidence = coverage.clip(0, 1).round(4)
    label = name.replace("_", " ")
    return frame.with_columns(
        score.alias(f"{name}_score"),
        confidence.alias(f"{name}_coverage"),
        confidence.alias(f"{name}_confidence"),
        _band(score, config.grade_bands).alias(f"{name}_grade"),
        pl.when(score.is_not_null())
        .then(
            pl.lit(
                f"{label.title()} combines all configured components without reweighting "
                "missing evidence."
            )
        )
        .otherwise(
            pl.lit(
                f"Insufficient {label} coverage; missing components were not converted to "
                "zero or reweighted."
            )
        )
        .alias(f"{name}_explanation"),
    )


def _immigration_columns(frame: pl.DataFrame, config: ScoringConfig) -> pl.DataFrame:
    result = _stem_opt_columns(frame, config)
    result = _h1b_columns(result, config)
    result = _green_card_columns(result, config)
    result = _composite_columns(
        result,
        name="immigration_evidence",
        formula=config.composites.immigration_evidence,
        score_columns={
            "stem_opt_readiness": "stem_opt_readiness_score",
            "h1b_history": "h1b_history_score",
            "green_card_history": "green_card_history_score",
        },
        confidence_columns={
            "stem_opt_readiness": "stem_opt_readiness_confidence",
            "h1b_history": "h1b_history_confidence",
            "green_card_history": "green_card_history_confidence",
        },
        config=config,
    )
    return result.with_columns(
        _band(pl.col("immigration_evidence_confidence"), config.confidence_bands).alias(
            "evidence_confidence"
        ),
        pl.lit(config.version).alias("score_version"),
    )


def _percentile(value: str, available: str) -> pl.Expr:
    observed = pl.when(pl.col(available)).then(pl.col(value).cast(pl.Float64)).otherwise(None)
    return (
        pl.when(pl.col(available))
        .then(observed.rank(method="average") / observed.count() * 100)
        .otherwise(None)
    )


def _research_columns(frame: pl.DataFrame, config: ScoringConfig | ScoringV2Config) -> pl.DataFrame:
    names = {
        "total_rd_percentile": ("total_rd", "has_total_rd_data"),
        "computing_rd_percentile": ("computing_rd", "has_computing_rd_data"),
        "engineering_rd_percentile": ("engineering_rd", "has_engineering_rd_data"),
        "federal_rd_percentile": ("federal_rd", "has_federal_rd_data"),
    }
    result = frame.with_columns(
        *[
            _percentile(value, available).round(2).alias(name)
            for name, (value, available) in names.items()
        ]
    )
    score, coverage = _weighted_component(
        {name: pl.col(name) for name in names},
        config.research_strength.weights,
        eligible=pl.col("has_total_rd_data"),
    )
    return result.with_columns(
        score.alias("research_strength_score"),
        coverage.alias("research_strength_coverage"),
        coverage.alias("research_strength_confidence"),
        _band(score, config.grade_bands).alias("research_strength_grade"),
        pl.when(pl.col("has_total_rd_data"))
        .then(
            pl.lit(
                "HERD total, computing, engineering, and federal R&D percentiles; "
                "unavailable short-form fields reduce coverage."
            )
        )
        .otherwise(pl.lit("No HERD observation is linked; research strength is unknown."))
        .alias("research_strength_explanation"),
    )


def _policy_score_frame(
    facts: pl.DataFrame | None, config: ScoringConfig | ScoringV2Config
) -> pl.DataFrame:
    schema = {
        "institution_id": pl.String,
        "policy_support_score": pl.Float64,
        "policy_support_coverage": pl.Float64,
        "policy_support_confidence": pl.Float64,
        "policy_support_fact_count": pl.UInt32,
    }
    if facts is None or facts.is_empty():
        return pl.DataFrame(schema=schema)
    accepted = facts.filter(
        (pl.col("human_review_status") == "REVIEWED_ACCEPTED")
        & pl.col("exact_excerpt_verified")
        & pl.col("is_current")
        & pl.col("valid_to").is_null()
        & pl.col("source_url").str.starts_with("https://")
        & pl.col("fact_type").is_in(list(config.policy_support.fact_weights))
    )
    if accepted.is_empty():
        return pl.DataFrame(schema=schema)
    weight_expression = pl.lit(None, dtype=pl.Float64)
    value_expression = pl.lit(None, dtype=pl.Float64)
    for fact_type, weight in config.policy_support.fact_weights.items():
        weight_expression = (
            pl.when(pl.col("fact_type") == fact_type)
            .then(pl.lit(weight))
            .otherwise(weight_expression)
        )
        for fact_value, score in config.policy_support.value_scores[fact_type].items():
            value_expression = (
                pl.when((pl.col("fact_type") == fact_type) & (pl.col("fact_value") == fact_value))
                .then(pl.lit(score))
                .otherwise(value_expression)
            )
    scored = (
        accepted.sort(["institution_id", "fact_type", "valid_from"])
        .group_by(["institution_id", "fact_type"], maintain_order=True)
        .last()
        .with_columns(
            weight_expression.alias("_fact_weight"),
            value_expression.alias("_fact_score"),
        )
        .filter(pl.col("_fact_score").is_not_null())
        .with_columns((pl.col("_fact_weight") * pl.col("_fact_score")).alias("_weighted"))
        .group_by("institution_id")
        .agg(
            pl.col("_weighted").sum(),
            pl.col("_fact_weight").sum().alias("_observed_weight"),
            pl.len().cast(pl.UInt32).alias("policy_support_fact_count"),
        )
        .with_columns(
            (pl.col("_weighted") / pl.col("_observed_weight"))
            .clip(0, 100)
            .round(2)
            .alias("policy_support_score"),
            (pl.col("_observed_weight") / sum(config.policy_support.fact_weights.values()))
            .clip(0, 1)
            .round(4)
            .alias("policy_support_coverage"),
        )
        .with_columns(pl.col("policy_support_coverage").alias("policy_support_confidence"))
    )
    return scored.select(list(schema))


def _policy_columns(
    frame: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: ScoringConfig | ScoringV2Config,
) -> pl.DataFrame:
    result = frame.join(_policy_score_frame(facts, config), on="institution_id", how="left")
    return result.with_columns(
        pl.col("policy_support_coverage").fill_null(0.0),
        pl.col("policy_support_fact_count").fill_null(0),
        _band(pl.col("policy_support_score"), config.grade_bands).alias("policy_support_grade"),
        pl.when(pl.col("policy_support_score").is_not_null())
        .then(
            pl.lit(
                "Weighted only current, exact, human-reviewed policy facts; unreviewed and "
                "unstated facts are missing, not negative."
            )
        )
        .otherwise(pl.lit("No reviewed scoring fact is available; policy support is unknown."))
        .alias("policy_support_explanation"),
    )


def score_employers(frame: pl.DataFrame, config: ScoringConfig) -> pl.DataFrame:
    """Attach nullable component and composite scores to employer metrics."""

    return _immigration_columns(frame, config)


def score_institutions(
    frame: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: ScoringConfig,
) -> pl.DataFrame:
    """Attach immigration, research, policy, and research-pathway scores."""

    result = _immigration_columns(frame, config)
    result = _research_columns(result, config)
    result = _policy_columns(result, facts, config)
    result = _composite_columns(
        result,
        name="research_pathway",
        formula=config.composites.research_pathway,
        score_columns={
            "immigration_evidence": "immigration_evidence_score",
            "research_strength": "research_strength_score",
            "policy_support": "policy_support_score",
        },
        confidence_columns={
            "immigration_evidence": "immigration_evidence_confidence",
            "research_strength": "research_strength_confidence",
            "policy_support": "policy_support_confidence",
        },
        config=config,
    )
    return result.with_columns(pl.lit(config.version).alias("score_version"))


def _gated_grade(
    score: str,
    coverage: str,
    *,
    minimum_coverage: float,
    config: ScoringV2Config,
    extra_condition: pl.Expr | None = None,
) -> pl.Expr:
    """Return a letter grade only when every configured gate is satisfied."""

    eligible = pl.col(score).is_not_null() & (pl.col(coverage) >= minimum_coverage)
    if extra_condition is not None:
        eligible &= extra_condition
    return (
        pl.when(eligible)
        .then(_band(pl.col(score), config.grade_bands))
        .otherwise(pl.lit(None, dtype=pl.String))
    )


def _v2_history_columns(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    """Attach grade-aware V2 history components without changing V1 functions."""

    result = _h1b_columns(frame, config)
    result = _green_card_columns(result, config)
    h1b_complete = pl.col("h1b_history_score").is_not_null() & (
        pl.col("h1b_history_coverage") >= config.h1b_history.grade_minimum_coverage
    )
    green_card_complete = pl.col("green_card_history_score").is_not_null() & (
        pl.col("green_card_history_coverage") >= config.green_card_history.grade_minimum_coverage
    )
    return result.with_columns(
        _gated_grade(
            "h1b_history_score",
            "h1b_history_coverage",
            minimum_coverage=config.h1b_history.grade_minimum_coverage,
            config=config,
        ).alias("h1b_history_grade"),
        _gated_grade(
            "green_card_history_score",
            "green_card_history_coverage",
            minimum_coverage=config.green_card_history.grade_minimum_coverage,
            config=config,
        ).alias("green_card_history_grade"),
        pl.when(pl.col("h1b_history_score").is_null())
        .then(pl.lit("UNKNOWN"))
        .when(h1b_complete)
        .then(pl.lit("COMPLETE"))
        .otherwise(pl.lit("PARTIAL"))
        .alias("h1b_history_status"),
        pl.when(pl.col("green_card_history_score").is_null())
        .then(pl.lit("UNKNOWN"))
        .when(green_card_complete)
        .then(pl.lit("COMPLETE"))
        .otherwise(pl.lit("PARTIAL"))
        .alias("green_card_history_status"),
        pl.when(pl.col("h1b_history_score").is_not_null())
        .then(
            pl.format(
                "Observed {} relevant LCA records and {} USCIS initial approvals; "
                "component coverage is {}.",
                pl.col("relevant_lca_count"),
                pl.col("initial_approvals"),
                pl.col("h1b_history_coverage"),
            )
        )
        .otherwise(pl.lit("No observed LCA or USCIS history; missing evidence remains unknown."))
        .alias("h1b_history_explanation"),
        pl.when(pl.col("green_card_history_score").is_not_null())
        .then(
            pl.format(
                "Observed {} PERM records, including {} relevant certified technical records; "
                "component coverage is {}.",
                pl.col("perm_case_count"),
                pl.col("relevant_certified_perm_count"),
                pl.col("green_card_history_coverage"),
            )
        )
        .otherwise(pl.lit("No observed PERM history; absence remains unknown rather than refusal."))
        .alias("green_card_history_explanation"),
    )


def _sponsorship_history_columns(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    """Combine H-1B and green-card history independently of E-Verify and OPT."""

    formula = config.sponsorship_history
    component_scores = {
        "h1b_history": pl.col("h1b_history_score"),
        "green_card_history": pl.col("green_card_history_score"),
    }
    any_present = pl.any_horizontal([value.is_not_null() for value in component_scores.values()])
    score, availability_coverage = _weighted_component(
        component_scores,
        formula.weights,
        eligible=any_present,
    )
    confidence = pl.sum_horizontal(
        [
            pl.col("h1b_history_coverage").fill_null(0.0) * formula.weights["h1b_history"],
            pl.col("green_card_history_coverage").fill_null(0.0)
            * formula.weights["green_card_history"],
        ]
    ).clip(0, 1)
    complete = (
        (pl.col("h1b_history_status") == "COMPLETE")
        & (pl.col("green_card_history_status") == "COMPLETE")
        & (availability_coverage >= formula.grade_minimum_coverage)
    )
    result = frame.with_columns(
        score.alias("sponsorship_history_score"),
        availability_coverage.alias("sponsorship_history_coverage"),
        confidence.round(4).alias("sponsorship_history_confidence"),
        pl.when(~any_present)
        .then(pl.lit("UNKNOWN"))
        .when(complete)
        .then(pl.lit("COMPLETE"))
        .otherwise(pl.lit("PARTIAL"))
        .alias("sponsorship_history_status"),
    )
    return result.with_columns(
        _gated_grade(
            "sponsorship_history_score",
            "sponsorship_history_coverage",
            minimum_coverage=formula.grade_minimum_coverage,
            config=config,
            extra_condition=pl.col("sponsorship_history_status") == "COMPLETE",
        ).alias("sponsorship_history_grade"),
        _band(pl.col("sponsorship_history_confidence"), config.confidence_bands).alias(
            "sponsorship_history_confidence_band"
        ),
        pl.when(pl.col("sponsorship_history_status") == "COMPLETE")
        .then(
            pl.format(
                "Complete H-1B and green-card history: 40% H-1B ({} relevant LCA; {} "
                "initial approvals) and 60% green card ({} relevant certified PERM).",
                pl.col("relevant_lca_count"),
                pl.col("initial_approvals"),
                pl.col("relevant_certified_perm_count"),
            )
        )
        .when(
            pl.col("h1b_history_score").is_not_null() & pl.col("green_card_history_score").is_null()
        )
        .then(
            pl.format(
                "Partial score from the available 40% H-1B component ({} relevant LCA; {} "
                "initial approvals); green-card history is unknown.",
                pl.col("relevant_lca_count"),
                pl.col("initial_approvals"),
            )
        )
        .when(
            pl.col("green_card_history_score").is_not_null() & pl.col("h1b_history_score").is_null()
        )
        .then(
            pl.format(
                "Partial score from the available 60% green-card component ({} relevant "
                "certified PERM); H-1B history is unknown.",
                pl.col("relevant_certified_perm_count"),
            )
        )
        .when(pl.col("sponsorship_history_score").is_not_null())
        .then(
            pl.lit(
                "Both history components contribute, but one or more internal evidence fields "
                "remain incomplete; no letter grade is assigned."
            )
        )
        .otherwise(
            pl.lit(
                "No observed H-1B or green-card history is available; missing evidence was not "
                "converted to zero."
            )
        )
        .alias("sponsorship_history_explanation"),
    ).with_columns(pl.col("sponsorship_history_confidence_band").alias("evidence_confidence"))


def _core_policy_frame(facts: pl.DataFrame | None, config: ScoringV2Config) -> pl.DataFrame:
    """Measure completed review separately from substantive official evidence."""

    state_columns = [
        f"{fact_type}_review_state" for fact_type in config.core_policy.required_fact_types
    ]
    schema: dict[str, Any] = {
        "institution_id": pl.String,
        "core_policy_reviewed_count": pl.UInt32,
        "core_policy_evidence_count": pl.UInt32,
        "core_policy_review_coverage": pl.Float64,
        "core_policy_evidence_coverage": pl.Float64,
        "core_policy_profile_status": pl.String,
        "research_pathway_policy_blocker": pl.Boolean,
    }
    schema.update({column: pl.String for column in state_columns})
    if facts is None or facts.is_empty():
        return pl.DataFrame(schema=schema)

    current = facts.filter(
        pl.col("fact_type").is_in(config.core_policy.required_fact_types)
        & pl.col("is_current")
        & pl.col("valid_to").is_null()
    )
    if current.is_empty():
        return pl.DataFrame(schema=schema)

    official_url = pl.col("source_url").str.starts_with("https://").fill_null(False)
    substantive = (
        (pl.col("human_review_status") == config.core_policy.accepted_review_status)
        & pl.col("fact_value").is_in(config.core_policy.substantive_values)
        & pl.col("exact_excerpt_verified").fill_null(False)
        & official_url
    )
    reviewed_not_stated = (
        (pl.col("human_review_status") == config.core_policy.not_stated_review_status)
        & (pl.col("fact_value") == "NOT_STATED")
        & official_url
    )
    grouped = (
        current.with_columns(
            substantive.alias("_has_evidence"),
            reviewed_not_stated.alias("_reviewed_not_stated"),
            (pl.col("human_review_status") == "NEEDS_REVIEW").alias("_pending"),
            (pl.col("human_review_status") == "REVIEWED_REJECTED").alias("_rejected"),
            (
                substantive
                & (pl.col("fact_type") == "pr_research_staff_eligible")
                & (pl.col("fact_value") == "NO")
            ).alias("_policy_blocker"),
        )
        .group_by("institution_id", "fact_type")
        .agg(
            pl.col("_has_evidence").any(),
            pl.col("_reviewed_not_stated").any(),
            pl.col("_pending").any(),
            pl.col("_rejected").any(),
            pl.col("_policy_blocker").any(),
        )
        .with_columns(
            (pl.col("_has_evidence") | pl.col("_reviewed_not_stated")).alias("_reviewed"),
            pl.when(pl.col("_has_evidence"))
            .then(pl.lit("REVIEWED_EVIDENCE"))
            .when(pl.col("_reviewed_not_stated"))
            .then(pl.lit("REVIEWED_NOT_STATED"))
            .when(pl.col("_pending"))
            .then(pl.lit("REVIEW_PENDING"))
            .when(pl.col("_rejected"))
            .then(pl.lit("REVIEWED_REJECTED"))
            .otherwise(pl.lit("UNKNOWN"))
            .alias("_review_state"),
        )
    )
    wide = grouped.select("institution_id", "fact_type", "_review_state").pivot(
        on="fact_type",
        index="institution_id",
        values="_review_state",
    )
    for fact_type in config.core_policy.required_fact_types:
        if fact_type not in wide.columns:
            wide = wide.with_columns(pl.lit("UNKNOWN").alias(fact_type))
    wide = wide.select(
        "institution_id",
        *[
            pl.col(fact_type).fill_null("UNKNOWN").alias(f"{fact_type}_review_state")
            for fact_type in config.core_policy.required_fact_types
        ],
    )
    denominator = float(len(config.core_policy.required_fact_types))
    summary = (
        grouped.group_by("institution_id")
        .agg(
            pl.col("_reviewed").sum().cast(pl.UInt32).alias("core_policy_reviewed_count"),
            pl.col("_has_evidence").sum().cast(pl.UInt32).alias("core_policy_evidence_count"),
            pl.col("_policy_blocker").any().alias("research_pathway_policy_blocker"),
        )
        .with_columns(
            (pl.col("core_policy_reviewed_count") / denominator)
            .round(4)
            .alias("core_policy_review_coverage"),
            (pl.col("core_policy_evidence_count") / denominator)
            .round(4)
            .alias("core_policy_evidence_coverage"),
            pl.when(
                pl.col("core_policy_reviewed_count") == len(config.core_policy.required_fact_types)
            )
            .then(pl.lit("COMPLETE_REVIEW"))
            .when(pl.col("core_policy_reviewed_count") > 0)
            .then(pl.lit("PARTIAL_REVIEW"))
            .otherwise(pl.lit("NOT_REVIEWED"))
            .alias("core_policy_profile_status"),
        )
    )
    return summary.join(wide, on="institution_id", how="left").select(list(schema))


def _core_policy_columns(
    frame: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: ScoringV2Config,
) -> pl.DataFrame:
    result = frame.join(_core_policy_frame(facts, config), on="institution_id", how="left")
    state_columns = [
        f"{fact_type}_review_state" for fact_type in config.core_policy.required_fact_types
    ]
    return result.with_columns(
        pl.col("core_policy_reviewed_count").fill_null(0),
        pl.col("core_policy_evidence_count").fill_null(0),
        pl.col("core_policy_review_coverage").fill_null(0.0),
        pl.col("core_policy_evidence_coverage").fill_null(0.0),
        pl.col("core_policy_profile_status").fill_null("NOT_REVIEWED"),
        pl.col("research_pathway_policy_blocker").fill_null(False),
        *[pl.col(column).fill_null("UNKNOWN") for column in state_columns],
    )


def _v2_research_columns(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    result = _research_columns(frame, config)
    return result.with_columns(
        _gated_grade(
            "research_strength_score",
            "research_strength_coverage",
            minimum_coverage=config.research_strength.grade_minimum_coverage,
            config=config,
        ).alias("research_strength_grade"),
        pl.when(pl.col("research_strength_score").is_null())
        .then(pl.lit("UNKNOWN"))
        .when(
            pl.col("research_strength_coverage") >= config.research_strength.grade_minimum_coverage
        )
        .then(pl.lit("SUFFICIENT_COVERAGE"))
        .otherwise(pl.lit("PARTIAL"))
        .alias("research_strength_status"),
    )


def _v2_policy_columns(
    frame: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: ScoringV2Config,
) -> pl.DataFrame:
    result = _policy_columns(frame, facts, config)
    return result.with_columns(
        _gated_grade(
            "policy_support_score",
            "policy_support_coverage",
            minimum_coverage=config.policy_support.grade_minimum_coverage,
            config=config,
        ).alias("policy_support_grade"),
        pl.when(pl.col("policy_support_score").is_null())
        .then(pl.lit("UNKNOWN"))
        .when(pl.col("policy_support_coverage") >= config.policy_support.grade_minimum_coverage)
        .then(pl.lit("SUFFICIENT_COVERAGE"))
        .otherwise(pl.lit("PARTIAL"))
        .alias("policy_support_status"),
    )


def _research_pathway_v2_columns(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    formula = config.research_pathway
    component_scores = {
        "sponsorship_history": pl.col("sponsorship_history_score"),
        "policy_support": pl.col("policy_support_score"),
        "research_strength": pl.col("research_strength_score"),
    }
    any_present = pl.any_horizontal([value.is_not_null() for value in component_scores.values()])
    evidence_score, _ = _weighted_component(
        component_scores,
        formula.weights,
        eligible=any_present,
    )
    confidence = pl.sum_horizontal(
        [
            pl.col("sponsorship_history_confidence").fill_null(0.0)
            * formula.weights["sponsorship_history"],
            pl.col("policy_support_confidence").fill_null(0.0) * formula.weights["policy_support"],
            pl.col("research_strength_confidence").fill_null(0.0)
            * formula.weights["research_strength"],
        ]
    ).clip(0, 1)
    complete = (
        (pl.col("sponsorship_history_status") == "COMPLETE")
        & (pl.col("research_strength_coverage") >= config.research_strength.grade_minimum_coverage)
        & (pl.col("core_policy_review_coverage") == 1.0)
        & pl.all_horizontal([value.is_not_null() for value in component_scores.values()])
        & (confidence >= formula.grade_minimum_coverage)
        & ~pl.col("research_pathway_policy_blocker")
    )
    result = frame.with_columns(
        evidence_score.alias("research_pathway_evidence_score"),
        confidence.round(4).alias("research_pathway_coverage"),
        confidence.round(4).alias("research_pathway_confidence"),
    ).with_columns(
        pl.when(pl.col("research_pathway_policy_blocker"))
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("research_pathway_evidence_score"))
        .alias("research_pathway_score"),
        pl.when(pl.col("research_pathway_policy_blocker"))
        .then(pl.lit("POLICY_BLOCKED"))
        .when(complete)
        .then(pl.lit("COMPLETE"))
        .when(pl.col("research_pathway_evidence_score").is_not_null())
        .then(pl.lit("INCOMPLETE_EVIDENCE"))
        .otherwise(pl.lit("UNKNOWN"))
        .alias("research_pathway_status"),
    )
    return result.with_columns(
        _gated_grade(
            "research_pathway_score",
            "research_pathway_coverage",
            minimum_coverage=formula.grade_minimum_coverage,
            config=config,
            extra_condition=pl.col("research_pathway_status") == "COMPLETE",
        ).alias("research_pathway_grade"),
        pl.when(pl.col("research_pathway_status") == "POLICY_BLOCKED")
        .then(
            pl.lit(
                "Reviewed official policy states that research staff are ineligible for "
                "permanent-residence sponsorship; no pathway score or grade is published."
            )
        )
        .when(pl.col("research_pathway_status") == "COMPLETE")
        .then(
            pl.lit(
                "Complete 50% sponsorship history, 30% reviewed policy support, and 20% "
                "research strength with all four core policy questions reviewed."
            )
        )
        .when(pl.col("research_pathway_status") == "INCOMPLETE_EVIDENCE")
        .then(
            pl.format(
                "Numerical partial score only: coverage is {}; core-policy review coverage is "
                "{}. No letter grade is assigned.",
                pl.col("research_pathway_coverage"),
                pl.col("core_policy_review_coverage"),
            )
        )
        .otherwise(pl.lit("Insufficient evidence for a research-pathway score."))
        .alias("research_pathway_explanation"),
    )


def _optional_column(frame: pl.DataFrame, name: str, default: object) -> pl.Expr:
    return pl.col(name) if name in frame.columns else pl.lit(default)


def _decision_readiness_columns(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    """Compute the evidence tier while exposing the unresolved quality-gate prerequisite."""

    history_complete = (pl.col("h1b_history_status") == "COMPLETE") & (
        pl.col("green_card_history_status") == "COMPLETE"
    )
    some_history = (
        pl.col("h1b_history_score").is_not_null() | pl.col("green_card_history_score").is_not_null()
    )
    current_ipeds_identity = (
        _optional_column(frame, "institution_id", None).cast(pl.String).str.starts_with("ipeds:")
        & _optional_column(frame, "ipeds_unitid", None).is_not_null()
        & (_optional_column(frame, "active_status", "UNKNOWN") == "ACTIVE")
    )
    valid_linkage = (
        _optional_column(frame, "legal_entity_id", None).is_not_null()
        & _optional_column(frame, "organization_id", None).is_not_null()
    )
    evidence_tier = (
        pl.when(
            history_complete
            & (pl.col("core_policy_review_coverage") == 1.0)
            & current_ipeds_identity
            & valid_linkage
        )
        .then(pl.lit("TIER_1_REVIEWED"))
        .when(history_complete & (pl.col("core_policy_review_coverage") < 1.0))
        .then(pl.lit("TIER_2_STRONG_HISTORY_POLICY_INCOMPLETE"))
        .when(some_history & ~history_complete)
        .then(pl.lit("TIER_3_PARTIAL_HISTORY"))
        .otherwise(pl.lit("TIER_4_INSUFFICIENT_EVIDENCE"))
    )
    return frame.with_columns(
        evidence_tier.alias("decision_readiness_evidence_tier"),
        evidence_tier.alias("decision_readiness_tier"),
        pl.lit("PENDING_QUALITY_GATE").alias("decision_readiness_prerequisite_status"),
        pl.lit(False).alias("decision_readiness_tier_is_final"),
    ).with_columns(
        pl.format(
            "{} is the deterministic evidence tier. Final publication eligibility is pending "
            "the current build's critical quality gate; the tier is not a sponsorship "
            "probability.",
            pl.col("decision_readiness_evidence_tier"),
        ).alias("decision_readiness_explanation")
    )


def score_employers_v2(frame: pl.DataFrame, config: ScoringV2Config) -> pl.DataFrame:
    """Attach canonical V2 evidence scores while preserving the independent V1 engine."""

    result = _stem_opt_columns(frame, config)
    result = _v2_history_columns(result, config)
    result = _sponsorship_history_columns(result, config)
    return result.with_columns(pl.lit(config.version).alias("score_version"))


def score_institutions_v2(
    frame: pl.DataFrame,
    facts: pl.DataFrame | None,
    config: ScoringV2Config,
) -> pl.DataFrame:
    """Attach canonical V2 policy coverage, pathway scores, and readiness tiers."""

    result = score_employers_v2(frame, config)
    result = _v2_research_columns(result, config)
    result = _v2_policy_columns(result, facts, config)
    result = _core_policy_columns(result, facts, config)
    result = _research_pathway_v2_columns(result, config)
    result = _decision_readiness_columns(result, config)
    return result.with_columns(pl.lit(config.version).alias("score_version"))
