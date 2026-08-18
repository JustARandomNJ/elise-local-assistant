from __future__ import annotations

import unittest

import app
from conversation_session import ConversationSessionState, MAX_RECENT_CHARACTERS, MAX_RECENT_TURNS, MAX_TOPIC_SEGMENTS, detect_response_constraints


class MemoryStub:
    def search(self, *_args, **_kwargs): return []
    def build_prompt(self, _results): return "No relevant persistent memories were retrieved."


class DocumentStub:
    def search(self, *_args, **_kwargs): return []
    def build_prompt_context(self, _results): return "No relevant local document context."


class ToolStub:
    def ollama_tool_schemas(self): return []


class FakeResponse:
    def __init__(self, content):
        self.message = type("Message", (), {"content": content, "tool_calls": []})()


class ConversationSessionTests(unittest.TestCase):
    def test_direct_response_constraints(self):
        for phrase in ("pick one", "choose for me"):
            self.assertEqual(1, detect_response_constraints(phrase).choice_count)
        self.assertEqual(2, detect_response_constraints("exactly two").choice_count)
        self.assertEqual(1, detect_response_constraints("one sentence").sentence_count)
        self.assertTrue(detect_response_constraints("don't explain").no_explanation)
        self.assertEqual("Walk outside.", app.enforce_response_constraints(
            "1. Walk outside.\n2. Read a book.\n3. Call a friend.", detect_response_constraints("pick one")))

    def test_topic_segments_corrections_entities_and_reflection(self):
        history = [
            {"role": "user", "content": "if I have an hour of freetime, what should I do"},
            {"role": "assistant", "content": "1. Take a walk\n2. Read\n3. Code"},
            {"role": "user", "content": "pick one"},
            {"role": "assistant", "content": "Take a walk."},
            {"role": "user", "content": "separate question, Is it bad I feel a kinship to spiderman"},
            {"role": "assistant", "content": "No. What feels familiar?"},
            {"role": "user", "content": "yeah, multiple things, have you seen brand new day"},
            {"role": "assistant", "content": "I don't literally watch movies; do you mean Spider-Man: Brand New Day?"},
            {"role": "user", "content": "spiderman brand new day, sorry"},
            {"role": "assistant", "content": "Thanks for the correction."},
            {"role": "user", "content": "yeah, and that movie is one of the reasons I relate to him a lot right now"},
        ]
        state = ConversationSessionState.from_history(history)
        self.assertGreaterEqual(len(state.topic_segments), 2)
        self.assertNotIn("hour", state.prompt_summary("why?").casefold())
        self.assertIn("brand new day", " ".join(state.salient_entities).casefold())
        self.assertTrue(state.active_segment.corrected)
        self.assertEqual("reflective_conversation", state.resolve_intent("that's why I relate to him").mode)
        self.assertIn("do not turn it into advice", state.prompt_summary("that's why I relate to him"))

    def test_topic_segments_are_bounded_and_stale_constraints_do_not_leak(self):
        history = [{"role": "user", "content": "I have one free hour."}]
        for index in range(MAX_TOPIC_SEGMENTS + 3):
            history.extend(({"role": "assistant", "content": "Okay."},
                            {"role": "user", "content": f"different topic, topic {index}"}))
        state = ConversationSessionState.from_history(history)
        self.assertLessEqual(len(state.topic_segments), MAX_TOPIC_SEGMENTS)
        self.assertNotIn("available_time", state.active_constraints)
        self.assertFalse(any("free hour" in turn["content"] for turn in state.active_recent_turns()))

    def test_named_entity_grounding_and_hallucination_restraint(self):
        state = ConversationSessionState.from_history([
            {"role": "user", "content": "I relate to Spider-Man."},
            {"role": "assistant", "content": "What resonates?"},
            {"role": "user", "content": "Have you heard of Brand New Day?"},
        ])
        grounded = state.grounding_query("Who stars in Brand New Day?")
        self.assertIn("Spider-Man", grounded)
        self.assertTrue(app.requires_forced_freshness_search(grounded))
        self.assertTrue(app.requires_forced_freshness_search("Spider-Man have you seen Brand New Day"))
        for ordinary in ("I feel tired tonight.", "Should I code tonight?", "I relate to Spider-Man."):
            self.assertFalse(app.requires_forced_freshness_search(ordinary), ordinary)
    def test_freshness_requires_changing_domain(self):
        ordinary = (
            "I'm deciding whether to code tonight or relax.", "I'm tired today.",
            "I had a productive day.", "What should I do tonight?", "I feel behind lately.",
            "Should I study tomorrow?", "I've been coding a lot this week.",
            "I recently started learning Rust.",
            "I'm available tonight if you want to code.",
        )
        current = (
            "What's the weather tonight?", "What happened in AI news today?",
            "Who is currently the CEO of X?", "What games are happening tonight?",
            "What is the latest Python release?", "Is GitHub down right now?",
            "What's happened in AI lately?",
        )
        for query in ordinary: self.assertFalse(app.requires_forced_freshness_search(query), query)
        for query in current: self.assertTrue(app.requires_forced_freshness_search(query), query)

    def test_exact_failed_five_turn_sequence(self):
        turns = (
            ("I'm deciding whether to spend tonight coding or just relax.", "I'd relax tonight."),
            ("Which would you choose?", "I would choose relaxing."),
            ("Why?", "Because rest will make the next coding session better."),
            ("What if I have two free hours instead of one?", "That makes a split practical."),
            ("Okay, give me a compromise.", "Code for one hour, then relax for one hour."),
        )
        history, calls, forced_calls = [], [], []
        original_chat, original_forced = app.ollama.chat, app.execute_forced_freshness_search
        try:
            def fake_chat(**kwargs):
                calls.append(kwargs)
                return FakeResponse(turns[len(calls) - 1][1])
            app.ollama.chat = fake_chat
            app.execute_forced_freshness_search = lambda **kwargs: forced_calls.append(kwargs)
            responses = []
            for user, _expected in turns:
                history.append({"role": "user", "content": user})
                response, _docs, _memories, trace = app.request_model_response(
                    history, MemoryStub(), DocumentStub(), ToolStub(), None, user
                )
                self.assertIsNone(trace)
                responses.append(response)
                history.append({"role": "assistant", "content": response})
        finally:
            app.ollama.chat, app.execute_forced_freshness_search = original_chat, original_forced

        self.assertFalse(forced_calls)
        self.assertEqual(len(calls), 5)
        prompts = [call["messages"][0]["content"] for call in calls]
        self.assertIn("coding versus just relax", prompts[1])
        self.assertIn("A short reference such as 'why'", prompts[2])
        self.assertIn("available_time=two hours", prompts[4])
        self.assertNotIn("clarify", responses[1].casefold())
        self.assertNotIn("clarify", responses[2].casefold())
        self.assertIn("one hour", responses[4].casefold())

    def test_corrections_modes_subject_change_and_ambiguity(self):
        corrected = ConversationSessionState.from_history([
            {"role": "user", "content": "I have an interview Friday."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": "Actually, Thursday."},
        ])
        self.assertEqual(corrected.active_constraints["day"], "Thursday")
        self.assertNotIn("Friday", corrected.prompt_summary("How should I prepare the night before?"))

        vent = ConversationSessionState.from_history([
            {"role": "user", "content": "I just want to vent."},
            {"role": "assistant", "content": "I'm listening."},
        ])
        self.assertEqual(vent.resolve_intent("Today was awful.").mode, "vent_listen")
        self.assertEqual(vent.resolve_intent("Okay, what should I do?").mode, "advice")

        subject = ConversationSessionState.from_history([
            {"role": "user", "content": "I'm deciding whether to code tonight or relax."},
            {"role": "assistant", "content": "Relax."},
            {"role": "user", "content": "Anyway, explain black holes."},
            {"role": "assistant", "content": "Gravity curves spacetime."},
        ])
        self.assertIn("black holes", subject.current_topic.casefold())
        self.assertNotIn("decision between", subject.current_topic.casefold())

        ambiguous = ConversationSessionState.from_history([
            {"role": "user", "content": "I was comparing three different laptops."}
        ])
        self.assertFalse(ambiguous.active_options)

        choice = ConversationSessionState.from_history([
            {"role": "user", "content": "I'm choosing Python or Rust."},
            {"role": "assistant", "content": "I'd use Python here."},
        ])
        self.assertEqual(choice.active_options, ["Python", "Rust"])
        self.assertIn("Python versus Rust", choice.prompt_summary("Why?"))

        preference_history = [
            {"role": "user", "content": "Explain mutexes."},
            {"role": "assistant", "content": "A mutex controls exclusive access."},
            {"role": "user", "content": "Too textbook-y."},
            {"role": "assistant", "content": "Fair point."},
            {"role": "user", "content": "Use an example instead."},
            {"role": "assistant", "content": "Think of one bathroom key."},
            {"role": "user", "content": "Now explain semaphores the same way."},
        ]
        preference = ConversationSessionState.from_history(preference_history)
        self.assertTrue(any("bathroom key" in turn["content"] for turn in preference.recent_turns))
        self.assertTrue(any("Too textbook-y" in turn["content"] for turn in preference.recent_turns))

    def test_buffer_and_diagnostics_are_bounded_and_sanitized(self):
        history = []
        for index in range(30):
            history += [{"role": "user", "content": f"turn {index} " + "x" * 1000},
                        {"role": "assistant", "content": "reply " + "y" * 1000}]
        state = ConversationSessionState.from_history(history)
        self.assertLessEqual(len(state.recent_turns), MAX_RECENT_TURNS * 2)
        self.assertLessEqual(sum(len(t["content"]) for t in state.recent_turns), MAX_RECENT_CHARACTERS)
        diagnostic = state.diagnostic("Why?", False)
        self.assertEqual(set(diagnostic), {"session_topic_retained", "recent_turns", "referent_resolved", "freshness_required"})


if __name__ == "__main__": unittest.main()
