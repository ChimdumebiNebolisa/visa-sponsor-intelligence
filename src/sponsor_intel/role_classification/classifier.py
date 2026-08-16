"""Ordered deterministic SOC/title role classification."""

from __future__ import annotations

import re
import unicodedata

from sponsor_intel.role_classification.models import (
    CombinedRule,
    RoleClassification,
    RoleTaxonomyConfig,
    TitleRule,
)

_PUNCTUATION = re.compile(r"[^A-Z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_title(value: str | None) -> str:
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", folded.upper())).strip()


def normalize_soc_code(value: str | None) -> str:
    if value is None:
        return ""
    match = re.search(r"\d{2}-\d{4}(?:\.\d{2})?", value)
    return match.group(0) if match else ""


def _matches(rule: TitleRule | CombinedRule, title: str) -> bool:
    if any(re.search(pattern, title) for pattern in rule.exclude_patterns):
        return False
    return any(re.search(pattern, title) for pattern in rule.patterns)


class RoleClassifier:
    """Apply the specification's classification precedence without an LLM."""

    def __init__(self, config: RoleTaxonomyConfig) -> None:
        self.config = config
        self._overrides = {
            normalize_title(item.title): item for item in config.exact_title_overrides
        }

    def _result(
        self,
        technical_role: bool | None,
        family: str,
        confidence: float,
        method: str,
        rule: str,
    ) -> RoleClassification:
        review = (
            "NEEDS_REVIEW"
            if technical_role is None
            or family == "ambiguous"
            or confidence < self.config.review_confidence_threshold
            else "NOT_REQUIRED"
        )
        return RoleClassification(
            technical_role=technical_role,
            role_family=family,
            role_confidence=confidence,
            classification_method=method,
            classification_rule=rule,
            classification_version=self.config.classification_version,
            review_status=review,
        )

    def classify(self, title_raw: str | None, soc_code_raw: str | None) -> RoleClassification:
        title = normalize_title(title_raw)
        soc_code = normalize_soc_code(soc_code_raw)

        override = self._overrides.get(title)
        if override is not None:
            return self._result(
                override.technical_role,
                override.role_family,
                override.confidence,
                "EXACT_REVIEWED_TITLE",
                f"reviewed:{title}",
            )

        positive_rule = next(
            (
                rule
                for rule in self.config.positive_title_rules
                if rule.role_family is not None and _matches(rule, title)
            ),
            None,
        )
        exclusion_rule = next(
            (rule for rule in self.config.exclusion_rules if _matches(rule, title)), None
        )
        if (
            exclusion_rule is not None
            and exclusion_rule.rule_id == "noncomputing_faculty"
            and soc_code.startswith("25-1021")
        ):
            # 25-1021 is the specific Computer Science Teachers SOC, so a
            # generic faculty title is not evidence of *noncomputing* faculty.
            exclusion_rule = None

        # A clearly non-target title must not become technical solely because a
        # disclosure row carries a broad computing SOC code. When a complete
        # title independently supplies strong technical evidence, keep it in the
        # target universe rather than letting a contextual exclusion overreach.
        if exclusion_rule is not None:
            if positive_rule is not None:
                assert positive_rule.role_family is not None
                return self._result(
                    True,
                    positive_rule.role_family,
                    positive_rule.confidence,
                    "STRONG_TITLE_PATTERN",
                    positive_rule.rule_id,
                )
            return self._result(
                False,
                "not_relevant",
                exclusion_rule.confidence,
                "STRONG_EXCLUSION_PATTERN",
                exclusion_rule.rule_id,
            )

        for index, mapping in enumerate(self.config.soc_mappings):
            if any(soc_code.startswith(prefix) for prefix in mapping.prefixes):
                return self._result(
                    True,
                    mapping.role_family,
                    mapping.confidence,
                    "SOC_MAPPING",
                    f"soc_mapping:{index}",
                )

        if positive_rule is not None:
            assert positive_rule.role_family is not None
            return self._result(
                True,
                positive_rule.role_family,
                positive_rule.confidence,
                "STRONG_TITLE_PATTERN",
                positive_rule.rule_id,
            )

        for rule in self.config.combined_rules:
            if any(soc_code.startswith(prefix) for prefix in rule.soc_prefixes) and _matches(
                rule, title
            ):
                return self._result(
                    True,
                    rule.role_family,
                    rule.confidence,
                    "COMBINED_SOC_TITLE",
                    rule.rule_id,
                )

        if not title and not soc_code:
            return self._result(None, "ambiguous", 0.0, "MISSING_EVIDENCE", "missing")
        if any(re.search(pattern, title) for pattern in self.config.ambiguous_title_patterns):
            return self._result(None, "ambiguous", 0.50, "AMBIGUOUS_TITLE", "ambiguous_pattern")
        return self._result(
            False,
            "not_relevant",
            0.90,
            "DEFAULT_NOT_RELEVANT",
            "no_technical_evidence",
        )
