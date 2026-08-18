from __future__ import annotations


def normalize_apostrophes(value: str) -> str:
    """Normalize supported apostrophe encodings for phrase matching."""

    return value.replace("\u2019", "'").replace("\u00e2\u20ac\u2122", "'")
