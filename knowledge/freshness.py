from __future__ import annotations

import re
from .models import FreshnessClass


def classify_freshness(title: str, headings: list[str] | None = None) -> FreshnessClass:
    text = f"{title} {' '.join(headings or [])}".casefold()
    if re.search(r"\b(current|incumbent|upcoming|season|leadership|chief executive|version history|product line)\b", text):
        return FreshnessClass.CURRENT
    if re.search(r"\b(city|company|corporation|programming language|software|character|technology|film|television)\b", text):
        return FreshnessClass.SLOW
    return FreshnessClass.STATIC


def freshness_warning(freshness: FreshnessClass, retrieved_at: str | None, asks_current: bool) -> str | None:
    if asks_current and freshness is FreshnessClass.CURRENT:
        return f"Local information is a snapshot retrieved {retrieved_at or 'at an unknown time'} and should not be presented as current without verification."
    return None
