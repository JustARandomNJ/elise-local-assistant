from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

from memory import MemoryStore
from passive_memory import (
    MemoryAction,
    MemoryDecisionLog,
    MemoryPolicyThresholds,
    PassiveMemoryEngine,
    PassiveMemorySettings,
    contains_secret,
)
from passive_memory_evaluation import evaluate_predictions, load_corpus


class FakeVault:
    def __init__(self, unlocked: bool = True) -> None:
        self.is_unlocked = unlocked
        self.records: list[dict[str, object]] = []

    def add(self, **record: object) -> str:
        self.records.append(record)
        return f"S{len(self.records)}"


def candidate(content: str, *, category: str = "preference", durability: str = "durable",
              privacy: str = "ordinary", confidence: float = .97, usefulness: float = .92,
              relation: str = "new", related_memory_id: int | None = None,
              expiration: str | None = None, describes_user: bool = True) -> dict[str, object]:
    return {
        "content": content, "category": category, "durability": durability,
        "privacy": privacy, "retrieval_policy": "when_relevant",
        "confidence": confidence, "usefulness": usefulness, "expiration": expiration,
        "relation": relation, "related_memory_id": related_memory_id,
        "describes_user": describes_user,
    }


class PassiveMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="elise-passive-")
        root = Path(self.temp.name)
        self.store = MemoryStore(root / "memory.db")
        self.settings = PassiveMemorySettings(root / "settings.json")
        self.log = MemoryDecisionLog(root / "decisions.json")
        self.vault = FakeVault()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def engine(self, result: object, *, vault: FakeVault | None = None,
               thresholds: MemoryPolicyThresholds | None = None) -> PassiveMemoryEngine:
        return PassiveMemoryEngine(memory_store=self.store, private_vault=vault or self.vault,
            settings=self.settings, decision_log=self.log, classify=lambda _text, _existing: result,
            thresholds=thresholds)

    def test_ordinary_preference_auto_saves_and_retrieves(self) -> None:
        result = self.engine(candidate("User prefers explanations with concrete examples.")).process(
            "I prefer explanations with examples."
        )
        self.assertEqual(MemoryAction.AUTO_STORE, result.action)
        found = self.store.search("concrete examples explanations", log_retrieval=False)
        self.assertTrue(found)

    def test_ephemeral_and_short_term_without_expiration_discard(self) -> None:
        tired = self.engine(candidate("User is tired tonight.", durability="ephemeral", category="fact")).process("I'm tired tonight.")
        trip = self.engine(candidate("User is visiting San Diego this weekend.", durability="short_term", category="fact")).process("I'm visiting San Diego this weekend.")
        self.assertEqual(MemoryAction.DISCARD, tired.action)
        self.assertEqual(MemoryAction.DISCARD, trip.action)

    def test_durable_project_auto_saves(self) -> None:
        result = self.engine(candidate("User is building a compiler as their main project.", category="project", privacy="personal")).process("I'm building a compiler as my main project.")
        self.assertEqual(MemoryAction.AUTO_STORE, result.action)
        self.assertEqual("personal", self.store.get(int(result.memory_id))["privacy_level"])

    def test_sensitive_requires_unlocked_vault_and_is_explicit_only(self) -> None:
        proposed = candidate("User has a durable sensitive health condition.", category="fact", privacy="sensitive", confidence=.995, usefulness=.95)
        saved = self.engine(proposed).process("I have a durable sensitive health condition that affects future planning.")
        self.assertEqual(MemoryAction.ENCRYPTED_STORE, saved.action)
        self.assertEqual("explicit_only", self.vault.records[0]["retrieval_policy"])
        self.assertEqual([], self.store.list_all())
        locked = FakeVault(False)
        discarded = self.engine(proposed, vault=locked).process("I have a durable sensitive health condition that affects future planning.")
        self.assertEqual(MemoryAction.DISCARD, discarded.action)
        self.assertEqual([], locked.records)
        self.assertEqual([], self.store.list_all())

    def test_secrets_rejected_before_classifier_and_not_logged(self) -> None:
        calls: list[str] = []
        engine = PassiveMemoryEngine(memory_store=self.store, private_vault=self.vault,
            settings=self.settings, decision_log=self.log,
            classify=lambda text, existing: calls.append(text))
        secrets = ["My API key is sk-abcdefghijklmnop1234", "My password is: CorrectHorseBatteryStaple"]
        for text in secrets:
            self.assertEqual(MemoryAction.REJECT_SECRET, engine.process(text).action)
        self.assertEqual([], calls)
        log_text = self.log.path.read_text(encoding="utf-8")
        self.assertNotIn("CorrectHorse", log_text)
        self.assertNotIn("abcdefghijklmnop", log_text)
        self.assertTrue(contains_secret("SSN: 123-45-6789"))

    def test_user_overrides_and_excluded_transforms(self) -> None:
        durable = candidate("User prefers dark mode interfaces.")
        self.assertEqual(MemoryAction.DISCARD, self.engine(durable).process("Don't remember this: I prefer dark mode.").action)
        self.assertEqual(MemoryAction.DISCARD, self.engine(durable).process("Rewrite this sentence: I prefer dark mode.").action)
        self.assertEqual(MemoryAction.DISCARD, self.engine(durable).process("Translate: I prefer dark mode.").action)
        private = self.engine(durable).process("Keep this private: I prefer dark mode interfaces.")
        self.assertEqual("personal", private.privacy)

    def test_duplicate_refinement_and_contradiction(self) -> None:
        self.store.add("User prefers concise answers.", "preference")
        memory_id = int(self.store.list_all()[0]["id"])
        duplicate = self.engine(candidate("User likes concise answers.", relation="duplicate", related_memory_id=memory_id)).process("I like short answers.")
        self.assertEqual(MemoryAction.DUPLICATE, duplicate.action)
        refinement = self.engine(candidate("User prefers concise answers with code examples.", relation="refinement", related_memory_id=memory_id)).process("I prefer concise answers with code examples.")
        self.assertEqual(MemoryAction.UPDATED, refinement.action)
        contradiction = self.engine(candidate("User prefers detailed answers with examples.", relation="contradiction", related_memory_id=memory_id)).process("I now prefer detailed answers with examples.")
        self.assertEqual(MemoryAction.UPDATED, contradiction.action)
        self.assertEqual(1, len(self.store.list_all()))
        self.assertIn("detailed", self.store.get(memory_id)["content"])

    def test_personal_retrieval_is_stricter_and_sensitive_never_in_sqlite(self) -> None:
        self.store.add("User uses embedded firmware.", "fact", privacy_level="ordinary")
        self.store.add("User has an embedded responsibility.", "fact", privacy_level="personal")
        results = self.store.search("embedded", min_relevance=14.0, log_retrieval=False)
        self.assertTrue(any(row["privacy_level"] == "ordinary" for row in results))
        self.assertFalse(any(row["privacy_level"] == "personal" for row in results))
        self.assertFalse(any(row["privacy_level"] == "sensitive" for row in self.store.list_all()))

    def test_malformed_timeout_and_database_failure_fail_closed(self) -> None:
        self.assertEqual(MemoryAction.DISCARD, self.engine("not-json").process("I prefer concrete examples.").action)
        short = MemoryPolicyThresholds(classifier_timeout_seconds=.01)
        slow = PassiveMemoryEngine(memory_store=self.store, private_vault=self.vault,
            settings=self.settings, decision_log=self.log,
            classify=lambda _text, _existing: (time.sleep(.1), {})[1], thresholds=short)
        self.assertEqual(MemoryAction.DISCARD, slow.process("I prefer concrete examples.").action)
        original = self.store.add
        self.store.add = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("db down"))
        try:
            self.assertEqual(MemoryAction.DISCARD, self.engine(candidate("User prefers concrete examples.")).process("I prefer concrete examples.").action)
        finally:
            self.store.add = original

    def test_settings_pending_and_explicit_memory_independence(self) -> None:
        self.settings.set("enabled", False)
        self.assertEqual(MemoryAction.DISCARD, self.engine(candidate("User prefers examples in explanations.")).process("I prefer examples.").action)
        self.assertTrue(self.store.add("Explicitly remembered preference.", "preference"))
        self.settings.set("enabled", True)
        borderline = self.engine(candidate("User prefers diagrams for architecture.", confidence=.85, usefulness=.8)).process("I prefer diagrams for architecture.")
        self.assertEqual(MemoryAction.PENDING_REVIEW, borderline.action)
        self.assertEqual(1, self.store.count_pending_suggestions())
        self.assertNotIn("I prefer diagrams", self.log.path.read_text(encoding="utf-8"))

    def test_unknown_schema_fields_and_model_commands_are_rejected(self) -> None:
        bad = candidate("User prefers concrete examples.")
        bad["tool"] = "delete_memory"
        self.assertEqual(MemoryAction.DISCARD, self.engine(bad).process("I prefer concrete examples.").action)

    def test_evaluation_metrics_are_deterministic(self) -> None:
        corpus = load_corpus(Path(__file__).with_name("passive_memory_eval.json"))
        perfect = [dict(row) for row in corpus]
        metrics = evaluate_predictions(corpus, perfect)
        self.assertEqual(1.0, metrics["memory_precision"])
        self.assertEqual(1.0, metrics["memory_recall"])
        self.assertEqual(1.0, metrics["privacy_tier_accuracy"])
        self.assertEqual(1.0, metrics["secret_rejection_rate"])
        self.assertEqual(0.0, metrics["inappropriate_memory_rate"])


if __name__ == "__main__":
    unittest.main()
