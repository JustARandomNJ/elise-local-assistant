from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from conversational_intent import ConversationIntent, detect_conversational_intent


MAX_RECENT_TURNS = 8
MAX_RECENT_CHARACTERS = 8_000
MAX_TOPIC_SEGMENTS = 4

TOPIC_SHIFT_RE = re.compile(
    r"^(?:separate question|different question|different topic|unrelated question|"
    r"anyway|moving on|on another note|something else|switching topics|new topic)\b[,:;\s-]*",
    re.IGNORECASE,
)
CORRECTION_RE = re.compile(
    r"^(?:no[,:]?\s*)?(?:sorry[,:]?\s*)?(?:i meant|actually|correction:|"
    r"i was talking about)\b[,:;\s-]*(.*)$",
    re.IGNORECASE,
)


@dataclass
class ResponseConstraints:
    choice_count: int | None = None
    sentence_count: int | None = None
    concise: bool = False
    no_explanation: bool = False
    answer_only: bool = False
    yes_no: bool = False

    @property
    def active(self) -> bool:
        return any((self.choice_count, self.sentence_count, self.concise,
                    self.no_explanation, self.answer_only, self.yes_no))

    def instruction(self) -> str:
        rules: list[str] = []
        if self.choice_count is not None:
            rules.append(f"Return exactly {self.choice_count} choice{'s' if self.choice_count != 1 else ''}.")
        if self.sentence_count is not None:
            rules.append(f"Use exactly {self.sentence_count} sentence{'s' if self.sentence_count != 1 else ''}.")
        if self.concise:
            rules.append("Keep it brief.")
        if self.no_explanation:
            rules.append("Give no explanation or rationale.")
        if self.answer_only:
            rules.append("Give only the answer, with no preamble or follow-up question.")
        if self.yes_no:
            rules.append("Answer yes or no only.")
        return " ".join(rules) or "No immediate response constraint."


