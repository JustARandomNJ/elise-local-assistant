from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Mapping

from text_normalization import normalize_apostrophes


@dataclass(frozen=True)
class ParsedCommand:
    """A host-validated command, never a free-form tool invocation."""

    intent: str
    arguments: Mapping[str, object]
    confidence: float
    corrections: tuple[str, ...] = ()
    clarification: str | None = None


IntentProposer = Callable[[str], object]
_MIN_DETERMINISTIC_CONFIDENCE = 0.70
_MIN_PROPOSAL_CONFIDENCE = 0.85
_CORRECTIONS = {
    "plaay": "play",
    "pul": "pull",
    "brign": "bring",
    "latesst": "latest",
    # Retained bounded corrections from the original command surface.
    "latset": "latest",
    "vidoe": "video",
    "teh": "the",
    "ythe": "the",
}
_ACTION = re.compile(
    r"^(?:pl(?:ay|aay)(?: me)?|playing|open(?:ing)?|show(?: me|ing)?|pu(?:ll|l)(?:ing)?(?: up)?|br(?:ing|ign)(?:ing)? up|put(?:ting)? on|start(?:ing)?|watch(?:ing)?|load(?:ing)?|get(?:ting)?|let me watch|i want to watch)\s+",
    re.IGNORECASE,
)
_REQUEST_PREFIX = re.compile(
    r"^(?:(?:please|go ahead and)\s+)*(?:(?:can|could) you\s+|would you(?: mind)?\s+)?(?:(?:please|go ahead and)\s+)*",
    re.IGNORECASE,
)
_ARTICLE = r"(?:the|teh|ythe)"
_RECENCY = r"(?:newest\s+available|most\s+recent|latest|newest|recent|latesst|latset)"
_MEDIA = r"(?:video|upload|youtube\s+video|vidoe)"
_TAIL = re.compile(r"\s+(?:for me|please|right now|thanks|thank you)\s*$", re.IGNORECASE)


def _possessive_creator(value: str) -> str:
    value = value.strip()
    if value.casefold().endswith(("'s", "’s")):
        return value[:-2].rstrip()
    return value


def _has_forbidden_context(text: str) -> bool:
    lowered = normalize_apostrophes(text).casefold()
    if any(ord(character) < 32 and character not in "\t\r\n" for character in text):
        return True
    if re.search(r"\b(?:do not|don't|don’t|never|stop|without)\b", lowered):
        return True
    if re.match(r"^(?:what|which|who|when|where|why|how|did|do|should|would i|could i|tell me|search)\b", lowered):
        return True
    if re.search(r"\b(?:might|maybe|perhaps|if|later)\b", lowered):
        return True
    if re.match(r"^(?:i (?:liked?|saw)|.+\b(?:was|is|seems?)\b)", lowered):
        return True
    if re.search(r"\b(?:ignore (?:your )?permissions|reveal\b|delete\b|remove\b|use the api key)\b", lowered):
        return True
    if re.search(r"\b(?:and|then)\s+(?:delete|remove|reveal|open|search|show|tell|send|run|write|email|message|call|download|edit|move|copy)\b", lowered):
        return True
    if re.search(r"https?://|www\.", lowered):
        return True
    if re.search(r"(?:^|\s)(?:\.\.?[\\/]|[a-z]:[\\/])|\\\.\.[\\/]", text, re.IGNORECASE):
        return True
    if re.search(r"(?:\|\||&&|[|;`]|\$\(|>|<)", text):
        return True
    return False


def _safe_creator(value: str) -> bool:
    lowered = value.casefold().strip()
    if not lowered or lowered in {"a", "something", "anything", "it", "their", "them", "someone", "somebody", "the creator", "that creator", "my favorite"}:
        return False
    if re.search(r"[\x00-\x1f\x7f]", value) or re.search(r"https?://|www\.", lowered):
        return False
    if re.search(r"(?:^|[\\/])\.\.(?:[\\/]|$)|^\.?[\\/]|^[a-z]:[\\/]", value, re.IGNORECASE):
        return False
    if re.search(r"(?:\|\||&&|[|;`] |[|;`]|\$\(|>|<)", value):
        return False
    if re.search(r"\b(?:api\s+key|password|secret|token)\b", lowered):
        return False
    if re.search(r"\b(?:and|then)\s+(?:delete|remove|reveal|open|search|run|write)\b", lowered):
        return False
    return True


