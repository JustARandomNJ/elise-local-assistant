from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Sequence


def evaluate_predictions(expected: Sequence[dict[str, Any]], predicted: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Score offline classifier/policy predictions without network or subprocesses."""
    predictions = {str(row["id"]): row for row in predicted}
    counts: Counter[str] = Counter()
    for truth in expected:
        guess = predictions.get(str(truth["id"]), {})
        wanted = bool(truth.get("candidate", False))
        got = bool(guess.get("candidate", False))
        counts["true_positive"] += int(wanted and got)
        counts["false_positive"] += int(not wanted and got)
        counts["false_negative"] += int(wanted and not got)
        counts["privacy_total"] += int("privacy" in truth)
        counts["privacy_correct"] += int("privacy" in truth and guess.get("privacy") == truth["privacy"])
        counts["secret_total"] += int(truth.get("privacy") == "secret")
        counts["secret_rejected"] += int(truth.get("privacy") == "secret" and guess.get("action") == "reject_secret")
        counts["relation_total"] += int("relation" in truth)
        counts["relation_correct"] += int("relation" in truth and guess.get("relation") == truth["relation"])
        counts["duplicate"] += int(guess.get("action") == "duplicate")
        counts["inappropriate"] += int(not wanted and got)
    precision_denominator = counts["true_positive"] + counts["false_positive"]
    recall_denominator = counts["true_positive"] + counts["false_negative"]
    size = max(1, len(expected))
    return {
        "memory_precision": counts["true_positive"] / max(1, precision_denominator),
        "memory_recall": counts["true_positive"] / max(1, recall_denominator),
        "privacy_tier_accuracy": counts["privacy_correct"] / max(1, counts["privacy_total"]),
        "secret_rejection_rate": counts["secret_rejected"] / max(1, counts["secret_total"]),
        "duplicate_rate": counts["duplicate"] / size,
        "contradiction_resolution_accuracy": counts["relation_correct"] / max(1, counts["relation_total"]),
        "inappropriate_memory_rate": counts["inappropriate"] / size,
    }


def load_corpus(path: str | Path = "passive_memory_eval.json") -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