@dataclass
class TopicSegment:
    topic: str
    turns: list[dict[str, str]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    constraints: dict[str, str] = field(default_factory=dict)
    invalidated_entities: list[str] = field(default_factory=list)
    corrected: bool = False


@dataclass
class ConversationSessionState:
    """Bounded, process-local context split into ephemeral topic segments."""

    recent_turns: list[dict[str, str]] = field(default_factory=list)
    topic_segments: list[TopicSegment] = field(default_factory=list)
    active_mode: str | None = None
    previous_assistant_response: str | None = None

    @property
    def active_segment(self) -> TopicSegment | None:
        return self.topic_segments[-1] if self.topic_segments else None

    @property
    def current_topic(self) -> str | None:
        return self.active_segment.topic if self.active_segment else None

    @property
    def active_options(self) -> list[str]:
        return self.active_segment.options if self.active_segment else []

    @property
    def active_constraints(self) -> dict[str, str]:
        return self.active_segment.constraints if self.active_segment else {}

    @property
    def salient_entities(self) -> list[str]:
        return self.active_segment.entities if self.active_segment else []

    @classmethod
    def from_history(cls, history: list[dict[str, Any]]) -> "ConversationSessionState":
        bounded: list[dict[str, str]] = []
        characters = 0
        for item in reversed(history[-MAX_RECENT_TURNS * 2:]):
            role, content = str(item.get("role", "")), item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or item.get("tool_calls"):
                continue
            remaining = MAX_RECENT_CHARACTERS - characters
            if remaining <= 0:
                break
            bounded.append({"role": role, "content": content[:remaining]})
            characters += min(len(content), remaining)
        bounded.reverse()

        state = cls(recent_turns=bounded)
        for turn in bounded:
            if turn["role"] == "assistant":
                state.previous_assistant_response = turn["content"]
                if state.active_segment:
                    state.active_segment.turns.append(turn)
                    options = _extract_assistant_options(turn["content"])
                    if options:
                        state.active_segment.options = options
                continue
            state._observe_user_turn(turn["content"], turn)
        return state

    def _new_segment(self, topic: str) -> TopicSegment:
        segment = TopicSegment(topic=topic.strip()[:300] or "new topic")
        self.topic_segments.append(segment)
        self.topic_segments = self.topic_segments[-MAX_TOPIC_SEGMENTS:]
        return segment

    def _observe_user_turn(self, message: str, turn: dict[str, str] | None = None) -> None:
        text = " ".join(message.casefold().split())
        intent = detect_conversational_intent(message)
        shifted = _starts_new_topic(message)
        clean_topic = TOPIC_SHIFT_RE.sub("", message).strip()
        if shifted or self.active_segment is None:
            segment = self._new_segment(clean_topic or message)
            if shifted and intent.mode in {"ordinary_chat", "clarification"}:
                self.active_mode = intent.mode
        else:
            segment = self.active_segment
        if turn:
            segment.turns.append(turn)

        if intent.mode not in {"ordinary_chat", "clarification"}:
            self.active_mode = intent.mode
        if _is_personal_reflection(message) and intent.mode in {"ordinary_chat", "clarification"}:
            self.active_mode = "reflective_conversation"

        correction = _correction_value(message)
        if correction:
            segment.invalidated_entities.extend(entity for entity in segment.entities if entity not in segment.invalidated_entities)
            segment.entities = _extract_entities(correction) or [correction.strip()[:120]]
            segment.topic = correction.strip()[:300]
            segment.corrected = True

        time_value, day_value = _extract_time_constraint(message), _extract_day_constraint(message)
        if time_value: segment.constraints["available_time"] = time_value
        if day_value: segment.constraints["day"] = day_value

        options = _extract_options(message)
        if options:
            segment.options = options
            segment.entities = list(options)
            segment.topic = f"decision between {options[0]} and {options[1]}"
            self.active_mode = "advice"
        elif not correction:
            entities = _extract_entities(message)
            for entity in entities:
                if entity not in segment.entities:
                    segment.entities.append(entity)

    def response_constraints(self, message: str) -> ResponseConstraints:
        return detect_response_constraints(message)

    def resolve_intent(self, message: str) -> ConversationIntent:
        current = detect_conversational_intent(message)
        text = " ".join(message.casefold().split())
        if re.search(r"\b(what should i do|give me a compromise|which would you|which one|choose for me)\b", text):
            return _intent_with_mode(current, "advice")
        if self.active_mode == "vent_listen" and current.mode in {"ordinary_chat", "clarification"}:
            return _intent_with_mode(current, "vent_listen", restricted=True)
        if self.active_mode in {"challenge", "reflective_conversation"} and current.mode in {"ordinary_chat", "clarification"}:
            return _intent_with_mode(current, self.active_mode)
        if _is_follow_up(message) and self.current_topic and current.mode == "clarification":
            return _intent_with_mode(current, self.active_mode or "ordinary_chat")
        return current

    def grounding_query(self, message: str) -> str:
        entities = " ".join(self.salient_entities[-2:])
        return f"{entities} {message}".strip() if entities else message

    def active_recent_turns(self) -> list[dict[str, str]]:
        """Only expose the active segment unless the user explicitly returns to an old one."""
        return list(self.active_segment.turns) if self.active_segment else []

    def prompt_summary(self, current_message: str) -> str:
        segment = self.active_segment
        follow_up = _is_follow_up(current_message)
        lines = [
            "Priority: latest explicit instruction/correction, active topic, active-topic turns, mode, memory, inactive topics.",
            f"Active topic: {self.current_topic or 'not established'}",
            f"Inactive topic count: {max(0, len(self.topic_segments) - 1)}; do not use them unless explicitly referenced.",
            f"Active conversational mode: {self.active_mode or 'not established'}",
            "Active entities: " + (", ".join(self.salient_entities) if self.salient_entities else "none established"),
            "Active options: " + (" versus ".join(self.active_options) if self.active_options else "none established"),
            "Active constraints: " + (", ".join(f"{k}={v}" for k, v in self.active_constraints.items()) if self.active_constraints else "none established"),
            "IMMEDIATE RESPONSE CONSTRAINTS: " + self.response_constraints(current_message).instruction(),
            f"Current message appears to be a follow-up: {'yes' if follow_up else 'no'}",
        ]
        if segment and segment.corrected:
            lines.append("The active entity is a correction. Discard facts based on invalidated interpretations and verify uncertain factual claims.")
        if follow_up and self.previous_assistant_response:
            lines.append("A short reference such as 'why' refers to the preceding active-topic response; resolve it inside the active topic, not an older topic.")
        if self.active_mode == "reflective_conversation":
            lines.append("Reflect on and explore what the user shared; do not turn it into advice or an action plan unless asked.")
        return "\n".join(lines)

    def diagnostic(self, current_message: str, freshness_required: bool) -> dict[str, object]:
        return {"session_topic_retained": bool(self.current_topic), "recent_turns": len(self.recent_turns),
                "referent_resolved": "active_decision" if _is_follow_up(current_message) and self.active_options else None,
                "freshness_required": freshness_required}


def detect_response_constraints(message: str) -> ResponseConstraints:
    text = " ".join(message.casefold().split())
    one_choice = bool(re.search(r"\b(pick one|choose one|just choose|choose for me)\b", text))
    exact_two = bool(re.search(r"\b(exactly two|give me two)\b", text))
    return ResponseConstraints(
        choice_count=1 if one_choice else (2 if exact_two else None),
        sentence_count=1 if re.search(r"\b(one sentence|single sentence)\b", text) else None,
        concise=bool(re.search(r"\b(keep it brief|keep it concise|briefly)\b", text)),
        no_explanation=bool(re.search(r"\b(don't explain|do not explain|no explanation)\b", text)),
        answer_only=bool(re.search(r"\b(give me the answer only|answer only)\b", text)),
        yes_no=bool(re.search(r"\b(yes or no)\b", text)),
    )


def _intent_with_mode(intent: ConversationIntent, mode: str, restricted: bool = False) -> ConversationIntent:
    if restricted: return ConversationIntent(mode, False, False, False, 0, intent.concise, intent.blunt)
    memory_limit = 2 if mode in {"advice", "challenge", "critique"} else intent.memory_limit
    return ConversationIntent(mode, intent.tools_allowed, intent.web_allowed, intent.memories_allowed, memory_limit, intent.concise, intent.blunt)


def _extract_options(message: str) -> list[str]:
    for pattern in (r"whether to (.+?) or (.+?)(?:[?.]|$)", r"(?:choosing|deciding between|choose between) (.+?) or (.+?)(?:[?.]|$)"):
        match = re.search(pattern, message, re.IGNORECASE)
        if match: return [match.group(1).strip(), match.group(2).strip()]
    return []


def _extract_assistant_options(message: str) -> list[str]:
    numbered = re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+([^\n]+)", message)
    if len(numbered) >= 2: return [re.split(r"[:.!?]", item, maxsplit=1)[0].strip() for item in numbered[:8]]
    return []


def _extract_entities(message: str) -> list[str]:
    # Conservative title/name candidates; context is for resolution, never proof.
    candidates = re.findall(r"\b(?:Spider-Man|Spiderman|Brand New Day|Peter Parker|Python|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", message)
    return list(dict.fromkeys(candidate.strip() for candidate in candidates))[-6:]


def _correction_value(message: str) -> str | None:
    match = CORRECTION_RE.match(message.strip())
    if match and match.group(1).strip(): return match.group(1).strip()
    match = re.match(r"^not\s+(.+?),\s*(.+)$", message.strip(), re.IGNORECASE)
    if match:
        return match.group(2).strip()
    # Natural repair after an ambiguity is often stated as "<full name>, sorry".
    suffix = re.match(r"^(.+?)[,;]?\s+sorry[.!]?$", message.strip(), re.IGNORECASE)
    return suffix.group(1).strip() if suffix else None


def _extract_time_constraint(message: str) -> str | None:
    match = re.search(r"\b(?:have|had|with|make that)\s+(one|two|three|four|\d+)\s+(?:free\s*)?hours?\b", message, re.IGNORECASE)
    return f"{match.group(1).casefold()} hours" if match else None


def _extract_day_constraint(message: str) -> str | None:
    match = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", message, re.IGNORECASE)
    return match.group(1).title() if match else None


def _is_follow_up(message: str) -> bool:
    text = " ".join(message.casefold().split())
    return bool(len(text.split()) <= 8 or re.match(r"^(which|why|what if|what about|do the other|okay,? give me|give me a compromise|explain that|that(?:'s| is)|actually|use an example|now explain|yeah)\b", text))


def _starts_new_topic(message: str) -> bool:
    return bool(TOPIC_SHIFT_RE.match(message.strip()))


def _is_personal_reflection(message: str) -> bool:
    text = " ".join(message.casefold().split())
    advice = re.search(r"\b(what should i do|advice|help me fix|how should i)\b", text)
    sharing = re.search(r"\b(i relate to|i identify with|kinship|one of the reasons|that's why i|that is why i)\b", text)
    return bool(sharing and not advice)