def _correct_scaffold(value: str, corrections: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        corrected = _CORRECTIONS.get(original.casefold())
        if corrected is None:
            return original
        corrections.append(f"{original} -> {corrected}")
        return corrected

    return re.sub(r"\b(?:plaay|pul|brign|latesst|latset|vidoe|teh|ythe)\b", replace, value, flags=re.IGNORECASE)


def _restore_untrusted_terms(fragment: str, corrected_text: str, original_text: str) -> str:
    """Map an extracted corrected span back to original tokens; targets are opaque."""

    corrected_words, original_words, fragment_words = corrected_text.split(), original_text.split(), fragment.split()
    if len(corrected_words) != len(original_words) or not fragment_words:
        return fragment
    wanted = [word.casefold() for word in fragment_words]
    for start in range(len(corrected_words) - len(wanted) + 1):
        if [word.casefold() for word in corrected_words[start:start + len(wanted)]] == wanted:
            return " ".join(original_words[start:start + len(wanted)])
    return fragment


def _extract_deterministic(text: str) -> tuple[str | None, tuple[str, ...]]:
    core = _REQUEST_PREFIX.sub("", text).strip()
    action_match = _ACTION.match(core)
    if action_match:
        content = core[action_match.end():].strip()
    elif re.match(rf"^(?:{_ARTICLE}\s+)?{_RECENCY}\s+{_MEDIA}\s+.+\s+posted$", core, re.IGNORECASE):
        content = core
    else:
        return None, ()

    content = re.sub(r"\s+(?:playing|going)\s*$", "", content, flags=re.IGNORECASE)
    while _TAIL.search(content):
        content = _TAIL.sub("", content).strip()
    content = re.sub(r"\s+up\s*$", "", content, flags=re.IGNORECASE)
    patterns = (
        rf"^(?:{_ARTICLE}\s+)?{_RECENCY}\s+(?P<creator>.+?)\s+{_MEDIA}$",
        rf"^(?P<creator>.+?)(?:'s|’s)\s+{_RECENCY}\s+{_MEDIA}$",
        rf"^(?P<creator>.+?)\s+{_RECENCY}\s+{_MEDIA}$",
        rf"^(?:{_ARTICLE}\s+)?{_RECENCY}\s+{_MEDIA}\s+(?:from|by)\s+(?P<creator>.+)$",
        rf"^(?:{_ARTICLE}\s+)?{_RECENCY}\s+{_MEDIA}\s+(?P<creator>.+?)\s+posted$",
    )
    for pattern in patterns:
        match = re.match(pattern, content, flags=re.IGNORECASE)
        if match:
            creator = _possessive_creator(match.group("creator"))
            scaffold = (
                core[: core.find(content)]
                + content[: match.start("creator")]
                + content[match.end("creator") :]
            )
            corrections: list[str] = []
            _correct_scaffold(scaffold, corrections)
            return creator, tuple(corrections)
    return None, ()


def parse_natural_command(user_input: str) -> ParsedCommand | None:
    """Parse the conservative deterministic natural-language command grammar."""

    text = user_input.strip()
    if not text or text.startswith("/"):
        return None
    text = normalize_apostrophes(text)
    control = _extract_spotify_control(text)
    if control is not None:
        return control
    if _has_forbidden_context(text):
        return None
    question = text.endswith("?")
    if question and not re.match(r"^(?:please\s+)?(?:can|could|would) you\b", text, re.IGNORECASE):
        return None
    text = text.rstrip(".!? ")

    music_command = _extract_music_query(text)
    if music_command is not None:
        return music_command

    vague_core = _REQUEST_PREFIX.sub("", _correct_scaffold(text, [])).strip()
    vague_core = _ACTION.sub("", vague_core).strip()
    if re.fullmatch(rf"(?:the\s+)?{_RECENCY}\s+{_MEDIA}", vague_core, re.IGNORECASE) or re.fullmatch(rf"their\s+{_RECENCY}\s+{_MEDIA}", vague_core, re.IGNORECASE) or re.fullmatch(r"(?:a|some)\s+video", vague_core, re.IGNORECASE):
        return ParsedCommand("media.play_latest", {}, 1.0, clarification="Elise: Which creator should I use?")

    creator, corrections = _extract_deterministic(text)
    if creator is not None and _safe_creator(creator):
        confidence = round(0.97 - 0.08 * len(corrections), 2)
        if confidence >= _MIN_DETERMINISTIC_CONFIDENCE:
            return ParsedCommand("media.play_latest", {"creator": creator}, confidence, corrections)

    query_command = _extract_video_query(text)
    return query_command


def _extract_spotify_control(text: str) -> ParsedCommand | None:
    normalized = " ".join(text.casefold().rstrip(".!? ").split())
    if re.search(r"\b(?:don't|do not|never|stop|without)\b", normalized):
        return None
    actions = {
        "pause": r"^(?:pause (?:the )?(?:music|spotify)|pause spotify)$",
        "resume": r"^(?:resume (?:the |my )?(?:music|spotify)|resume spotify)$",
        "next": r"^(?:next (?:song|track)|skip (?:this |the )?(?:song|track))$",
        "previous": r"^(?:previous (?:song|track)|go back(?: (?:a |one )?(?:song|track))?)$",
        "volume_up": r"^(?:turn (?:the music |the )?volume up|turn the music up)$",
        "volume_down": r"^(?:turn (?:the music |the )?volume down|turn it down)$",
        "current": r"^(?:what's|what is) playing$|^(?:what's|what is|what) (?:song|track) (?:is this|is playing)$|^(?:spotify|music) status$",
    }
    for action, pattern in actions.items():
        if re.fullmatch(pattern, normalized, re.IGNORECASE):
            return ParsedCommand("media.spotify_control", {"action": action}, 1.0)
    return None


def _extract_music_query(text: str) -> ParsedCommand | None:
    """Parse only strong music/provider cues while preserving opaque names."""

    if re.search(r"\b(?:video|upload|youtube)\b", text, re.IGNORECASE):
        return None
    # Preserve the existing creator/video grammar's explicit generic-creator form.
    if re.search(r"\bby\s+(?:some|a|the)\s+creator$", text, re.IGNORECASE):
        return None
    core = _REQUEST_PREFIX.sub("", text).strip()
    # Provider names are routing syntax, not part of the title or artist.  Strip
    # the suffix once before matching so an optional regex group cannot absorb it.
    provider_suffix = re.search(r"\s+on\s+spotify\s*$", core, flags=re.IGNORECASE) is not None
    core = re.sub(r"\s+on\s+spotify\s*$", "", core, flags=re.IGNORECASE).strip()
    list_only = False
    patterns = (
        (r"^(?:show me spotify results for|find songs called)\s+(?P<title>.+?)(?:\s+by\s+(?P<artist>.+?))?$", True),
        (r"^play\s+(?:some\s+)?music\s+by\s+(?P<artist>.+?)$", False),
        (r"^play\s+(?:the\s+)?(?:song|track)\s+(?P<title>.+?)(?:\s+by\s+(?P<artist>.+?))?$", False),
        (r"^play\s+track\s+(?P<title>.+?)(?:\s+by\s+(?P<artist>.+?))?$", False),
        (r"^play\s+(?P<title>.+?)\s+by\s+(?P<artist>.+?)$", False),
        (r"^put on\s+(?P<title>.+?)$", False),
        (r"^play some\s+(?P<artist>.+?)$", False),
    )
    if provider_suffix:
        patterns += ((r"^play\s+(?P<title>.+?)$", False),)
    for pattern, forced_list in patterns:
        match = re.fullmatch(pattern, core, re.IGNORECASE)
        if not match:
            continue
        values = match.groupdict()
        title = (values.get("title") or "").strip()
        artist = (values.get("artist") or "").strip() or None
        if (title and not _safe_creator(title)) or (artist and not _safe_creator(artist)) or (not title and not artist):
            return None
        list_only = forced_list
        return ParsedCommand("media.play_music", {"query": title, "artist": artist, "album": None, "list_only": list_only}, 1.0)
    return None


def _extract_video_query(text: str) -> ParsedCommand | None:
    """Separate the creator grammar from an opaque, spelling-preserved topic."""

    corrections: list[str] = []
    corrected = _correct_scaffold(text, corrections)
    if len(corrections) >= 4:
        return None
    core = _REQUEST_PREFIX.sub("", corrected).strip()
    list_only = bool(re.match(r"^(?:show me|find me)(?:\s+some)?\s+", core, re.IGNORECASE))
    core = re.sub(r"^(?:show me|find me)(?:\s+some)?\s+", "", core, flags=re.IGNORECASE) if list_only else core
    if not list_only:
        action = _ACTION.match(core)
        if not action:
            return None
        core = core[action.end():].strip()
    core = _TAIL.sub("", core).strip()
    if re.search(r"\b(?:project file|document|news about)\b", core, re.IGNORECASE):
        return None
    core = re.sub(r"^(?:one of\s+)", "", core, flags=re.IGNORECASE)
    leading_article_match = re.match(r"^(a|an)\s+", core, re.IGNORECASE)
    core = re.sub(r"^(?:a|an)\s+", "", core, flags=re.IGNORECASE)
    core = re.sub(r"^the\s+(?=(?:latest|newest)\b)", "", core, flags=re.IGNORECASE)
    core = re.sub(r"\s+(?:video|videos|upload|uploads|vidoe)\s*$", "", core, flags=re.IGNORECASE).strip()
    ordering = "relevance"
    possessive = re.match(r"^(?P<creator>.+?)(?:'s|’s|â€™s)\s+(?P<body>.+)$", core, re.IGNORECASE)
    if possessive:
        creator, body = possessive.group("creator").strip(), possessive.group("body").strip()
    else:
        words = core.split(maxsplit=1)
        if len(words) < 2:
            if not list_only and leading_article_match and leading_article_match.group(1).casefold() == "an" and _safe_creator(core):
                return ParsedCommand("media.play_latest", {"creator": core}, 0.90)
            if list_only or re.search(r"\b(?:video|videos)\b", text, re.IGNORECASE):
                return ParsedCommand("media.play_query", {}, 1.0, clarification="Elise: Which creator should I use?")
            return None
        creator, body = words[0], words[1]
    if re.match(r"^(?:latest|newest)\s+", body, re.IGNORECASE):
        ordering, body = "date", re.sub(r"^(?:latest|newest)\s+", "", body, flags=re.IGNORECASE)
    elif re.match(r"^most viewed\s+", body, re.IGNORECASE):
        ordering, body = "view_count", re.sub(r"^most viewed\s+", "", body, flags=re.IGNORECASE)
    elif not possessive:
        latest_prefix = re.match(r"^(?:latest|newest)\s+(\S+)\s+(.+)$", core, re.IGNORECASE)
        if latest_prefix:
            ordering, creator, body = "date", latest_prefix.group(1), latest_prefix.group(2)
    body = re.sub(r"\s+(?:from|by)\s*$", "", body, flags=re.IGNORECASE).strip()
    creator = _restore_untrusted_terms(creator, corrected, text)
    body = _restore_untrusted_terms(body, corrected, text)
    body = re.sub(r"\s+(?:video|videos|upload|uploads|vidoe)[.!?]?\s*$", "", body, flags=re.IGNORECASE).strip()
    if not _safe_creator(creator) or not body or not _safe_creator(body):
        return None
    return ParsedCommand("media.play_query", {"creator": creator, "query": body, "ordering": ordering, "list_only": list_only}, 0.97)


def _eligible_for_proposal(user_input: str) -> bool:
    text = user_input.strip()
    if not text or text.startswith("/") or _has_forbidden_context(text):
        return False
    if text.endswith("?") and not re.match(r"^(?:please\s+)?(?:can|could|would) you\b", text, re.IGNORECASE):
        return False
    core = _REQUEST_PREFIX.sub("", text)
    if re.match(r"^(?:tell|explain|find|search|what|which|who|when|where|why|how)\b", core, re.IGNORECASE):
        return False
    framed = core != text or _ACTION.match(core) is not None
    media_cue = re.search(r"\b(?:play|watch|video|upload|youtube)\b", text, re.IGNORECASE)
    ambiguous = re.search(
        r"\b(?:their|them|someone|somebody|that creator|the creator|my favorite)\b",
        text,
        re.IGNORECASE,
    )
    return bool(framed and media_cue and not ambiguous)


def validate_intent_proposal(user_input: str, proposal: object) -> ParsedCommand | None:
    """Validate an untrusted local-model syntax proposal against host policy."""

    if not _eligible_for_proposal(user_input):
        return None
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(proposal, dict):
        return None
    intent = proposal.get("intent")
    expected = {"intent", "creator", "confidence"} if intent == "media.play_latest" else {"intent", "creator", "query", "ordering", "list_only", "confidence"}
    if set(proposal) != expected or intent not in {"media.play_latest", "media.play_query"} or not isinstance(proposal.get("creator"), str):
        return None
    if isinstance(proposal["confidence"], bool) or not isinstance(proposal["confidence"], (int, float)):
        return None
    creator = _possessive_creator(proposal["creator"])
    if float(proposal["confidence"]) < _MIN_PROPOSAL_CONFIDENCE or not _safe_creator(creator):
        return None
    if intent == "media.play_query":
        query, ordering, list_only = proposal.get("query"), proposal.get("ordering"), proposal.get("list_only")
        if not isinstance(query, str) or not _safe_creator(query) or ordering not in {"relevance", "date", "view_count"} or not isinstance(list_only, bool):
            return None
        return ParsedCommand(intent, {"creator": creator, "query": query, "ordering": ordering, "list_only": list_only}, float(proposal["confidence"]))
    return ParsedCommand(intent, {"creator": creator}, float(proposal["confidence"]))


def dispatch_natural_command(user_input: str, play_latest: Callable[[str, str], str], propose_intent: IntentProposer | None = None, play_query: Callable[[str, str, str, str, bool], str] | None = None, play_music: Callable[[str, str, str | None, str | None, bool], str] | None = None, spotify_control: Callable[[str, str], str] | None = None) -> str | None:
    """Dispatch only registered typed intents; ``None`` means safe fallthrough."""

    command = parse_natural_command(user_input)
    if command is None and propose_intent is not None and _eligible_for_proposal(user_input):
        try:
            command = validate_intent_proposal(user_input, propose_intent(user_input))
        except Exception:
            command = None
    if command is None:
        return None
    if command.clarification is not None:
        return command.clarification
    if command.intent == "media.play_music":
        if play_music is None:
            return None
        return play_music("natural-language media.play_music", str(command.arguments["query"]), command.arguments.get("artist") if isinstance(command.arguments.get("artist"), str) else None, command.arguments.get("album") if isinstance(command.arguments.get("album"), str) else None, bool(command.arguments["list_only"]))
    if command.intent == "media.spotify_control":
        if spotify_control is None:
            return None
        return spotify_control("natural-language media.spotify_control", str(command.arguments["action"]))
    if command.intent == "media.play_query":
        if play_query is None:
            return None
        return play_query("natural-language media.play_query", str(command.arguments["creator"]), str(command.arguments["query"]), str(command.arguments["ordering"]), bool(command.arguments["list_only"]))
    return play_latest("natural-language media.play_latest", str(command.arguments["creator"]))
