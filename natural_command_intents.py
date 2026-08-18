from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping, Sequence

from text_normalization import normalize_apostrophes


class RiskLevel(str, Enum):
    SAFE = "A"
    STATEFUL = "B"
    SENSITIVE = "C"
    DEVELOPER = "D"


@dataclass(frozen=True)
class CommandIntent:
    canonical_command: str
    intent_name: str
    examples: tuple[str, ...]
    patterns: tuple[str, ...]
    risk_level: RiskLevel
    requires_confirmation: bool = False
    argument: str | None = None


@dataclass(frozen=True)
class PendingSelections:
    music: tuple[str, ...] = ()
    creator: tuple[str, ...] = ()
    video: tuple[str, ...] = ()
    spotify_device: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedNaturalCommand:
    canonical: str
    intent: CommandIntent


def _intent(command: str, name: str, risk: RiskLevel, examples: Sequence[str], patterns: Sequence[str], *, argument: str | None = None, confirmation: bool = False) -> CommandIntent:
    return CommandIntent(command, name, tuple(examples), tuple(patterns), risk, confirmation, argument)


# This is deliberately declarative. Patterns are anchored and arguments are captured
# only by the named ``arg`` group; implementations remain in the canonical handlers.
COMMAND_INTENTS: tuple[CommandIntent, ...] = (
    _intent("/help", "help.show", RiskLevel.SAFE, ("show me what you can do", "show the command help", "how can I control Elise?"), (r"show (?:me )?(?:the )?(?:command )?help", r"(?:what commands (?:can i use|do you support)|show me what you can do)", r"how can i control elise")),
    _intent("/documents", "documents.list", RiskLevel.SAFE, ("show my indexed documents", "what documents do you have?", "list my documents"), (r"(?:show|list) (?:me )?(?:my |the )?(?:indexed |local )?documents", r"what documents do you (?:have|know about)")),
    _intent("/search", "documents.search", RiskLevel.SAFE, ("search my documents for CAN bus", "find CAN bus in my documents", "look through the documents for CAN bus"), (r"search (?:my|the|local) documents for (?P<arg>.+)", r"find (?P<arg>.+) in (?:my|the|local) documents", r"look through (?:my|the|the local) documents for (?P<arg>.+)"), argument="query"),
    _intent("/reindex", "documents.reindex", RiskLevel.STATEFUL, ("reindex my documents", "refresh the document index", "scan my documents again"), (r"reindex (?:my |the )?documents", r"refresh (?:my |the )?document index", r"scan (?:my |the )?documents again")),
    _intent("/list-files", "files.list", RiskLevel.SAFE, ("list files in documents", "show files in the project", "recursively list files in documents notes"), (r"(?:list|show) files in (?:the )?(?P<root>project|documents)(?: under| in)?(?: (?P<path>.+?))?(?P<recursive> recursively)?", r"recursively list files in (?:the )?(?P<root>project|documents)(?: (?P<path>.+))?")),
    _intent("/read-file", "files.read", RiskLevel.SAFE, ("read documents notes.txt", "show the project file README.md", "open documents file plans/today.md"), (r"read (?P<root>project|documents)(?: file)? (?P<path>.+)", r"(?:show|open) (?:the )?(?P<root>project|documents) file (?P<path>.+)")),
    _intent("/preview-profile", "profile.preview", RiskLevel.SAFE, ("preview profile file profile.json", "validate the profile file import.json", "inspect profile JSON profile.json"), (r"preview profile file (?P<arg>.+\.json)", r"validate (?:the )?profile file (?P<arg>.+\.json)", r"inspect profile json (?P<arg>.+\.json)"), argument="path"),
    _intent("/memories", "memory.list", RiskLevel.SAFE, ("what do you remember about me?", "show your memories", "list my stored memories"), (r"what do you remember about me", r"show (?:me )?(?:your|my stored) memories", r"list (?:my )?(?:stored )?memories")),
    _intent("/memories", "memory.list_category", RiskLevel.SAFE, ("show my project memories", "list memories in the preference category", "what goals do you remember?"), (r"(?:show|list) (?:my )?(?P<arg>fact|preference|goal|project|observation) memories", r"list memories in (?:the )?(?P<arg>fact|preference|goal|project|observation) category", r"what (?P<arg>goals|preferences|projects|facts|observations) do you remember"), argument="category"),
    _intent("/search-memories", "memory.search", RiskLevel.SAFE, ("search your memories for embedded systems", "what do you remember about embedded systems?", "look through memory for embedded systems"), (r"search (?:your|my stored) memor(?:y|ies) for (?P<arg>.+)", r"what do you remember about (?P<arg>(?!me$).+)", r"look through (?:your )?memor(?:y|ies) for (?P<arg>.+)"), argument="query"),
    _intent("/memory", "memory.detail", RiskLevel.SAFE, ("show memory 12", "inspect stored memory 12", "show details for memory 12"), (r"show memory (?P<arg>\d+)", r"inspect (?:stored )?memory (?P<arg>\d+)", r"show (?:me )?details for memory (?P<arg>\d+)"), argument="id"),
    _intent("/memory-why", "memory.retrieval_log", RiskLevel.SAFE, ("why did you use my memories?", "show recent memory retrieval reasons", "show the last 5 memory reasons"), (r"why did you use my memories", r"show (?:me )?(?:recent )?memory retrieval reasons", r"show (?:me )?the last (?P<arg>\d+) memory (?:retrieval )?reasons"), argument="limit"),
    _intent("/remember", "memory.remember", RiskLevel.STATEFUL, ("remember as a preference that I like tea", "save as a project memory: Elise", "store this fact: I own an ESP32"), (r"remember as (?:a |an )?(?P<category>fact|preference|goal|project|observation)(?: that|:)? (?P<value>.+)", r"(?:save|store) as (?:a |an )?(?P<category>fact|preference|goal|project|observation) memory(?::| that)? (?P<value>.+)", r"(?:save|store) this (?P<category>fact|preference|goal|project|observation):? (?P<value>.+)")),
    _intent("/forget", "memory.forget", RiskLevel.SENSITIVE, ("forget memory 12", "delete stored memory 12", "remove memory number 12"), (r"forget (?:stored )?memory (?P<arg>\d+)", r"delete (?:stored )?memory (?P<arg>\d+)", r"remove memory (?:number )?(?P<arg>\d+)"), argument="id"),
    _intent("/memory-review", "memory_review.status", RiskLevel.SAFE, ("is memory review enabled?", "show memory review status", "check automatic memory review"), (r"is (?:automatic )?memory review (?:enabled|on)", r"show (?:me )?memory review status", r"check (?:automatic )?memory review"), argument="status"),
    _intent("/memory-review", "memory_review.on", RiskLevel.STATEFUL, ("turn memory review on", "enable automatic memory review", "start reviewing memories"), (r"turn (?:automatic )?memory review on", r"enable (?:automatic )?memory review", r"start reviewing memories"), argument="on"),
    _intent("/memory-review", "memory_review.off", RiskLevel.STATEFUL, ("turn memory review off", "disable automatic memory review", "stop reviewing memories automatically"), (r"turn (?:automatic )?memory review off", r"disable (?:automatic )?memory review", r"stop reviewing memories automatically"), argument="off"),
    _intent("/memory-suggestions", "memory_review.list_all", RiskLevel.SAFE, ("show all memory suggestions", "list every memory suggestion", "show recent approved and rejected memory suggestions"), (r"show (?:me )?all memory suggestions", r"list (?:every|all) memory suggestions", r"show (?:me )?recent approved and rejected memory suggestions"), argument="all"),
    _intent("/memory-suggestions", "memory_review.list", RiskLevel.SAFE, ("show pending memory suggestions", "list memory review suggestions", "what memory suggestions are pending?"), (r"show (?:me )?(?:pending )?memory suggestions", r"list (?:the )?memory review suggestions", r"what memory suggestions are pending")),
    _intent("/approve-memory", "memory_review.approve", RiskLevel.SENSITIVE, ("approve memory suggestion 4", "accept memory suggestion 4", "save suggestion 4"), (r"approve memory suggestion (?P<arg>\d+)", r"accept memory suggestion (?P<arg>\d+)", r"save (?:memory )?suggestion (?P<arg>\d+)"), argument="id"),
    _intent("/reject-memory", "memory_review.reject", RiskLevel.STATEFUL, ("reject memory suggestion 4", "decline memory suggestion 4", "discard suggestion 4"), (r"reject memory suggestion (?P<arg>\d+)", r"decline memory suggestion (?P<arg>\d+)", r"discard (?:memory )?suggestion (?P<arg>\d+)"), argument="id"),
    _intent("/internet", "internet.on", RiskLevel.STATEFUL, ("turn the internet on", "enable internet access", "go online", "enable web access"), (r"turn (?:the )?internet on", r"enable (?:internet|web) access", r"go online", r"let yourself use the internet"), argument="on"),
    _intent("/internet", "internet.off", RiskLevel.STATEFUL, ("turn the internet off", "disable internet access", "go offline", "stop using the web"), (r"turn (?:the )?internet off", r"disable (?:internet|web) access", r"go offline", r"stop using (?:the )?(?:web|internet)"), argument="off"),
    _intent("/internet", "internet.status", RiskLevel.SAFE, ("is internet access enabled?", "show internet status", "are you online?"), (r"is (?:the )?internet access (?:enabled|on)", r"show (?:me )?(?:the )?internet status", r"are you online"), argument="status"),
    _intent("/web-search", "internet.search", RiskLevel.SAFE, ("search the web for Python 3.15", "look online for local news", "find this on the internet: CAN bus"), (r"search (?:the )?(?:web|internet|online) for (?P<arg>.+)", r"look online for (?P<arg>.+)", r"find (?:this )?on the internet:? (?P<arg>.+)"), argument="query"),
    _intent("/fetch-url", "internet.fetch", RiskLevel.SAFE, ("fetch https://example.com", "read the page at https://example.com", "open this web page: https://example.com"), (r"fetch (?P<arg>https://\S+)", r"read the (?:web )?page at (?P<arg>https://\S+)", r"open this web page:? (?P<arg>https://\S+)"), argument="url"),
    _intent("/tools", "tools.list", RiskLevel.SAFE, ("show available tools", "what tools can you use?", "list your tools"), (r"show (?:me )?(?:the )?available tools", r"what tools can you use", r"list (?:your|the) tools")),
    _intent("/tool-permissions", "tools.permissions", RiskLevel.SAFE, ("show tool permissions", "what are the tool permission rules?", "list tool safety settings"), (r"show (?:me )?tool permissions", r"what are the tool permission rules", r"list tool safety settings")),
    _intent("/tool-log", "tools.audit", RiskLevel.SAFE, ("show the tool log", "show recent tool activity", "show the last 10 tool actions"), (r"show (?:me )?(?:the )?tool log", r"show (?:me )?recent tool activity", r"show (?:me )?(?:the )?last (?P<arg>\d+) tool actions"), argument="limit"),
    _intent("/ascii", "ascii.on", RiskLevel.STATEFUL, ("turn ASCII mode on", "enable ASCII art", "use ASCII presentation"), (r"turn ascii (?:mode )?on", r"enable ascii (?:art|presentation|mode)", r"use ascii presentation"), argument="on"),
    _intent("/ascii", "ascii.off", RiskLevel.STATEFUL, ("turn ASCII mode off", "disable ASCII art", "stop using ASCII art"), (r"turn ascii (?:mode )?off", r"disable ascii (?:art|presentation|mode)", r"stop using ascii (?:art|presentation)"), argument="off"),
    _intent("/ascii", "ascii.status", RiskLevel.SAFE, ("is ASCII mode on?", "show ASCII status", "are you using ASCII art?"), (r"is ascii (?:mode|art) (?:on|enabled)", r"show (?:me )?ascii (?:mode )?status", r"are you using ascii art"), argument="status"),
    _intent("/spotify-login", "spotify.login", RiskLevel.STATEFUL, ("log into Spotify", "connect my Spotify account", "start Spotify login"), (r"log in(?:to)? spotify", r"connect (?:to )?(?:my )?spotify(?: account)?", r"start spotify login")),
    _intent("/spotify-status", "spotify.status", RiskLevel.SAFE, ("is Spotify connected?", "what's my Spotify status?", "check Spotify"), (r"is spotify connected", r"what(?:'s| is) (?:my )?spotify status", r"(?:check|show) spotify(?: status)?")),
    _intent("/spotify-logout", "spotify.logout", RiskLevel.SENSITIVE, ("log out of Spotify", "disconnect my Spotify account", "remove Spotify login"), (r"log out of spotify", r"disconnect (?:my )?spotify(?: account)?", r"remove (?:my )?spotify login")),
    _intent("/spotify-devices", "spotify.devices", RiskLevel.SAFE, ("what Spotify devices are available?", "show my Spotify devices", "where can you play music?"), (r"what spotify devices are available", r"(?:show|list) (?:me )?(?:my )?spotify devices", r"where can you play (?:spotify|music)")),
    _intent("/spotify-device", "spotify.device", RiskLevel.STATEFUL, ("play Spotify on device 2", "switch Spotify to the second device", "use device 2 for Spotify"), (r"(?:play|use) spotify on device (?P<arg>\d+)", r"switch spotify to (?:device )?(?P<arg>\d+)", r"use device (?P<arg>\d+) for spotify"), argument="selection"),
    _intent("/music-select", "spotify.music_select", RiskLevel.STATEFUL, ("play the second one", "choose result 2", "pick the second song"), ()),
    _intent("/music-cancel", "spotify.music_cancel", RiskLevel.STATEFUL, ("cancel the music selection", "never mind those Spotify results", "clear the pending songs"), (r"cancel (?:the )?(?:music|song|spotify) selection", r"never mind (?:those|the) spotify results", r"clear (?:the )?pending songs")),
    _intent("/creator aliases", "youtube.aliases", RiskLevel.SAFE, ("show saved creator aliases", "list my YouTube creators", "what creator aliases are saved?"), (r"show (?:me )?(?:the )?saved creator aliases", r"list (?:my )?youtube creators", r"what creator aliases are saved")),
    _intent("/play-latest", "youtube.latest", RiskLevel.STATEFUL, ("play the latest Alpharad video", "open the newest video from Alpharad", "watch Alpharad's latest upload"), ()),
    _intent("/creator-select", "youtube.creator_select", RiskLevel.STATEFUL, ("the first one", "choose result 2", "use the Alpharad channel"), ()),
    _intent("/video-select", "youtube.video_select", RiskLevel.STATEFUL, ("play number 3", "choose the second video", "play the unique video title"), ()),
    _intent("/creator forget", "youtube.forget_alias", RiskLevel.STATEFUL, ("forget the creator alias Alpharad", "remove Alpharad from creator aliases", "delete the saved creator Alpharad"), (r"forget (?:the )?creator alias (?P<arg>.+)", r"remove (?P<arg>.+) from creator aliases", r"delete (?:the )?saved creator (?P<arg>.+)"), argument="alias"),
    _intent("/creator-cancel", "youtube.creator_cancel", RiskLevel.STATEFUL, ("cancel the creator selection", "never mind those channels", "clear the pending creator choice"), (r"cancel (?:the )?creator selection", r"never mind those (?:youtube )?channels", r"clear (?:the )?pending creator choice")),
    _intent("/video-cancel", "youtube.video_cancel", RiskLevel.STATEFUL, ("cancel the video selection", "never mind those videos", "clear the pending video results"), (r"cancel (?:the )?video selection", r"never mind those videos", r"clear (?:the )?pending video results")),
    _intent("/display", "display.status", RiskLevel.SAFE, ("show media display status", "is the display open?", "check the video window"), (r"show (?:me )?(?:the )?media display status", r"is (?:the )?(?:media )?display open", r"check (?:the )?video window"), argument="status"),
    _intent("/display", "display.pause", RiskLevel.STATEFUL, ("pause the media display", "pause the video window", "stop the display temporarily"), (r"pause (?:the )?(?:media )?display", r"pause (?:the )?video window", r"stop (?:the )?display temporarily"), argument="pause"),
    _intent("/display", "display.resume", RiskLevel.STATEFUL, ("resume the media display", "continue the video window", "unpause the display"), (r"resume (?:the )?(?:media )?display", r"continue (?:the )?video window", r"unpause (?:the )?display"), argument="resume"),
    _intent("/display", "display.close", RiskLevel.STATEFUL, ("close the media display", "close the video window", "shut the display window"), (r"close (?:the )?(?:media )?display", r"close (?:the )?video window", r"shut (?:the )?display window"), argument="close"),
    _intent("/time", "host.time", RiskLevel.SAFE, ("what time is it?", "show the local time", "what's the computer's time?"), (r"what time is it", r"show (?:me )?(?:the )?local time", r"what(?:'s| is) (?:the )?computer(?:'s)? time")),
    _intent("/workflows", "workflow.list", RiskLevel.SAFE, ("show recent workflows", "list my workflows", "show the last 5 workflows"), (r"show (?:me )?(?:the )?recent workflows", r"list (?:my )?workflows", r"show (?:me )?(?:the )?last (?P<arg>\d+) workflows"), argument="limit"),
    _intent("/workflow", "workflow.status", RiskLevel.SAFE, ("show workflow 3", "check workflow 3", "what is the status of workflow 3?"), (r"show workflow (?P<arg>\d+)", r"check workflow (?P<arg>\d+)", r"what is the status of workflow (?P<arg>\d+)"), argument="id"),
    _intent("/run-workflow", "workflow.run", RiskLevel.SENSITIVE, ("run workflow 3", "execute workflow 3", "start workflow 3"), (r"run workflow (?P<arg>\d+)", r"execute workflow (?P<arg>\d+)", r"start workflow (?P<arg>\d+)"), argument="id", confirmation=True),
    _intent("/resume-workflow", "workflow.resume", RiskLevel.SENSITIVE, ("resume workflow 3", "continue workflow 3", "restart workflow 3"), (r"resume workflow (?P<arg>\d+)", r"continue workflow (?P<arg>\d+)", r"restart workflow (?P<arg>\d+)"), argument="id", confirmation=True),
    _intent("/cancel-workflow", "workflow.cancel", RiskLevel.SENSITIVE, ("cancel workflow 3", "stop workflow 3", "abort workflow 3"), (r"cancel workflow (?P<arg>\d+)", r"stop workflow (?P<arg>\d+)", r"abort workflow (?P<arg>\d+)"), argument="id"),
    _intent("/clear", "conversation.clear", RiskLevel.STATEFUL, ("clear our conversation", "forget the current chat context", "start a fresh conversation"), (r"clear (?:our|the|this) conversation", r"forget (?:the )?current chat context", r"start (?:a )?fresh conversation")),
    _intent("/exit", "session.exit", RiskLevel.STATEFUL, ("exit Elise", "shut down Elise", "close this session"), (r"exit elise", r"shut down elise", r"close (?:this|the) session")),
)


