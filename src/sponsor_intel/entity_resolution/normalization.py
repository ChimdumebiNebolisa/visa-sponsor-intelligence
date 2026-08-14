"""Non-destructive candidate-generation normalization."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from sponsor_intel.entity_resolution.models import EntityResolutionConfig

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^A-Z0-9]+")
_ACRONYM_STOPWORDS = {"AND", "AT", "FOR", "OF", "THE"}
_STATE_CODES = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICTOFCOLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "GUAM": "GU",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEWHAMPSHIRE": "NH",
    "NEWJERSEY": "NJ",
    "NEWMEXICO": "NM",
    "NEWYORK": "NY",
    "NORTHCAROLINA": "NC",
    "NORTHDAKOTA": "ND",
    "NORTHERNMARIANAISLANDS": "MP",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "PUERTORICO": "PR",
    "RHODEISLAND": "RI",
    "SOUTHCAROLINA": "SC",
    "SOUTHDAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "VIRGINISLANDS": "VI",
    "WASHINGTON": "WA",
    "WESTVIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}


def _fold_unicode(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def normalize_name(value: str | None, config: EntityResolutionConfig) -> str:
    """Normalize a name for matching while leaving source values untouched."""

    if value is None:
        return ""
    normalized = _fold_unicode(value).upper().strip()
    normalized = normalized.replace("&", " AND ")
    normalized = _PUNCTUATION.sub(" ", normalized)
    tokens = [config.abbreviations.get(token, token) for token in normalized.split()]
    return _WHITESPACE.sub(" ", " ".join(tokens)).strip()


def core_name(normalized_name: str, config: EntityResolutionConfig) -> str:
    tokens = normalized_name.split()
    suffixes = {config.abbreviations.get(suffix, suffix) for suffix in config.legal_suffixes}
    while tokens and tokens[-1] in suffixes:
        tokens.pop()
    if tokens[:1] == ["THE"]:
        tokens = tokens[1:]
    return " ".join(tokens)


def legal_suffix(normalized_name: str, config: EntityResolutionConfig) -> str:
    """Return the normalized terminal legal suffix, when one is present."""

    tokens = normalized_name.split()
    suffixes = {config.abbreviations.get(suffix, suffix) for suffix in config.legal_suffixes}
    return tokens[-1] if tokens and tokens[-1] in suffixes else ""


def name_acronym(normalized_name: str) -> str:
    tokens = [
        token
        for token in normalized_name.split()
        if token not in _ACRONYM_STOPWORDS and not token.isdigit()
    ]
    return "".join(token[0] for token in tokens if token)


def normalize_city(value: str | None) -> str:
    if value is None:
        return ""
    normalized = _fold_unicode(value).upper().strip()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", normalized)).strip()


def normalize_state(value: str | None) -> str:
    if value is None:
        return ""
    compact = _PUNCTUATION.sub("", _fold_unicode(value).upper())
    return _STATE_CODES.get(compact, compact[:3])


def normalize_postal_code(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())[:5]


def stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"
