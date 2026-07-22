"""Shared rental-term rules used by imports, storage and dashboard quotes."""

from __future__ import annotations

import re


RENTAL_TERM_SHORT = "short"
RENTAL_TERM_LONG = "long"
RENTAL_TERM_UNKNOWN = "unknown"

RENTAL_TERM_LABELS = {
    RENTAL_TERM_SHORT: "短租",
    RENTAL_TERM_LONG: "长租",
    RENTAL_TERM_UNKNOWN: "类型未知",
}

# Inclusive day ranges confirmed from the platform rental screens.
PLATFORM_TERM_RANGES = {
    "IGXE": {
        RENTAL_TERM_SHORT: (1.0, 14.0),
        RENTAL_TERM_LONG: (15.0, 60.0),
    },
    "ECOSteam": {
        RENTAL_TERM_SHORT: (1.0, 21.0),
        RENTAL_TERM_LONG: (22.0, 45.0),
    },
    "C5GAME": {
        RENTAL_TERM_SHORT: (8.0, 21.0),
        RENTAL_TERM_LONG: (22.0, 45.0),
    },
}


def normalize_rental_term(value) -> str:
    """Normalize stored/API values without guessing an unknown label."""
    normalized = str(value or "").strip().casefold()
    if normalized in {"short", "短租"}:
        return RENTAL_TERM_SHORT
    if normalized in {"long", "长租"}:
        return RENTAL_TERM_LONG
    return RENTAL_TERM_UNKNOWN


def classify_rental_term(platform, rental_days, raw_text="", explicit_term="") -> str:
    """Classify an order by explicit page label first, then confirmed day ranges."""
    normalized_explicit = normalize_rental_term(explicit_term)
    if normalized_explicit != RENTAL_TERM_UNKNOWN:
        return normalized_explicit

    text = str(raw_text or "")
    label_match = re.search(r"(?:租期|租赁|×\s*[0-9.]+\s*[（(])[^\n]{0,30}?(短租|长租)", text)
    if label_match:
        return normalize_rental_term(label_match.group(1))

    try:
        days = float(rental_days or 0.0)
    except (TypeError, ValueError):
        return RENTAL_TERM_UNKNOWN
    if days <= 0:
        return RENTAL_TERM_UNKNOWN

    ranges = PLATFORM_TERM_RANGES.get(str(platform or "").strip(), {})
    for term in (RENTAL_TERM_SHORT, RENTAL_TERM_LONG):
        minimum, maximum = ranges.get(term, (0.0, 0.0))
        if minimum <= days <= maximum:
            return term
    return RENTAL_TERM_UNKNOWN


def rental_term_label(value) -> str:
    return RENTAL_TERM_LABELS[normalize_rental_term(value)]