_NEGATION = re.compile(r"\b(?:do not|don't|never|without|no tools|stop yourself from)\b", re.I)
_INFORMATIONAL = re.compile(r"^(?:what|why|how|when|where|who|which)\b", re.I)
_COMMAND_DISCUSSION = re.compile(r"(?:what does|how does|what happens if|explain)\s+/|\b(?:what is|how does)\s+(?:ascii mode|memory search)\b", re.I)
_ORDINALS: Mapping[str, int] = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def _clean(text: str) -> str:
    return " ".join(normalize_apostrophes(text).strip().split()).rstrip(".?! ")


def _selection_number(text: str) -> int | None:
    match = re.fullmatch(r"(?:(?:play|choose|pick|use) )?(?:the )?(?:(?:number|result|device) )?(?P<n>\d+|first|second|third|fourth|fifth)(?: one| song| track| result| video| device| channel)?", text, re.I)
    if not match:
        match = re.fullmatch(r"(?:switch spotify to|play spotify on) (?:the )?(?P<n>\d+|first|second|third|fourth|fifth)(?: device)?", text, re.I)
    if not match:
        return None
    value = match.group("n").casefold()
    return int(value) if value.isdigit() else _ORDINALS[value]


def _unique_named(text: str, labels: Sequence[str]) -> int | None:
    match = re.fullmatch(r"(?:(?:play|choose|pick|use) )(?:the )?(?P<name>.+?)(?: result| song| track| channel| device)?(?: for spotify)?", text, re.I)
    if not match:
        return None
    wanted = match.group("name").casefold().strip()
    matches = [index for index, label in enumerate(labels, 1) if wanted in label.casefold()]
    return matches[0] if len(matches) == 1 else None


