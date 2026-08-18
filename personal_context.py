from __future__ import annotations

import re

from text_normalization import normalize_apostrophes


_PERSONAL_CONTEXT_PATTERNS = (
    re.compile(r"\b(?:describe|summarize)\s+me\b"),
    re.compile(r"\bwho\s+am\s+i\b"),
    re.compile(r"\btell\s+me\s+about\s+myself\b"),
    re.compile(r"\bwhat\s+do\s+you\s+(?:know|remember|recall)\s+about\s+me\b"),
    re.compile(
        r"\bwhat\s+do\s+you\s+(?:know|remember|recall)\s+about\s+my\s+"
        r"(?:identity|preferences?|goals?|projects?|history|background|choices|decisions|plans?)\b"
    ),
    re.compile(r"\bwhat\s+(?:did|have)\s+i\s+(?:tell|share|mention)(?:ed)?\s+(?:you\s+)?about\b"),
    re.compile(r"\bwhat\s+(?:did|have)\s+i\s+(?:tell|share|mention)(?:ed)?\s+you\b"),
    re.compile(r"\bwhat\s+(?:have\s+)?we\s+(?:discussed|decided|chosen|talked\s+about)\b"),
    re.compile(
        r"\b(?:what|which)\s+(?:are|were|is|was)\s+my\s+"
        r"(?:identity|preferences?|goals?|projects?|priorities|choices|decisions|history|background|plans?)\b"
    ),
    re.compile(r"\bwhat\s+projects?\s+(?:am|was)\s+i\s+working\s+on\b"),
    re.compile(r"\bwhat\s+(?:choices|decisions)\s+(?:did|have)\s+i\s+(?:make|made)\b"),
    re.compile(r"\bwhat\s+facts?\s+(?:did|have)\s+i\s+(?:share|shared|mention|mentioned|tell|told)\b"),
    re.compile(r"\b(?:remind|tell)\s+me\s+(?:about|of)\s+my\s+(?:preferences?|goals?|projects?|history|choices|decisions|background|plans?)\b"),
)


def is_personal_context_query(message: str) -> bool:
    """Return whether a message explicitly asks for remembered user context."""

    normalized = " ".join(normalize_apostrophes(message).casefold().split())
    return any(pattern.search(normalized) for pattern in _PERSONAL_CONTEXT_PATTERNS)
