from __future__ import annotations

from dataclasses import dataclass
import re

from text_normalization import normalize_apostrophes


# Human/design reference only: https://www.asciiart.eu/
# This local guidance captures general ASCII-art conventions; generation never
# fetches, copies, or otherwise treats the ASCII Art Archive as a content source.
ASCII_ART_REFERENCE_URL = "https://www.asciiart.eu/"
VISUAL_ASCII_STYLE_GUIDE = (
    "Create one coherent, original piece of visual ASCII art. Start with a recognizable silhouette and "
    "composition, then add only as much detail as the subject and display size support. Use whitespace "
    "deliberately, keep character density consistent, use symmetry where appropriate, and favor clean "
    "outlines and monospaced alignment. Choose characters deliberately, including /, \\, _, |, (, ), ., "
    "', -, =, and ^ where they help describe the form. Prefer a recognizable shape over excessive detail. "
    "Keep the result reasonably sized for Elise's terminal/display and use ASCII characters where practical. "
    "Output only the artwork in one fenced text code block. Do not put explanations, labels, or other prose "
    "inside the artwork unless the user explicitly requests them. Avoid Markdown emphasis syntax that could "
    "corrupt the design. Do not claim the drawing is exact, copied, or sourced from another artist, and do "
    "not reproduce a known ASCII artwork. This is locally stored style guidance inspired by established "
    f"ASCII-art conventions, with {ASCII_ART_REFERENCE_URL} used only as a human/design reference; do not "
    "contact it or use it as an artwork database."
)


@dataclass(frozen=True)
class AsciiArtRequest:
    subject: str | None


@dataclass(frozen=True)
class AsciiBannerRequest:
    text: str


_EXPLICIT_ASCII_REQUEST = re.compile(
    r"\b(?:draw|make|create|generate|render|write|use)\b[^\r\n]*\bascii(?:[ -]art)?\b"
    r"|\bascii[ -]art\b[^\r\n]*\b(?:of|for)\b",
    re.IGNORECASE,
)
_NEGATED_ASCII_REQUEST = re.compile(
    r"\b(?:don't|do not|never)\b[^\r\n,.!?;]*\b(?:draw|make|create|generate|render|write|spell|use)\b"
    r"[^\r\n,.!?;]*\bascii(?:[ -]art)?\b",
    re.IGNORECASE,
)
_SUBJECT_PATTERNS = (
    re.compile(r"^\s*draw\s+(?:me\s+)?(?P<subject>.+?)\s+in\s+ascii(?:[ -]art)?[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:make|create|generate|render)\s+ascii(?:[ -]art)?\s+of\s+(?P<subject>.+?)[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*write\s+(?P<subject>.+?)\s+in\s+ascii(?:[ -]art)?[.!?]?\s*$", re.IGNORECASE),
)
_BANNER_PATTERNS = (
    re.compile(r"^\s*write\s+(?P<text>.+)\s+in\s+ascii(?:[ -](?:art|letters?))?[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:make|create|generate|render)\s+(?:an?\s+)?ascii(?:[ -](?:art\s+)?banner)\s+(?:saying|that\s+says)\s+(?P<text>.+)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:make|create|generate|render)\s+(?P<text>.+)\s+in\s+ascii\s+letters?[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*spell\s+(?P<text>.+)\s+in\s+ascii(?:[ -](?:art|letters?))?[.!?]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*ascii\s+text\s+(?:saying|that\s+says)\s+(?P<text>.+)\s*$", re.IGNORECASE),
)

# Five rows keeps banners legible without overwhelming Elise's terminal display.
_BANNER_FONT_ROWS = {
    "A": (" ### ", "#   #", "#####", "#   #", "#   #"), "B": ("#### ", "#   #", "#### ", "#   #", "#### "),
    "C": (" ####", "#    ", "#    ", "#    ", " ####"), "D": ("#### ", "#   #", "#   #", "#   #", "#### "),
    "E": ("#####", "#    ", "#### ", "#    ", "#####"), "F": ("#####", "#    ", "#### ", "#    ", "#    "),
    "G": (" ####", "#    ", "# ###", "#   #", " ####"), "H": ("#   #", "#   #", "#####", "#   #", "#   #"),
    "I": ("#####", "  #  ", "  #  ", "  #  ", "#####"), "J": ("#####", "   # ", "   # ", "#  # ", " ##  "),
    "K": ("#   #", "#  # ", "###  ", "#  # ", "#   #"), "L": ("#    ", "#    ", "#    ", "#    ", "#####"),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #"), "N": ("#   #", "##  #", "# # #", "#  ##", "#   #"),
    "O": (" ### ", "#   #", "#   #", "#   #", " ### "), "P": ("#### ", "#   #", "#### ", "#    ", "#    "),
    "Q": (" ### ", "#   #", "#   #", "#  ##", " ####"), "R": ("#### ", "#   #", "#### ", "#  # ", "#   #"),
    "S": (" ####", "#    ", " ### ", "    #", "#### "), "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  "),
    "U": ("#   #", "#   #", "#   #", "#   #", " ### "), "V": ("#   #", "#   #", "#   #", " # # ", "  #  "),
    "W": ("#   #", "#   #", "# # #", "## ##", "#   #"), "X": ("#   #", " # # ", "  #  ", " # # ", "#   #"),
    "Y": ("#   #", " # # ", "  #  ", "  #  ", "  #  "), "Z": ("#####", "   # ", "  #  ", " #   ", "#####"),
    "0": (" ### ", "#  ##", "# # #", "##  #", " ### "), "1": ("  #  ", " ##  ", "  #  ", "  #  ", "#####"),
    "2": (" ### ", "#   #", "   # ", "  #  ", "#####"), "3": ("#### ", "    #", " ### ", "    #", "#### "),
    "4": ("#  # ", "#  # ", "#####", "   # ", "   # "), "5": ("#####", "#    ", "#### ", "    #", "#### "),
    "6": (" ### ", "#    ", "#### ", "#   #", " ### "), "7": ("#####", "   # ", "  #  ", " #   ", "#    "),
    "8": (" ### ", "#   #", " ### ", "#   #", " ### "), "9": (" ### ", "#   #", " ####", "    #", " ### "),
    " ": ("   ",) * 5, "-": ("     ", "     ", "#####", "     ", "     "),
    "'": ("  #  ", "  #  ", "     ", "     ", "     "), ".": ("     ", "     ", "     ", "     ", "  #  "),
    "_": ("     ", "     ", "     ", "     ", "#####"),
}
_UNKNOWN_GLYPH = ("?????", "    ?", "  ?? ", "     ", "  ?  ")