def resolve_contextual_selection(user_input: str, pending: PendingSelections) -> str | None:
    text = _clean(user_input)
    if _NEGATION.search(text):
        return None
    active = [("music", pending.music), ("creator", pending.creator), ("video", pending.video), ("spotify_device", pending.spotify_device)]
    active = [(kind, labels) for kind, labels in active if labels]
    if len(active) != 1:
        return None
    kind, labels = active[0]
    number = _selection_number(text) or _unique_named(text, labels)
    if number is None or number < 1 or number > len(labels):
        return None
    command = {"music": "/music-select", "creator": "/creator-select", "video": "/video-select", "spotify_device": "/spotify-device"}[kind]
    return f"{command} {number}"


def resolve_natural_command(user_input: str, pending: PendingSelections | None = None) -> ResolvedNaturalCommand | None:
    if not user_input.strip() or user_input.lstrip().startswith("/"):
        return None
    text = _clean(user_input)
    if _NEGATION.search(text) or _COMMAND_DISCUSSION.search(text):
        return None
    contextual = resolve_contextual_selection(text, pending or PendingSelections())
    if contextual:
        synthetic = _intent(contextual.split()[0], "selection.contextual", RiskLevel.STATEFUL, (text,), ())
        return ResolvedNaturalCommand(contextual, synthetic)
    for intent in COMMAND_INTENTS:
        for pattern in intent.patterns:
            match = re.fullmatch(pattern, text, re.I)
            if not match:
                continue
            groups = match.groupdict()
            if "category" in groups:
                category = groups["category"].casefold().rstrip("s")
                argument = f"{category} {groups['value'].strip()}"
            elif "root" in groups:
                root = groups["root"].casefold()
                path = (groups.get("path") or ".").strip()
                recursive = " --recursive" if groups.get("recursive") or text.casefold().startswith("recursively ") else ""
                argument = f"{root} {path}{recursive}"
            elif groups.get("arg"):
                argument = groups["arg"].strip()
            else:
                argument = intent.argument if intent.argument in {"on", "off", "status", "pause", "resume", "close", "all"} else None
            canonical = intent.canonical_command + (f" {argument}" if argument else "")
            return ResolvedNaturalCommand(canonical, intent)
    return None


def inventory() -> tuple[CommandIntent, ...]:
    return COMMAND_INTENTS
