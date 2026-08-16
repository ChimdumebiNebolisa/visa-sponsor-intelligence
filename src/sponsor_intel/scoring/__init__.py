"""Versioned evidence-strength scoring."""

from sponsor_intel.scoring.engine import (
    score_employers,
    score_employers_product_a,
    score_employers_v2,
    score_institutions,
    score_institutions_product_a,
    score_institutions_v2,
)
from sponsor_intel.scoring.models import (
    DEFAULT_PRODUCT_A_SCORING_CONFIG_PATH,
    DEFAULT_SCORING_CONFIG_PATH,
    DEFAULT_SCORING_V2_CONFIG_PATH,
    ProductAScoringConfig,
    ScoringConfig,
    ScoringV2Config,
)

__all__ = [
    "DEFAULT_PRODUCT_A_SCORING_CONFIG_PATH",
    "DEFAULT_SCORING_CONFIG_PATH",
    "DEFAULT_SCORING_V2_CONFIG_PATH",
    "ProductAScoringConfig",
    "ScoringConfig",
    "ScoringV2Config",
    "score_employers",
    "score_employers_product_a",
    "score_employers_v2",
    "score_institutions",
    "score_institutions_product_a",
    "score_institutions_v2",
]
