from __future__ import annotations

from dataclasses import dataclass
import re

from text_normalization import normalize_apostrophes


@dataclass(frozen=True)
class ConversationIntent:
    mode: str
    tools_allowed: bool
    web_allowed: bool
    memories_allowed: bool
    memory_limit: int
    concise: bool = False
    blunt: bool = False


def _has(text: str, *phrases: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) for phrase in phrases)


def detect_conversational_intent(message: str) -> ConversationIntent:
    """Classify explicit conversational intent before retrieval and tool routing."""
    text = " ".join(normalize_apostrophes(message).casefold().split())
    no_tools = _has(text, "don't use tools", "do not use tools", "no tools")
    no_web = no_tools or _has(text, "don't use the web", "do not use the web", "no web")
    concise = _has(text, "keep it concise", "be concise", "briefly", "keep it brief", "one sentence", "don't explain", "do not explain", "answer only", "give me the answer only")
    blunt = _has(text, "be blunt", "be honest", "don't sugarcoat it", "do not sugarcoat it")

    if _has(text, "just want to vent", "want to vent", "just listen", "don't give me advice", "do not give me advice"):
        return ConversationIntent("vent_listen", False, False, False, 0, True, blunt)
    emotional_chat = _has(text, "i feel", "i'm upset", "i am upset", "rough day", "having a hard day")
    if _has(text, "challenge my thinking", "challenge me", "push back", "argue against"):
        mode = "challenge"
    elif _has(text, "i want advice", "give me advice", "what should i do", "advise me", "choose for me"):
        mode = "advice"
    elif _has(text, "brainstorm", "give me ideas", "help me think of"):
        mode = "brainstorming"
    elif _has(text, "critique", "review this", "what's wrong with", "what is wrong with"):
        mode = "critique"
    elif _has(text, "research", "look up", "search the web", "search online"):
        mode = "research"
    elif _has(text, "create", "write", "save", "open", "play", "run", "change", "update"):
        mode = "task_execution"
    elif _has(text, "explain", "why does", "how does", "what makes", "what is", "what are"):
        mode = "explanation"
    elif message.rstrip().endswith("?") and len(text.split()) <= 4:
        mode = "clarification"
    else:
        mode = "ordinary_chat"

    if emotional_chat:
        return ConversationIntent(mode, False, False, False, 0, concise, blunt)
    memory_limit = 2 if mode in {"advice", "challenge", "critique"} else 1
    return ConversationIntent(mode, not no_tools, not no_web and not no_tools, True, memory_limit, concise, blunt)


def intent_instruction(intent: ConversationIntent) -> str:
    directives = [f"Conversational mode: {intent.mode}."]
    if intent.mode == "challenge":
        directives.append("Briefly acknowledge the request, then give one genuine counterpoint to a stated assumption. Do not substitute reassurance or empty agreement. Ask for a topic only if none was supplied.")
    elif intent.mode == "advice":
        directives.append("The user explicitly permits advice.")
    elif intent.mode in {"explanation", "ordinary_chat"}:
        directives.append("Answer the general question first. Use personal context only when it materially improves the answer; do not redirect it into commentary about the assistant's own project.")
    if intent.blunt:
        directives.append("Be candid and direct without being cruel.")
    if intent.concise:
        directives.append("Keep the response concise.")
    directives.append("Do not diagnose or label the user's mental health from a vague statement.")
    directives.append("Match requested durations exactly; never describe 30 minutes as an hour.")
    return " ".join(directives)


def listening_response() -> str:
    return "Okay. I'm listening. What happened?"


def diagnostic(intent: ConversationIntent, memory_scores: list[float]) -> dict[str, object]:
    return {
        "conversational_mode": intent.mode,
        "tools_allowed": intent.tools_allowed,
        "memories_retrieved": len(memory_scores),
        "memory_relevance_scores": [round(score, 3) for score in memory_scores],
    }