def detect_ascii_art_request(message: str) -> AsciiArtRequest | None:
    """Recognize only explicit ASCII-related creation or presentation requests."""

    normalized = normalize_apostrophes(message)
    if _NEGATED_ASCII_REQUEST.search(normalized) or not _EXPLICIT_ASCII_REQUEST.search(normalized):
        return None
    for pattern in _SUBJECT_PATTERNS:
        match = pattern.match(message)
        if match:
            return AsciiArtRequest(match.group("subject"))
    return AsciiArtRequest(None)


def detect_ascii_banner_request(message: str) -> AsciiBannerRequest | None:
    """Extract explicit text-banner wording without correcting the requested text."""

    normalized = normalize_apostrophes(message)
    if _NEGATED_ASCII_REQUEST.search(normalized):
        return None
    for pattern in _BANNER_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return AsciiBannerRequest(match.group("text"))
    return None


def render_ascii_banner(text: str) -> str:
    """Render text deterministically, preserving supported neighbors of unknown glyphs."""

    normalized = normalize_apostrophes(text).upper()
    rows: list[str] = []
    for row_index in range(5):
        rows.append(" ".join(_BANNER_FONT_ROWS.get(character, _UNKNOWN_GLYPH)[row_index] for character in normalized).rstrip())
    return "```text\n" + "\n".join(rows) + "\n```"


def handle_ascii_command(user_input: str, enabled: bool) -> tuple[bool, str] | None:
    """Handle the session-scoped ASCII preference command."""

    normalized = " ".join(user_input.casefold().split())
    if normalized != "/ascii" and not normalized.startswith("/ascii "):
        return None
    if normalized == "/ascii on":
        return True, "Elise: ASCII-art presentation is enabled for this session."
    if normalized == "/ascii off":
        return False, "Elise: ASCII-art presentation is disabled."
    if normalized == "/ascii status":
        state = "enabled" if enabled else "disabled"
        return enabled, f"Elise: ASCII-art presentation is {state} for this session."
    return enabled, "Elise: Usage: /ascii on|off|status"


def ascii_art_instruction(message: str, enabled: bool) -> str | None:
    """Build the session's ASCII-art presentation instruction."""

    request = detect_ascii_art_request(message)
    if request is not None:
        if detect_ascii_banner_request(message) is not None:
            return (
                "This is a text/banner request handled by Elise's deterministic local banner renderer; "
                "do not send it to a model or an external service."
            )
        subject_rule = (
            f"Preserve the requested subject or text exactly as written: {request.subject!r}."
            if request.subject is not None
            else "Preserve any requested subject or text exactly as written."
        )
        return (
            "The user explicitly requested textual ASCII art. Fulfill that request using the local model only; "
            "do not use tools, image conversion, external APIs, animation, or terminal effects. "
            f"{subject_rule} {VISUAL_ASCII_STYLE_GUIDE}"
        )
    if enabled:
        return (
            "ASCII-art presentation is enabled for this session. You may use textual ASCII art when it naturally "
            "supports the user's request. Keep ordinary prose ordinary, preserve requested text exactly, and put "
            "any artwork in a fenced text code block. If you choose visual ASCII art, follow this local guide: "
            f"{VISUAL_ASCII_STYLE_GUIDE}"
        )
    return (
        "ASCII-art presentation is off, and the current message does not explicitly request ASCII. "
        "Do not generate ASCII art or use it as a fallback for a generic request to draw, make, or create "
        "a picture. If the available capabilities cannot produce a graphical image, respond normally without "
        "substituting ASCII art, and do not claim that an image was created or displayed."
    )


def preserve_ascii_formatting(response: str, explicit_request: bool) -> str:
    """Ensure explicit ASCII output has a spacing-preserving container."""

    if not explicit_request or "```" in response:
        return response
    return f"```text\n{response}\n```"
