from __future__ import annotations

import unittest

from natural_command_intents import PendingSelections, inventory, resolve_natural_command


class NaturalCommandIntentTests(unittest.TestCase):
    def assert_routes(self, phrase: str, canonical: str) -> None:
        resolved = resolve_natural_command(phrase)
        self.assertIsNotNone(resolved, phrase)
        self.assertEqual(canonical, resolved.canonical, phrase)

    def test_direct_and_alternative_forms(self) -> None:
        cases = (
            ("turn the internet on", "/internet on"),
            ("enable web access", "/internet on"),
            ("go offline", "/internet off"),
            ("are you online?", "/internet status"),
            ("show your memories", "/memories"),
            ("list my stored memories", "/memories"),
            ("search your memories for embedded systems", "/search-memories embedded systems"),
            ("what do you remember about embedded systems?", "/search-memories embedded systems"),
            ("show my indexed documents", "/documents"),
            ("what documents do you have?", "/documents"),
            ("search my documents for CAN bus", "/search CAN bus"),
            ("find CAN bus in my documents", "/search CAN bus"),
            ("is Spotify connected?", "/spotify-status"),
            ("check Spotify", "/spotify-status"),
            ("show my Spotify devices", "/spotify-devices"),
            ("where can you play music?", "/spotify-devices"),
            ("enable ASCII art", "/ascii on"),
            ("stop using ASCII art", "/ascii off"),
            ("is ASCII mode on?", "/ascii status"),
            ("show available tools", "/tools"),
            ("what are the tool permission rules?", "/tool-permissions"),
            ("show recent workflows", "/workflows"),
            ("check workflow 7", "/workflow 7"),
            ("run workflow 7", "/run-workflow 7"),
            ("abort workflow 7", "/cancel-workflow 7"),
            ("what time is it?", "/time"),
        )
        for phrase, canonical in cases:
            with self.subTest(phrase=phrase):
                self.assert_routes(phrase, canonical)

    def test_argument_extraction_and_sensitive_parity(self) -> None:
        cases = (
            ("remember as a preference that I like tea", "/remember preference I like tea"),
            ("delete stored memory 12", "/forget 12"),
            ("approve memory suggestion 4", "/approve-memory 4"),
            ("reject memory suggestion 4", "/reject-memory 4"),
            ("search the web for Python 3.15", "/web-search Python 3.15"),
            ("show the last 5 workflows", "/workflows 5"),
        )
        for phrase, canonical in cases:
            with self.subTest(phrase=phrase):
                self.assert_routes(phrase, canonical)
        self.assertEqual("C", resolve_natural_command("delete stored memory 12").intent.risk_level.value)
        self.assertFalse(resolve_natural_command("delete stored memory 12").intent.requires_confirmation)
        self.assertTrue(resolve_natural_command("run workflow 7").intent.requires_confirmation)

    def test_negation_and_informational_discussion_fall_through(self) -> None:
        blocked = (
            "don't turn the internet on",
            "don’t use Spotify",
            "don't delete stored memory 12",
            "don't search my documents for CAN bus",
            "don't use tools",
            "don't play music",
            "don't enable ASCII art",
            "what does /internet on do?",
            "how does your memory search work?",
            "what is ASCII mode?",
            "what happens if I run /forget?",
            "can you use Spotify?",
        )
        for phrase in blocked:
            with self.subTest(phrase=phrase):
                self.assertIsNone(resolve_natural_command(phrase))

    def test_collisions_remain_conversation_or_specialized(self) -> None:
        normal = (
            "play Home",
            "show me how linked lists work",
            "remember when Spider-Man fought Green Goblin?",
            "play is important in child development",
            "search algorithms are interesting",
            "use recursion here?",
            "open addressing is a hash-table technique",
            "what devices are used in embedded systems?",
            "what do you know about me?",
            "write HELLO in ascii",
            "draw a cat in ascii",
            "show me Spotify results for X",
            "show me videos about X",
            "play Alpharad's latest video",
        )
        for phrase in normal:
            with self.subTest(phrase=phrase):
                self.assertIsNone(resolve_natural_command(phrase))

    def test_contextual_selection_namespaces_and_names(self) -> None:
        music = PendingSelections(music=("Pink + White Frank Ocean", "Ivy Frank Ocean"))
        self.assertEqual("/music-select 2", resolve_natural_command("play the second one", music).canonical)
        self.assertEqual("/music-select 1", resolve_natural_command("choose Pink + White result", music).canonical)
        creator = PendingSelections(creator=("Alpharad", "Alpha Gaming"))
        self.assertEqual("/creator-select 1", resolve_natural_command("use the Alpharad channel", creator).canonical)
        video = PendingSelections(video=("First video", "Second video", "Third video"))
        self.assertEqual("/video-select 3", resolve_natural_command("play the third video", video).canonical)
        devices = PendingSelections(spotify_device=("Living Room", "NJSLAPTOP"))
        self.assertEqual("/spotify-device 2", resolve_natural_command("use NJSLAPTOP for Spotify", devices).canonical)
        self.assertEqual("/spotify-device 2", resolve_natural_command("switch Spotify to the second device", devices).canonical)
        self.assertIsNone(resolve_natural_command("the second one", PendingSelections(music=("A", "B"), video=("C", "D"))))

    def test_registry_metadata_and_slash_commands(self) -> None:
        self.assertGreaterEqual(len(inventory()), 40)
        for intent in inventory():
            self.assertGreaterEqual(len(intent.examples), 3, intent.intent_name)
            self.assertTrue(intent.canonical_command.startswith("/"))
        self.assertIsNone(resolve_natural_command("/internet on"))
        self.assertIsNone(resolve_natural_command("/spotify-status"))


if __name__ == "__main__":
    unittest.main()
