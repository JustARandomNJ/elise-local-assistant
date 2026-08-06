from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import socket
import tempfile
import xml.etree.ElementTree as ET
import zlib
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


SETTINGS_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_500_000
MAX_COMPRESSED_RESPONSE_BYTES = 1_500_000
MAX_PROVIDER_ERROR_BYTES = 65_536
DECOMPRESSION_CHUNK_BYTES = 64_000
DEFAULT_PAGE_CHARS = 6_000
MAX_PAGE_CHARS = 30_000
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 8
MAX_QUERY_CHARS = 500
MAX_RESULT_TITLE_CHARS = 300
MAX_RESULT_SNIPPET_CHARS = 700
MAX_REDIRECTS = 5
MAX_EXTRACTED_PAGE_LINKS = 300
MAX_EXTRACTED_LINK_TEXT_CHARS = 300
MIN_RELEVANCE_SCORE = 6.0
MIN_SUBJECT_COVERAGE = 0.6
MAX_SEARCH_QUERY_VARIANTS = 4

SEARCH_PROVIDER_NAME = "Bing RSS with DuckDuckGo fallbacks"
BING_RSS_ENDPOINT = "https://www.bing.com/search"
DUCKDUCKGO_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36 "
    "EliseLocalAssistant/1.0"
)

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}

BLOCKED_HOST_SUFFIXES = {
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".onion",
    ".invalid",
    ".test",
}

ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
}

SEARCH_STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "give",
    "has",
    "have",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "of",
    "on",
    "or",
    "please",
    "show",
    "tell",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "this",
    "those",
    "to",
    "up",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
}

FRESHNESS_QUERY_TOKENS = {
    "breaking",
    "current",
    "currently",
    "latest",
    "new",
    "newest",
    "recent",
    "recently",
    "today",
    "tonight",
    "updated",
}

GENERIC_INTENT_TOKENS = {
    "announcement",
    "announcements",
    "change",
    "changes",
    "documentation",
    "docs",
    "feature",
    "features",
    "information",
    "major",
    "news",
    "official",
    "overview",
    "release",
    "releases",
    "update",
    "updates",
    "version",
    "versions",
}

TECHNICAL_QUERY_TOKENS = {
    "api",
    "apis",
    "compiler",
    "documentation",
    "docs",
    "firmware",
    "framework",
    "github",
    "language",
    "library",
    "linux",
    "ollama",
    "package",
    "python",
    "release",
    "runtime",
    "sdk",
    "software",
    "toolchain",
    "version",
    "windows",
}

TECHNICAL_RELEVANCE_TERMS = {
    "announcement",
    "changelog",
    "documentation",
    "docs",
    "download",
    "downloads",
    "feature",
    "features",
    "official",
    "release",
    "releases",
    "roadmap",
    "version",
    "versions",
    "what's new",
    "whats new",
}

GENERIC_NEWS_DOMAINS = {
    "abcnews.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "cbsnews.com",
    "cnn.com",
    "foxnews.com",
    "google.com",
    "news.google.com",
    "nbcnews.com",
    "newsweek.com",
    "reuters.com",
    "usatoday.com",
}


PRIMARY_UPDATE_PATH_MARKERS = {
    "/changelog",
    "/changes/",
    "/downloads/release/",
    "/news/",
    "/release-notes",
    "/releasenotes",
    "/release/",
    "/releases/",
    "/whatsnew/",
}

PRIMARY_UPDATE_TITLE_PATTERNS = (
    r"\bwhat'?s new\b",
    r"\brelease notes?\b",
    r"\bchangelog\b",
    r"\breleased\b",
    r"\brelease\b",
    r"\bnew features?\b",
    r"\bchanges in\b",
)

UPDATE_FALSE_POSITIVE_TERMS = {
    "compiler",
    "license",
    "licensing",
    "online compiler",
    "playground",
    "tutorial",
}

UPDATE_QUERY_TEMPLATES = {
    "python": [
        (
            "site:python.org/downloads/release "
            "python latest release"
        ),
        (
            "site:docs.python.org/3/whatsnew "
            "python latest what's new"
        ),
    ],
    "ollama": [
        (
            "site:github.com/ollama/ollama/releases "
            "ollama latest release"
        ),
        (
            "site:ollama.com/blog "
            "ollama latest announcement"
        ),
    ],
    "zephyr": [
        (
            "site:docs.zephyrproject.org/latest/releases "
            "zephyr latest release notes"
        ),
    ],
    "esp32": [
        (
            "site:docs.espressif.com "
            "esp32 latest release notes"
        ),
    ],
}

AUTHORITY_DOMAIN_HINTS = {
    "arduino": {
        "arduino.cc",
        "docs.arduino.cc",
    },
    "esp32": {
        "docs.espressif.com",
        "espressif.com",
        "github.com/espressif",
    },
    "git": {
        "git-scm.com",
        "github.com/git",
    },
    "github": {
        "docs.github.com",
        "github.com",
    },
    "linux": {
        "kernel.org",
        "docs.kernel.org",
    },
    "ollama": {
        "ollama.com",
        "docs.ollama.com",
        "github.com/ollama",
    },
    "openai": {
        "openai.com",
        "platform.openai.com",
    },
    "python": {
        "python.org",
        "docs.python.org",
        "peps.python.org",
        "github.com/python",
    },
    "rust": {
        "rust-lang.org",
        "doc.rust-lang.org",
        "github.com/rust-lang",
    },
    "zephyr": {
        "docs.zephyrproject.org",
        "zephyrproject.org",
        "github.com/zephyrproject-rtos",
    },
}


class InternetError(Exception):
    """Raised when a network request is invalid, unsafe, or unavailable."""


class ProviderHTTPError(InternetError):
    """A sanitized HTTP failure from a deterministic JSON provider."""

    def __init__(self, status: int, reason: str | None = None) -> None:
        super().__init__(f"The provider returned HTTP {status}.")
        self.status = status
        self.reason = reason


def _google_error_reason(body: bytes, charset: str = "utf-8") -> str | None:
    """Extract only Google's bounded, non-secret machine-readable reason."""

    try:
        payload = json.loads(body.decode(charset, errors="replace"))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    errors = error.get("errors") if isinstance(error, dict) else None
    first = errors[0] if isinstance(errors, list) and errors else None
    reason = first.get("reason") if isinstance(first, dict) else None
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", reason):
        return None
    return reason


def _provider_failure(status: int, reason: str | None) -> dict[str, Any]:
    """Normalize provider failures without retaining URLs, bodies, or headers."""

    normalized = reason.casefold() if reason else ""
    if normalized == "channelnotfound":
        code, message = "channel_not_found", "No exact YouTube handle matched."
    elif normalized in {"quotaexceeded", "dailylimitexceeded"}:
        code, message = "quota_exceeded", "YouTube API quota is unavailable."
    elif normalized in {"keyinvalid", "accessnotconfigured"}:
        code, message = "api_configuration_error", "YouTube API configuration is invalid or unavailable."
    elif normalized in {"invalidcriteria", "invalidparameter"}:
        code, message = "provider_request_error", "YouTube rejected the provider request."
    elif status >= 500:
        code, message = "provider_unavailable", "YouTube is temporarily unavailable."
    else:
        code, message = "provider_http_error", "YouTube returned an HTTP error."
    return {"success": False, "status": status, "provider_reason": reason, "error_code": code, "error": message}


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Validate every HTTP redirect before urllib follows it."""

    max_redirections = MAX_REDIRECTS
    max_repeats = 2

    def __init__(
        self,
        validator: Callable[[str], str],
    ) -> None:
        super().__init__()
        self.validator = validator

    def redirect_request(
        self,
        request,
        file_pointer,
        code,
        message,
        headers,
        new_url,
    ):
        absolute_url = urljoin(
            request.full_url,
            new_url,
        )
        validated_url = self.validator(
            absolute_url
        )

        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            validated_url,
        )


class _ReadableHTMLParser(HTMLParser):
    """Extract a page title, readable text, and bounded HTTP(S) links."""

    IGNORED_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "template",
    }

    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )
        self.ignored_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.link_stack: list[
            dict[str, Any]
        ] = []
        self.link_records: list[
            dict[str, Any]
        ] = []

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:
        normalized = tag.lower()

        if normalized in self.IGNORED_TAGS:
            self.ignored_depth += 1
            return

        if self.ignored_depth:
            return

        if normalized == "title":
            self.in_title = True

        if normalized == "a":
            attribute_map = {
                str(name).lower(): str(
                    value
                    or ""
                )
                for name, value in attrs
            }
            self.link_stack.append(
                {
                    "href": attribute_map.get(
                        "href",
                        "",
                    ).strip(),
                    "text_parts": [],
                }
            )

        if normalized in self.BLOCK_TAGS:
            self.text_parts.append(
                "\n"
            )

    def handle_endtag(
        self,
        tag: str,
    ) -> None:
        normalized = tag.lower()

        if normalized in self.IGNORED_TAGS:
            if self.ignored_depth:
                self.ignored_depth -= 1
            return

        if self.ignored_depth:
            return

        if normalized == "title":
            self.in_title = False

        if (
            normalized == "a"
            and self.link_stack
        ):
            self.link_records.append(
                self.link_stack.pop()
            )

        if normalized in self.BLOCK_TAGS:
            self.text_parts.append(
                "\n"
            )

    def handle_data(
        self,
        data: str,
    ) -> None:
        if self.ignored_depth:
            return

        if self.in_title:
            self.title_parts.append(
                data
            )

        if self.link_stack:
            self.link_stack[-1][
                "text_parts"
            ].append(
                data
            )

        self.text_parts.append(
            data
        )

    def get_title(self) -> str:
        return _normalize_inline_text(
            " ".join(
                self.title_parts
            )
        )

    def get_text(self) -> str:
        return _normalize_multiline_text(
            "".join(
                self.text_parts
            )
        )

    def get_links(
        self,
        base_url: str,
    ) -> list[dict[str, str]]:
        """Return deduplicated absolute HTTP(S) links with normalized labels."""

        links: list[
            dict[str, str]
        ] = []
        seen_urls: set[str] = set()

        for record in self.link_records:
            raw_href = str(
                record.get(
                    "href",
                    "",
                )
            ).strip()

            if not raw_href:
                continue

            absolute_url = urljoin(
                base_url,
                raw_href,
            )

            try:
                parsed = urlsplit(
                    absolute_url
                )
            except ValueError:
                continue

            if parsed.scheme.lower() not in {
                "http",
                "https",
            }:
                continue

            normalized_url = urlunsplit(
                (
                    parsed.scheme.lower(),
                    parsed.netloc,
                    parsed.path or "/",
                    parsed.query,
                    "",
                )
            )

            if normalized_url in seen_urls:
                continue

            seen_urls.add(
                normalized_url
            )
            label = _truncate(
                _normalize_inline_text(
                    " ".join(
                        str(part)
                        for part in record.get(
                            "text_parts",
                            [],
                        )
                    )
                ),
                MAX_EXTRACTED_LINK_TEXT_CHARS,
            )

            links.append(
                {
                    "text": label,
                    "url": normalized_url,
                }
            )

            if (
                len(
                    links
                )
                >= MAX_EXTRACTED_PAGE_LINKS
            ):
                break

        return links


class _DuckDuckGoHTMLParser(HTMLParser):
    """Extract result links and snippets from DuckDuckGo HTML variants."""

    TITLE_CLASSES = {
        "result__a",
        "result-link",
    }

    SNIPPET_CLASSES = {
        "result__snippet",
        "result-snippet",
    }

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )
        self.results: list[dict[str, str]] = []
        self.capture_title = False
        self.capture_snippet = False
        self.current_href = ""
        self.current_parts: list[str] = []

    @staticmethod
    def _class_names(
        attrs,
    ) -> set[str]:
        for key, value in attrs:
            if key == "class" and value:
                return set(
                    str(value).split()
                )

        return set()

    @staticmethod
    def _attribute(
        attrs,
        target: str,
    ) -> str:
        for key, value in attrs:
            if key == target and value:
                return str(value)

        return ""

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:
        normalized_tag = tag.lower()
        classes = self._class_names(
            attrs
        )
        href = self._attribute(
            attrs,
            "href",
        )
        rel = self._attribute(
            attrs,
            "rel",
        ).lower()

        is_title_link = (
            normalized_tag == "a"
            and (
                bool(
                    classes
                    & self.TITLE_CLASSES
                )
                or (
                    "nofollow" in rel
                    and href
                    and not href.startswith(
                        "#"
                    )
                    and "duckduckgo.com/settings" not in href
                )
            )
        )

        if is_title_link:
            self.capture_title = True
            self.current_href = href
            self.current_parts = []
            return

        if (
            classes
            & self.SNIPPET_CLASSES
            and normalized_tag
            in {
                "a",
                "div",
                "span",
                "td",
            }
        ):
            self.capture_snippet = True
            self.current_parts = []

    def handle_endtag(
        self,
        tag: str,
    ) -> None:
        normalized = tag.lower()

        if (
            self.capture_title
            and normalized == "a"
        ):
            title = _normalize_inline_text(
                " ".join(
                    self.current_parts
                )
            )
            url = _decode_duckduckgo_result_url(
                self.current_href
            )

            if title and url:
                self.results.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": "",
                    }
                )

            self.capture_title = False
            self.current_href = ""
            self.current_parts = []
            return

        if (
            self.capture_snippet
            and normalized
            in {
                "a",
                "div",
                "span",
                "td",
            }
        ):
            snippet = _normalize_inline_text(
                " ".join(
                    self.current_parts
                )
            )

            if self.results and snippet:
                if not self.results[-1][
                    "snippet"
                ]:
                    self.results[-1][
                        "snippet"
                    ] = snippet

            self.capture_snippet = False
            self.current_parts = []

    def handle_data(
        self,
        data: str,
    ) -> None:
        if (
            self.capture_title
            or self.capture_snippet
        ):
            self.current_parts.append(
                data
            )


def _normalize_inline_text(
    value: str,
) -> str:
    return " ".join(
        value.split()
    )


def _normalize_multiline_text(
    value: str,
) -> str:
    lines: list[str] = []

    for raw_line in value.splitlines():
        cleaned = _normalize_inline_text(
            raw_line
        )

        if not cleaned:
            if (
                lines
                and lines[-1] != ""
            ):
                lines.append("")
            continue

        lines.append(
            cleaned
        )

    while (
        lines
        and lines[-1] == ""
    ):
        lines.pop()

    return "\n".join(
        lines
    )


def _truncate(
    value: str,
    maximum: int,
) -> str:
    if len(value) <= maximum:
        return value

    return (
        value[: maximum - 3]
        + "..."
    )


def _decode_duckduckgo_result_url(
    raw_url: str,
) -> str:
    cleaned = raw_url.strip()

    if cleaned.startswith("//"):
        cleaned = (
            "https:"
            + cleaned
        )

    parsed = urlsplit(
        cleaned
    )

    if (
        parsed.hostname
        and parsed.hostname.lower().endswith(
            "duckduckgo.com"
        )
    ):
        query = parse_qs(
            parsed.query
        )
        redirected = query.get(
            "uddg",
            [],
        )

        if redirected:
            return unquote(
                redirected[0]
            ).strip()

    return cleaned


def _strip_html_to_inline_text(
    value: str,
) -> str:
    """Convert a small HTML fragment such as an RSS description to text."""

    parser = _ReadableHTMLParser()

    try:
        parser.feed(
            value
        )
        parser.close()
    except Exception:
        return _normalize_inline_text(
            re.sub(
                r"<[^>]+>",
                " ",
                value,
            )
        )

    return _normalize_inline_text(
        parser.get_text()
    )


def _parse_bing_rss_results(
    xml_text: str,
) -> list[dict[str, str]]:
    """Parse Bing's structured RSS search response."""

    root = ET.fromstring(
        xml_text
    )
    parsed_results: list[
        dict[str, str]
    ] = []

    for item in root.findall(
        ".//item"
    ):
        title = _normalize_inline_text(
            item.findtext(
                "title",
                default="",
            )
        )
        url = (
            item.findtext(
                "link",
                default="",
            )
            .strip()
        )
        description = (
            item.findtext(
                "description",
                default="",
            )
        )
        snippet = _strip_html_to_inline_text(
            description
        )

        if title and url:
            parsed_results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                }
            )

    return parsed_results


def _normalize_search_token(
    token: str,
) -> str:
    """Normalize a token for lightweight local search scoring."""

    normalized = token.lower().strip(
        "._-"
    )

    aliases = {
        "announcements": "announcement",
        "changes": "change",
        "documents": "documentation",
        "features": "feature",
        "releases": "release",
        "updates": "update",
        "versions": "version",
    }

    return aliases.get(
        normalized,
        normalized,
    )


def _tokenize_search_text(
    value: str,
) -> list[str]:
    """Tokenize text while preserving common programming names such as C++."""

    raw_tokens = re.findall(
        r"[a-z0-9][a-z0-9+#._-]*",
        value.lower(),
    )

    return [
        normalized
        for raw_token in raw_tokens
        if (
            normalized := _normalize_search_token(
                raw_token
            )
        )
    ]


def _ordered_unique(
    values: list[str],
) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        if value in seen:
            continue

        seen.add(
            value
        )
        output.append(
            value
        )

    return output


def _query_token_groups(
    query: str,
) -> dict[str, list[str]]:
    """Separate subject terms from freshness and generic intent terms."""

    tokens = _ordered_unique(
        _tokenize_search_text(
            query
        )
    )

    meaningful = [
        token
        for token in tokens
        if token not in SEARCH_STOP_WORDS
    ]
    freshness = [
        token
        for token in meaningful
        if token in FRESHNESS_QUERY_TOKENS
    ]
    intent = [
        token
        for token in meaningful
        if (
            token in GENERIC_INTENT_TOKENS
            or token in freshness
        )
    ]
    subject = [
        token
        for token in meaningful
        if (
            token not in GENERIC_INTENT_TOKENS
            and token not in FRESHNESS_QUERY_TOKENS
        )
    ]

    if not subject:
        subject = [
            token
            for token in meaningful
            if token not in freshness
        ]

    return {
        "all": meaningful,
        "subject": subject,
        "intent": _ordered_unique(
            intent
        ),
        "freshness": freshness,
    }


def _is_technical_query(
    query: str,
) -> bool:
    tokens = set(
        _tokenize_search_text(
            query
        )
    )

    return bool(
        tokens
        & TECHNICAL_QUERY_TOKENS
    )


def _authority_hints_for_query(
    query: str,
) -> list[str]:
    tokens = set(
        _tokenize_search_text(
            query
        )
    )
    hints: list[str] = []

    for keyword, domains in AUTHORITY_DOMAIN_HINTS.items():
        if keyword in tokens:
            hints.extend(
                sorted(
                    domains
                )
            )

    return _ordered_unique(
        hints
    )



def _query_requests_documentation(
    query: str,
) -> bool:
    intent = set(
        _query_token_groups(
            query
        )["intent"]
    )

    return bool(
        intent
        & {
            "documentation",
            "docs",
        }
    )


def _query_requests_change_information(
    query: str,
) -> bool:
    groups = _query_token_groups(
        query
    )
    intent = set(
        groups["intent"]
    )

    explicit_change = bool(
        intent
        & {
            "announcement",
            "change",
            "feature",
            "release",
            "update",
            "version",
        }
    )
    freshness_change = (
        bool(
            intent
            & {
                "latest",
                "newest",
                "recent",
            }
        )
        and not _query_requests_documentation(
            query
        )
        and "news" not in intent
    )

    return (
        explicit_change
        or freshness_change
    )


def _subject_phrase(
    query: str,
) -> str:
    subject = _query_token_groups(
        query
    )["subject"]

    return " ".join(
        subject
    ).strip()


def _primary_update_evidence(
    *,
    title: str,
    url: str,
) -> tuple[
    bool,
    list[str],
]:
    """
    Require a version-specific release page or What's New page.

    Generic indexes, aggregate changelogs, licensing pages, and snippet-only
    version mentions are not primary evidence for the latest released version.
    """

    title_lower = title.lower()

    try:
        parsed = urlsplit(
            url
        )
        path_lower = (
            parsed.path
            or "/"
        ).lower()
    except ValueError:
        path_lower = ""

    generic_update_page = bool(
        re.search(
            r"/whatsnew/(?:index|changelog)\.(?:html?|xhtml)$",
            path_lower,
        )
    )

    if generic_update_page:
        return (
            False,
            [],
        )

    false_positive = any(
        term in title_lower
        for term in UPDATE_FALSE_POSITIVE_TERMS
    )

    reasons: list[str] = []

    for marker in sorted(
        PRIMARY_UPDATE_PATH_MARKERS
    ):
        if marker in path_lower:
            reasons.append(
                f"url:{marker}"
            )

    for pattern in PRIMARY_UPDATE_TITLE_PATTERNS:
        if re.search(
            pattern,
            title_lower,
        ):
            reasons.append(
                "title:"
                + pattern
            )

    title_version = re.search(
        r"\b\d+\.\d+(?:\.\d+)?"
        r"(?:a\d+|b\d+|rc\d+)?\b",
        title_lower,
    )
    path_version = re.search(
        r"(?:^|[/_-])(?:python[-_/]?)?"
        r"(?:\d+\.\d+(?:\.\d+)?|\d{3,})"
        r"(?:[/_.-]|$)",
        path_lower,
    )
    version_specific = bool(
        title_version
        or path_version
    )

    if false_positive:
        return (
            False,
            [],
        )

    if not version_specific:
        return (
            False,
            [],
        )

    return (
        bool(
            reasons
        ),
        reasons,
    )


def focus_search_query(
    query: str,
) -> str:
    """
    Rewrite a conversational question into concise provider search terms.

    The original query remains in the audit log and returned metadata.
    """

    groups = _query_token_groups(
        query
    )
    subject = groups[
        "subject"
    ]
    intent = groups[
        "intent"
    ]

    focused_tokens = _ordered_unique(
        subject
        + intent
    )

    if _is_technical_query(
        query
    ):
        documentation_intent = bool(
            set(
                intent
            )
            & {
                "documentation",
                "docs",
            }
        )
        change_intent = bool(
            set(
                intent
            )
            & {
                "announcement",
                "change",
                "feature",
                "latest",
                "newest",
                "recent",
                "release",
                "update",
                "version",
            }
        )

        if (
            documentation_intent
            and not change_intent
        ):
            focused_tokens = _ordered_unique(
                focused_tokens
                + [
                    "official",
                    "reference",
                ]
            )
        else:
            focused_tokens = _ordered_unique(
                focused_tokens
                + [
                    "official",
                    "release",
                    "notes",
                    "documentation",
                ]
            )

    if not focused_tokens:
        focused_tokens = groups[
            "all"
        ]

    focused = " ".join(
        focused_tokens
    ).strip()

    return (
        focused
        or query.strip()
    )


def build_search_query_variants(
    query: str,
) -> list[str]:
    """
    Build focused variants with intent-specific official paths first.

    Technical update questions receive release-specific templates before the
    generic authority-domain and focused queries.
    """

    focused = focus_search_query(
        query
    )
    subject_phrase = _subject_phrase(
        query
    )
    variants: list[str] = []
    subject_tokens = set(
        _query_token_groups(
            query
        )["subject"]
    )

    if _query_requests_change_information(
        query
    ):
        for subject_token in subject_tokens:
            variants.extend(
                UPDATE_QUERY_TEMPLATES.get(
                    subject_token,
                    [],
                )
            )

    authority_hints = (
        _authority_hints_for_query(
            query
        )
    )

    if authority_hints:
        primary_domain = authority_hints[
            0
        ]

        if "/" in primary_domain:
            primary_domain = (
                primary_domain.split(
                    "/",
                    1,
                )[0]
            )

        if _query_requests_change_information(
            query
        ):
            variants.append(
                f"site:{primary_domain} "
                f"{subject_phrase or focused} "
                "latest release notes changelog"
            )
        else:
            variants.append(
                f"site:{primary_domain} {focused}"
            )

    variants.append(
        focused
    )

    return _ordered_unique(
        variants
    )[:MAX_SEARCH_QUERY_VARIANTS]


def _url_relevance_text(
    url: str,
) -> tuple[str, str]:
    try:
        parsed = urlsplit(
            url
        )
    except ValueError:
        return "", ""

    hostname = (
        parsed.hostname
        or ""
    ).lower()
    path_text = (
        parsed.path
        .replace(
            "/",
            " ",
        )
        .replace(
            "-",
            " ",
        )
        .replace(
            "_",
            " ",
        )
        .replace(
            ".",
            " ",
        )
    )

    return (
        hostname,
        path_text,
    )


def _domain_matches_hint(
    hostname: str,
    url: str,
    hint: str,
) -> bool:
    normalized_hint = hint.lower()

    if "/" in normalized_hint:
        domain, path_prefix = (
            normalized_hint.split(
                "/",
                1,
            )
        )
        return (
            (
                hostname == domain
                or hostname.endswith(
                    "."
                    + domain
                )
            )
            and (
                "/"
                + path_prefix
            )
            in url.lower()
        )

    return (
        hostname == normalized_hint
        or hostname.endswith(
            "."
            + normalized_hint
        )
    )


def _score_search_result(
    *,
    original_query: str,
    focused_query: str,
    title: str,
    snippet: str,
    url: str,
) -> dict[str, Any]:
    """Score topicality and enforce intent-specific evidence requirements."""

    groups = _query_token_groups(
        original_query
    )
    subject_terms = set(
        groups["subject"]
    )
    intent_terms = set(
        groups["intent"]
    )
    focused_terms = set(
        _tokenize_search_text(
            focused_query
        )
    )

    hostname, path_text = (
        _url_relevance_text(
            url
        )
    )
    title_terms = set(
        _tokenize_search_text(
            title
        )
    )
    snippet_terms = set(
        _tokenize_search_text(
            snippet
        )
    )
    url_terms = set(
        _tokenize_search_text(
            hostname
            + " "
            + path_text
        )
    )

    subject_title = (
        subject_terms
        & title_terms
    )
    subject_snippet = (
        subject_terms
        & snippet_terms
    )
    subject_url = (
        subject_terms
        & url_terms
    )
    subject_matches = (
        subject_title
        | subject_snippet
        | subject_url
    )

    if subject_terms:
        subject_coverage = (
            len(subject_matches)
            / len(subject_terms)
        )
    else:
        subject_coverage = 1.0

    intent_matches = intent_terms & (
        title_terms
        | snippet_terms
        | url_terms
    )
    focused_matches = focused_terms & (
        title_terms
        | snippet_terms
        | url_terms
    )

    score = 0.0
    score += 5.0 * len(
        subject_title
    )
    score += 2.0 * len(
        subject_snippet
    )
    score += 3.0 * len(
        subject_url
    )
    score += 1.5 * len(
        intent_terms
        & title_terms
    )
    score += 0.75 * len(
        intent_terms
        & snippet_terms
    )
    score += 0.75 * len(
        intent_terms
        & url_terms
    )
    score += 0.25 * len(
        focused_matches
    )

    authority_hints = (
        _authority_hints_for_query(
            original_query
        )
    )
    authoritative = any(
        _domain_matches_hint(
            hostname,
            url,
            hint,
        )
        for hint in authority_hints
    )

    if authoritative:
        score += 8.0

    if hostname.endswith(
        ".gov"
    ):
        score += 4.0
        authoritative = True
    elif hostname.endswith(
        ".edu"
    ):
        score += 2.0

    title_lower = title.lower()
    path_lower = path_text.lower()
    combined_lower = (
        title
        + " "
        + snippet
        + " "
        + path_text
    ).lower()

    technical_query = _is_technical_query(
        original_query
    )

    if technical_query:
        technical_hits = sum(
            1
            for term in TECHNICAL_RELEVANCE_TERMS
            if term in combined_lower
        )
        score += min(
            technical_hits,
            3,
        ) * 1.5

        if any(
            (
                hostname == domain
                or hostname.endswith(
                    "."
                    + domain
                )
            )
            for domain in GENERIC_NEWS_DOMAINS
        ):
            score -= 10.0

    documentation_intent = (
        _query_requests_documentation(
            original_query
        )
    )
    change_intent = (
        _query_requests_change_information(
            original_query
        )
    )
    news_intent = (
        "news"
        in intent_terms
    )

    documentation_supported = (
        not documentation_intent
        or any(
            term in (
                title_lower
                + " "
                + path_lower
                + " "
                + snippet.lower()
            )
            for term in {
                "api",
                "documentation",
                "docs",
                "manual",
                "reference",
            }
        )
    )

    primary_update_evidence = False
    update_evidence_reasons: list[
        str
    ] = []

    if change_intent:
        (
            primary_update_evidence,
            update_evidence_reasons,
        ) = _primary_update_evidence(
            title=title,
            url=url,
        )

        if primary_update_evidence:
            score += 12.0

    news_supported = (
        not news_intent
        or (
            "news"
            in combined_lower
        )
        or bool(
            intent_matches
        )
    )

    technical_primary_required = (
        technical_query
        and change_intent
    )
    primary_requirement_supported = (
        not technical_primary_required
        or (
            authoritative
            and primary_update_evidence
        )
    )

    rejection_reasons: list[
        str
    ] = []

    if not subject_matches:
        rejection_reasons.append(
            "missing_subject"
        )

    if (
        subject_coverage
        < MIN_SUBJECT_COVERAGE
    ):
        rejection_reasons.append(
            "low_subject_coverage"
        )

    if not documentation_supported:
        rejection_reasons.append(
            "missing_documentation_evidence"
        )

    if not news_supported:
        rejection_reasons.append(
            "missing_news_evidence"
        )

    if (
        change_intent
        and not primary_update_evidence
    ):
        rejection_reasons.append(
            "missing_primary_update_evidence"
        )

    if (
        technical_primary_required
        and not authoritative
    ):
        rejection_reasons.append(
            "non_authoritative_update_source"
        )

    if score < MIN_RELEVANCE_SCORE:
        rejection_reasons.append(
            "low_relevance_score"
        )

    accepted = not rejection_reasons

    return {
        "score": round(
            score,
            2,
        ),
        "accepted": accepted,
        "rejection_reasons": (
            rejection_reasons
        ),
        "subject_coverage": round(
            subject_coverage,
            2,
        ),
        "matched_subject_terms": sorted(
            subject_matches
        ),
        "matched_intent_terms": sorted(
            intent_matches
        ),
        "authoritative": authoritative,
        "change_intent": (
            change_intent
        ),
        "primary_update_evidence": (
            primary_update_evidence
        ),
        "update_evidence_reasons": (
            update_evidence_reasons
        ),
    }


def _rank_and_filter_results(
    parsed_results: list[dict[str, str]],
    *,
    validator: Callable[[str], str | None],
    original_query: str,
    focused_query: str,
    max_results: int,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Validate, score, reject weak evidence, rerank, and limit results."""

    candidates: list[
        dict[str, Any]
    ] = []
    seen_urls: set[str] = set()
    raw_result_count = 0
    evaluated_scores: list[
        float
    ] = []
    rejection_reason_counts: dict[
        str,
        int,
    ] = {}

    for parsed_result in parsed_results:
        raw_result_count += 1
        safe_url = validator(
            parsed_result.get(
                "url",
                "",
            )
        )

        if (
            safe_url is None
            or safe_url in seen_urls
        ):
            rejection_reason_counts[
                "invalid_or_duplicate_url"
            ] = (
                rejection_reason_counts.get(
                    "invalid_or_duplicate_url",
                    0,
                )
                + 1
            )
            continue

        seen_urls.add(
            safe_url
        )
        title = _truncate(
            parsed_result.get(
                "title",
                "",
            ),
            MAX_RESULT_TITLE_CHARS,
        )
        snippet = _truncate(
            parsed_result.get(
                "snippet",
                "",
            ),
            MAX_RESULT_SNIPPET_CHARS,
        )
        relevance = _score_search_result(
            original_query=original_query,
            focused_query=focused_query,
            title=title,
            snippet=snippet,
            url=safe_url,
        )
        evaluated_scores.append(
            float(
                relevance[
                    "score"
                ]
            )
        )

        if not relevance[
            "accepted"
        ]:
            for reason in relevance.get(
                "rejection_reasons",
                [],
            ):
                rejection_reason_counts[
                    reason
                ] = (
                    rejection_reason_counts.get(
                        reason,
                        0,
                    )
                    + 1
                )
            continue

        candidates.append(
            {
                "title": title,
                "url": safe_url,
                "snippet": snippet,
                "relevance_score": relevance[
                    "score"
                ],
                "subject_coverage": relevance[
                    "subject_coverage"
                ],
                "matched_subject_terms": relevance[
                    "matched_subject_terms"
                ],
                "matched_intent_terms": relevance[
                    "matched_intent_terms"
                ],
                "authoritative": relevance[
                    "authoritative"
                ],
                "primary_update_evidence": relevance[
                    "primary_update_evidence"
                ],
                "update_evidence_reasons": relevance[
                    "update_evidence_reasons"
                ],
            }
        )

    candidates.sort(
        key=lambda item: (
            bool(
                item[
                    "primary_update_evidence"
                ]
            ),
            bool(
                item[
                    "authoritative"
                ]
            ),
            float(
                item[
                    "relevance_score"
                ]
            ),
            float(
                item[
                    "subject_coverage"
                ]
            ),
        ),
        reverse=True,
    )

    results = candidates[
        :max_results
    ]

    for rank, result in enumerate(
        results,
        start=1,
    ):
        result["rank"] = rank

    quality = {
        "raw_result_count": (
            raw_result_count
        ),
        "accepted_result_count": len(
            results
        ),
        "rejected_result_count": (
            raw_result_count
            - len(
                results
            )
        ),
        "top_relevance_score": (
            max(
                evaluated_scores
            )
            if evaluated_scores
            else None
        ),
        "primary_update_result_count": sum(
            1
            for result in results
            if result.get(
                "primary_update_evidence"
            )
        ),
        "rejection_reasons": (
            rejection_reason_counts
        ),
    }

    return (
        results,
        quality,
    )


SUPPORTED_CONTENT_ENCODINGS = {
    "",
    "identity",
    "gzip",
    "x-gzip",
    "deflate",
}


def _bounded_zlib_decompress(
    compressed_body: bytes,
    *,
    wbits: int,
    encoding_name: str,
) -> bytes:
    """Incrementally decompress under compressed and expanded byte limits."""

    if (
        len(
            compressed_body
        )
        > MAX_COMPRESSED_RESPONSE_BYTES
    ):
        raise InternetError(
            "The compressed response exceeded the "
            f"{MAX_COMPRESSED_RESPONSE_BYTES}-byte limit."
        )

    decompressor = zlib.decompressobj(
        wbits
    )
    output_parts: list[bytes] = []
    output_size = 0

    try:
        for start in range(
            0,
            len(
                compressed_body
            ),
            DECOMPRESSION_CHUNK_BYTES,
        ):
            pending = compressed_body[
                start:
                start
                + DECOMPRESSION_CHUNK_BYTES
            ]

            while pending:
                remaining = (
                    MAX_RESPONSE_BYTES
                    - output_size
                    + 1
                )
                expanded = decompressor.decompress(
                    pending,
                    remaining,
                )
                output_parts.append(
                    expanded
                )
                output_size += len(
                    expanded
                )

                if output_size > MAX_RESPONSE_BYTES:
                    raise InternetError(
                        "The decompressed response exceeded the "
                        f"{MAX_RESPONSE_BYTES}-byte limit."
                    )

                pending = (
                    decompressor.unconsumed_tail
                )

        remaining = (
            MAX_RESPONSE_BYTES
            - output_size
            + 1
        )
        expanded = decompressor.flush(
            remaining
        )
        output_parts.append(
            expanded
        )
        output_size += len(
            expanded
        )
    except zlib.error as error:
        raise InternetError(
            f"The server returned invalid {encoding_name} compression."
        ) from error

    if output_size > MAX_RESPONSE_BYTES:
        raise InternetError(
            "The decompressed response exceeded the "
            f"{MAX_RESPONSE_BYTES}-byte limit."
        )

    if not decompressor.eof:
        raise InternetError(
            f"The server returned incomplete {encoding_name} compression."
        )

    if decompressor.unused_data:
        raise InternetError(
            f"The server returned unsupported trailing {encoding_name} data."
        )

    return b"".join(
        output_parts
    )


def decode_bounded_content_encoding(
    body: bytes,
    content_encoding: str,
) -> bytes:
    """Decode one supported HTTP content encoding under strict size limits."""

    normalized = (
        content_encoding
        .strip()
        .lower()
    )

    if normalized in {
        "",
        "identity",
    }:
        if len(body) > MAX_RESPONSE_BYTES:
            raise InternetError(
                "The response exceeded the "
                f"{MAX_RESPONSE_BYTES}-byte limit."
            )

        return body

    if "," in normalized:
        raise InternetError(
            "Multiple HTTP content encodings are not supported."
        )

    if normalized in {
        "gzip",
        "x-gzip",
    }:
        return _bounded_zlib_decompress(
            body,
            wbits=(
                zlib.MAX_WBITS
                | 16
            ),
            encoding_name="gzip",
        )

    if normalized == "deflate":
        try:
            return _bounded_zlib_decompress(
                body,
                wbits=zlib.MAX_WBITS,
                encoding_name="deflate",
            )
        except InternetError as wrapped_error:
            try:
                return _bounded_zlib_decompress(
                    body,
                    wbits=(
                        -zlib.MAX_WBITS
                    ),
                    encoding_name="deflate",
                )
            except InternetError:
                raise wrapped_error

    raise InternetError(
        "Unsupported HTTP content encoding "
        f"{normalized!r}. Supported encodings are identity, gzip, and "
        "deflate."
    )



class InternetManager:
    """
    Persistent opt-in read-only internet access for Elise.

    This manager exposes no uploads, POST requests to arbitrary sites, cookies,
    authentication, JavaScript execution, downloads to disk, or local-network
    access.
    """

    def __init__(
        self,
        settings_path: str | Path,
    ) -> None:
        self.settings_path = Path(
            settings_path
        )
        self.settings_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._enabled = False
        self._settings_warning: str | None = None
        self._load_settings()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _load_settings(self) -> None:
        if not self.settings_path.exists():
            self._enabled = False
            return

        try:
            payload = json.loads(
                self.settings_path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            self._enabled = False
            self._settings_warning = (
                "Internet settings could not be read; "
                f"defaulting to disabled: {error}"
            )
            return

        if not isinstance(
            payload,
            dict,
        ):
            self._enabled = False
            self._settings_warning = (
                "Internet settings were malformed; "
                "defaulting to disabled."
            )
            return

        self._enabled = bool(
            payload.get(
                "enabled",
                False,
            )
        )

    def set_enabled(
        self,
        enabled: bool,
    ) -> dict[str, Any]:
        """Persistently enable or disable internet tools."""

        if not isinstance(
            enabled,
            bool,
        ):
            raise ValueError(
                "enabled must be true or false."
            )

        payload = {
            "version": SETTINGS_VERSION,
            "enabled": enabled,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(
                timespec="seconds"
            ),
        }

        temporary_path: Path | None = None

        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".internet_settings.",
                suffix=".tmp",
                dir=self.settings_path.parent,
            )
            temporary_path = Path(
                temporary_name
            )

            with open(
                descriptor,
                "w",
                encoding="utf-8",
                closefd=True,
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write(
                    "\n"
                )
                handle.flush()

            temporary_path.replace(
                self.settings_path
            )
            temporary_path = None

        except OSError as error:
            raise InternetError(
                f"Unable to save internet settings: {error}"
            ) from error
        finally:
            if (
                temporary_path is not None
                and temporary_path.exists()
            ):
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

        self._enabled = enabled
        self._settings_warning = None

        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "provider": SEARCH_PROVIDER_NAME,
            "settings_path": str(
                self.settings_path
            ),
            "settings_warning": (
                self._settings_warning
            ),
            "privacy_notice": (
                "When enabled, search terms and requested URLs are sent "
                "to external internet services."
            ),
            "offline_behavior": (
                "Network failures return a clear error while local chat, "
                "memory, documents, and filesystem tools remain available."
            ),
        }

    @staticmethod
    def _validate_result_url_syntax(
        raw_url: str,
    ) -> str | None:
        """Filter obviously unsafe search-result URLs without DNS lookups."""

        try:
            parsed = urlsplit(
                raw_url.strip()
            )
            port = parsed.port
        except ValueError:
            return None

        scheme = parsed.scheme.lower()

        if scheme not in {
            "http",
            "https",
        }:
            return None

        if (
            parsed.username is not None
            or parsed.password is not None
        ):
            return None

        hostname = (
            parsed.hostname
            or ""
        ).rstrip(".").lower()

        if not hostname:
            return None

        if (
            hostname in BLOCKED_HOSTNAMES
            or any(
                hostname.endswith(
                    suffix
                )
                for suffix
                in BLOCKED_HOST_SUFFIXES
            )
        ):
            return None

        if "." not in hostname:
            try:
                ipaddress.ip_address(
                    hostname
                )
            except ValueError:
                return None

        if port is not None:
            allowed_port = (
                port == 80
                if scheme == "http"
                else port == 443
            )

            if not allowed_port:
                return None

        try:
            literal_address = ipaddress.ip_address(
                hostname
            )
        except ValueError:
            literal_address = None

        if (
            literal_address is not None
            and not literal_address.is_global
        ):
            return None

        return urlunsplit(
            (
                scheme,
                parsed.netloc,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    @staticmethod
    def _validate_public_url(
        raw_url: str,
    ) -> str:
        """
        Validate an HTTP(S) URL and reject local/private network destinations.

        DNS is resolved before every initial request and redirect. If any
        resolved address is non-public, the request is blocked.
        """

        cleaned = raw_url.strip()

        if not cleaned:
            raise InternetError(
                "A URL is required."
            )

        try:
            parsed = urlsplit(
                cleaned
            )
            port = parsed.port
        except ValueError as error:
            raise InternetError(
                f"Invalid URL: {error}"
            ) from error

        scheme = parsed.scheme.lower()

        if scheme not in {
            "http",
            "https",
        }:
            raise InternetError(
                "Only http and https URLs are allowed."
            )

        if (
            parsed.username is not None
            or parsed.password is not None
        ):
            raise InternetError(
                "URLs containing usernames or passwords are not allowed."
            )

        hostname = (
            parsed.hostname
            or ""
        ).rstrip(".").lower()

        if not hostname:
            raise InternetError(
                "The URL must contain a hostname."
            )

        if (
            hostname in BLOCKED_HOSTNAMES
            or any(
                hostname.endswith(
                    suffix
                )
                for suffix
                in BLOCKED_HOST_SUFFIXES
            )
        ):
            raise InternetError(
                "Local, internal, and special-use hostnames are blocked."
            )

        if "." not in hostname:
            try:
                ipaddress.ip_address(
                    hostname
                )
            except ValueError as error:
                raise InternetError(
                    "Single-label and intranet-style hostnames are blocked."
                ) from error

        expected_port = (
            80
            if scheme == "http"
            else 443
        )

        if (
            port is not None
            and port != expected_port
        ):
            raise InternetError(
                "Only the standard HTTP and HTTPS ports are allowed."
            )

        try:
            ascii_hostname = hostname.encode(
                "idna"
            ).decode(
                "ascii"
            )
        except UnicodeError as error:
            raise InternetError(
                "The URL hostname is invalid."
            ) from error

        lookup_port = (
            port
            or expected_port
        )

        try:
            address_info = socket.getaddrinfo(
                ascii_hostname,
                lookup_port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            raise InternetError(
                f"Unable to resolve the hostname: {error}"
            ) from error

        addresses = {
            entry[4][0].split(
                "%",
                1,
            )[0]
            for entry in address_info
        }

        if not addresses:
            raise InternetError(
                "The hostname did not resolve to an address."
            )

        for address_text in addresses:
            try:
                address = ipaddress.ip_address(
                    address_text
                )
            except ValueError as error:
                raise InternetError(
                    "The hostname resolved to an invalid address."
                ) from error

            if not address.is_global:
                raise InternetError(
                    "The URL resolves to a private, local, reserved, "
                    "or otherwise non-public address."
                )

        if ":" in ascii_hostname:
            display_hostname = (
                f"[{ascii_hostname}]"
            )
        else:
            display_hostname = (
                ascii_hostname
            )

        netloc = display_hostname

        if (
            port is not None
            and port != expected_port
        ):
            netloc += (
                f":{port}"
            )

        return urlunsplit(
            (
                scheme,
                netloc,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    def _open_public_url(
        self,
        url: str,
        *,
        accept: str,
        timeout_seconds: int = (
            DEFAULT_TIMEOUT_SECONDS
        ),
    ) -> dict[str, Any]:
        """Open one validated public URL and read a bounded response."""

        if (
            isinstance(
                timeout_seconds,
                bool,
            )
            or not isinstance(
                timeout_seconds,
                int,
            )
            or not 1
            <= timeout_seconds
            <= MAX_TIMEOUT_SECONDS
        ):
            raise InternetError(
                f"timeout_seconds must be between 1 and "
                f"{MAX_TIMEOUT_SECONDS}."
            )

        validated_url = (
            self._validate_public_url(
                url
            )
        )

        opener = build_opener(
            ProxyHandler({}),
            _SafeRedirectHandler(
                self._validate_public_url
            ),
        )

        request = Request(
            validated_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
            method="GET",
        )

        try:
            with opener.open(
                request,
                timeout=timeout_seconds,
            ) as response:
                final_url = (
                    self._validate_public_url(
                        response.geturl()
                    )
                )
                status = int(
                    getattr(
                        response,
                        "status",
                        200,
                    )
                )
                content_length = (
                    response.headers.get(
                        "Content-Length"
                    )
                )

                if content_length:
                    try:
                        declared_size = int(
                            content_length
                        )
                    except ValueError:
                        declared_size = None

                    if (
                        declared_size is not None
                        and declared_size
                        > MAX_RESPONSE_BYTES
                    ):
                        raise InternetError(
                            "The response is larger than the "
                            f"{MAX_RESPONSE_BYTES}-byte limit."
                        )

                content_encoding = (
                    response.headers.get(
                        "Content-Encoding",
                        "",
                    )
                    .strip()
                    .lower()
                )

                if (
                    content_encoding
                    not in SUPPORTED_CONTENT_ENCODINGS
                ):
                    raise InternetError(
                        "Unsupported HTTP content encoding "
                        f"{content_encoding!r}. Supported encodings are "
                        "identity, gzip, and deflate."
                    )

                raw_read_limit = (
                    MAX_RESPONSE_BYTES
                    if content_encoding
                    in {
                        "",
                        "identity",
                    }
                    else MAX_COMPRESSED_RESPONSE_BYTES
                )
                compressed_body = response.read(
                    raw_read_limit
                    + 1
                )

                if (
                    len(
                        compressed_body
                    )
                    > raw_read_limit
                ):
                    limit_name = (
                        "response"
                        if content_encoding
                        in {
                            "",
                            "identity",
                        }
                        else "compressed response"
                    )
                    raise InternetError(
                        f"The {limit_name} exceeded the "
                        f"{raw_read_limit}-byte limit."
                    )

                body = decode_bounded_content_encoding(
                    compressed_body,
                    content_encoding,
                )

                content_type = (
                    response.headers.get_content_type()
                )
                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

        except HTTPError as error:
            # HTTPError is also a response object. Read only a bounded body and
            # retain only Google's safe machine-readable reason.
            try:
                error_body = error.read(MAX_PROVIDER_ERROR_BYTES + 1)
            except (OSError, ValueError):
                error_body = b""
            if len(error_body) > MAX_PROVIDER_ERROR_BYTES:
                error_body = b""
            charset = error.headers.get_content_charset() if error.headers is not None else None
            raise ProviderHTTPError(
                int(error.code),
                _google_error_reason(error_body, charset or "utf-8"),
            ) from error
        except URLError as error:
            reason = getattr(
                error,
                "reason",
                error,
            )
            raise InternetError(
                f"Network request failed: {reason}"
            ) from error
        except (
            TimeoutError,
            socket.timeout,
        ) as error:
            raise InternetError(
                "The network request timed out."
            ) from error
        except OSError as error:
            raise InternetError(
                f"Network request failed: {error}"
            ) from error

        return {
            "body": body,
            "final_url": final_url,
            "status": status,
            "content_type": content_type,
            "charset": charset,
            "size_bytes": len(
                body
            ),
            "wire_size_bytes": len(
                compressed_body
            ),
            "content_encoding": (
                content_encoding
                or "identity"
            ),
            "fetched_at": datetime.now(
                timezone.utc
            ).isoformat(
                timespec="seconds"
            ),
        }

    @staticmethod
    def _decode_body(
        body: bytes,
        charset: str,
    ) -> str:
        try:
            return body.decode(
                charset,
                errors="replace",
            )
        except LookupError:
            return body.decode(
                "utf-8",
                errors="replace",
            )

    def search_web(
        self,
        query: str,
        *,
        max_results: int = (
            DEFAULT_SEARCH_RESULTS
        ),
    ) -> dict[str, Any]:
        """
        Search the public web with focused queries and local relevance checks.

        Provider output is not considered successful until at least one result
        passes subject coverage, intent, authority, and relevance thresholds.
        """

        if not self._enabled:
            return {
                "success": False,
                "tool": "search_web",
                "internet_enabled": False,
                "offline": True,
                "error": (
                    "Internet access is disabled. "
                    "Use `/internet on` to enable it."
                ),
            }

        if not isinstance(
            query,
            str,
        ):
            return {
                "success": False,
                "tool": "search_web",
                "error": (
                    "query must be a string."
                ),
            }

        original_query = (
            query.strip()
        )

        if not original_query:
            return {
                "success": False,
                "tool": "search_web",
                "error": (
                    "A nonempty search query is required."
                ),
            }

        if len(original_query) > MAX_QUERY_CHARS:
            return {
                "success": False,
                "tool": "search_web",
                "error": (
                    f"The search query exceeds the "
                    f"{MAX_QUERY_CHARS}-character limit."
                ),
            }

        if (
            isinstance(
                max_results,
                bool,
            )
            or not isinstance(
                max_results,
                int,
            )
            or not 1
            <= max_results
            <= MAX_SEARCH_RESULTS
        ):
            return {
                "success": False,
                "tool": "search_web",
                "error": (
                    f"max_results must be between 1 and "
                    f"{MAX_SEARCH_RESULTS}."
                ),
            }

        query_variants = (
            build_search_query_variants(
                original_query
            )
        )
        focused_query = (
            focus_search_query(
                original_query
            )
        )

        provider_specs = [
            {
                "name": "Bing RSS",
                "endpoint": (
                    BING_RSS_ENDPOINT
                ),
                "accept": (
                    "application/rss+xml,application/xml,"
                    "text/xml;q=0.9,*/*;q=0.1"
                ),
                "parser": "rss",
            },
            {
                "name": "DuckDuckGo HTML",
                "endpoint": (
                    DUCKDUCKGO_HTML_ENDPOINT
                ),
                "accept": (
                    "text/html,application/xhtml+xml"
                ),
                "parser": "duckduckgo",
            },
            {
                "name": "DuckDuckGo Lite",
                "endpoint": (
                    DUCKDUCKGO_LITE_ENDPOINT
                ),
                "accept": (
                    "text/html,application/xhtml+xml"
                ),
                "parser": "duckduckgo",
            },
        ]

        provider_attempts: list[
            dict[str, str]
        ] = []

        for query_index, provider_query in enumerate(
            query_variants
        ):
            selected_specs = (
                provider_specs[:1]
                if query_index
                < len(
                    query_variants
                )
                - 1
                else provider_specs
            )

            for spec in selected_specs:
                if spec["parser"] == "rss":
                    request_url = (
                        str(
                            spec[
                                "endpoint"
                            ]
                        )
                        + "?"
                        + urlencode(
                            {
                                "q": (
                                    provider_query
                                ),
                                "format": "rss",
                            }
                        )
                    )
                else:
                    request_url = (
                        str(
                            spec[
                                "endpoint"
                            ]
                        )
                        + "?"
                        + urlencode(
                            {
                                "q": (
                                    provider_query
                                ),
                            }
                        )
                    )

                provider_attempts.append(
                    {
                        "name": str(
                            spec["name"]
                        ),
                        "query": (
                            provider_query
                        ),
                        "url": request_url,
                        "accept": str(
                            spec["accept"]
                        ),
                        "parser": str(
                            spec["parser"]
                        ),
                    }
                )

        errors: list[str] = []
        any_network_response = False
        rejected_sets = 0
        aggregate_raw_results = 0
        aggregate_rejected_results = 0
        aggregate_top_score: float | None = None
        aggregate_rejection_reasons: dict[
            str,
            int,
        ] = {}
        attempts_evaluated = 0

        for attempt in provider_attempts:
            provider_name = str(
                attempt["name"]
            )
            provider_query = str(
                attempt["query"]
            )

            try:
                response = self._open_public_url(
                    str(
                        attempt["url"]
                    ),
                    accept=str(
                        attempt["accept"]
                    ),
                )
                any_network_response = True
            except InternetError as error:
                errors.append(
                    f"{provider_name} [{provider_query}]: {error}"
                )
                continue

            decoded = self._decode_body(
                response["body"],
                response["charset"],
            )

            try:
                if attempt["parser"] == "rss":
                    parsed_results = (
                        _parse_bing_rss_results(
                            decoded
                        )
                    )
                else:
                    parser = (
                        _DuckDuckGoHTMLParser()
                    )
                    parser.feed(
                        decoded
                    )
                    parser.close()
                    parsed_results = (
                        parser.results
                    )
            except Exception as error:
                errors.append(
                    f"{provider_name} [{provider_query}]: "
                    f"parse error: {error}"
                )
                continue

            (
                results,
                quality,
            ) = _rank_and_filter_results(
                parsed_results,
                validator=(
                    self._validate_result_url_syntax
                ),
                original_query=(
                    original_query
                ),
                focused_query=(
                    focused_query
                ),
                max_results=max_results,
            )

            attempts_evaluated += 1
            aggregate_raw_results += int(
                quality.get(
                    "raw_result_count",
                    0,
                )
            )
            aggregate_rejected_results += int(
                quality.get(
                    "rejected_result_count",
                    0,
                )
            )
            attempt_top_score = quality.get(
                "top_relevance_score"
            )

            if attempt_top_score is not None:
                aggregate_top_score = (
                    float(
                        attempt_top_score
                    )
                    if aggregate_top_score is None
                    else max(
                        aggregate_top_score,
                        float(
                            attempt_top_score
                        ),
                    )
                )

            for reason, count in quality.get(
                "rejection_reasons",
                {},
            ).items():
                aggregate_rejection_reasons[
                    str(
                        reason
                    )
                ] = (
                    aggregate_rejection_reasons.get(
                        str(
                            reason
                        ),
                        0,
                    )
                    + int(
                        count
                    )
                )

            if not results:
                rejected_sets += 1
                errors.append(
                    f"{provider_name} [{provider_query}]: "
                    f"{quality['raw_result_count']} results were "
                    "off-topic or below the relevance threshold"
                )
                continue

            return {
                "success": True,
                "tool": "search_web",
                "internet_enabled": True,
                "offline": False,
                "provider": provider_name,
                "provider_query": (
                    provider_query
                ),
                "provider_chain": [
                    (
                        f"{item['name']} "
                        f"[{item['query']}]"
                    )
                    for item in provider_attempts
                ],
                "query": original_query,
                "focused_query": (
                    focused_query
                ),
                "query_variants": (
                    query_variants
                ),
                "result_count": len(
                    results
                ),
                "results": results,
                "quality": {
                    "raw_result_count": (
                        aggregate_raw_results
                    ),
                    "accepted_result_count": len(
                        results
                    ),
                    "rejected_result_count": (
                        aggregate_rejected_results
                    ),
                    "top_relevance_score": (
                        aggregate_top_score
                    ),
                    "primary_update_result_count": sum(
                        1
                        for item in results
                        if item.get(
                            "primary_update_evidence"
                        )
                    ),
                    "rejection_reasons": (
                        aggregate_rejection_reasons
                    ),
                    "attempts_evaluated": (
                        attempts_evaluated
                    ),
                },
                "fetched_at": response[
                    "fetched_at"
                ],
            }

        failure_detail = "; ".join(
            errors
        )

        if (
            any_network_response
            and rejected_sets
        ):
            failure_prefix = (
                "Search providers responded, but all result sets were "
                "off-topic or too weak to support the question. "
            )
        else:
            failure_prefix = (
                "All configured search providers failed. "
            )

        return {
            "success": False,
            "tool": "search_web",
            "internet_enabled": True,
            "offline": (
                not any_network_response
            ),
            "provider": SEARCH_PROVIDER_NAME,
            "provider_chain": [
                (
                    f"{item['name']} "
                    f"[{item['query']}]"
                )
                for item in provider_attempts
            ],
            "query": original_query,
            "focused_query": (
                focused_query
            ),
            "query_variants": (
                query_variants
            ),
            "quality": {
                "raw_result_count": (
                    aggregate_raw_results
                ),
                "accepted_result_count": 0,
                "rejected_result_count": (
                    aggregate_rejected_results
                ),
                "top_relevance_score": (
                    aggregate_top_score
                ),
                "primary_update_result_count": 0,
                "rejection_reasons": (
                    aggregate_rejection_reasons
                ),
                "attempts_evaluated": (
                    attempts_evaluated
                ),
            },
            "error": (
                failure_prefix
                + (
                    failure_detail
                    if failure_detail
                    else "No provider returned usable results."
                )
            ),
        }

    def fetch_web_page(
        self,
        url: str,
        *,
        max_chars: int = (
            DEFAULT_PAGE_CHARS
        ),
    ) -> dict[str, Any]:
        """Fetch bounded readable text from one public HTTP(S) page."""

        if not self._enabled:
            return {
                "success": False,
                "tool": "fetch_web_page",
                "internet_enabled": False,
                "offline": True,
                "error": (
                    "Internet access is disabled. "
                    "Use `/internet on` to enable it."
                ),
            }

        if not isinstance(
            url,
            str,
        ):
            return {
                "success": False,
                "tool": "fetch_web_page",
                "error": (
                    "url must be a string."
                ),
            }

        if (
            isinstance(
                max_chars,
                bool,
            )
            or not isinstance(
                max_chars,
                int,
            )
            or not 1_000
            <= max_chars
            <= MAX_PAGE_CHARS
        ):
            return {
                "success": False,
                "tool": "fetch_web_page",
                "error": (
                    f"max_chars must be between 1000 and "
                    f"{MAX_PAGE_CHARS}."
                ),
            }

        try:
            response = self._open_public_url(
                url,
                accept=(
                    "text/html,application/xhtml+xml,"
                    "text/plain,application/json"
                ),
            )
        except InternetError as error:
            return {
                "success": False,
                "tool": "fetch_web_page",
                "internet_enabled": True,
                "offline": True,
                "source_url": url.strip(),
                "error": str(error),
            }

        content_type = response[
            "content_type"
        ]

        if content_type not in ALLOWED_CONTENT_TYPES:
            return {
                "success": False,
                "tool": "fetch_web_page",
                "internet_enabled": True,
                "offline": False,
                "source_url": url.strip(),
                "final_url": response[
                    "final_url"
                ],
                "error": (
                    "Unsupported content type: "
                    f"{content_type}. This initial version supports "
                    "HTML, plain text, and JSON only."
                ),
            }

        decoded = self._decode_body(
            response["body"],
            response["charset"],
        )
        title = ""
        extracted_links: list[
            dict[str, str]
        ] = []

        if content_type in {
            "text/html",
            "application/xhtml+xml",
        }:
            parser = _ReadableHTMLParser()

            try:
                parser.feed(
                    decoded
                )
                parser.close()
            except Exception as error:
                return {
                    "success": False,
                    "tool": "fetch_web_page",
                    "internet_enabled": True,
                    "offline": False,
                    "source_url": url.strip(),
                    "final_url": response[
                        "final_url"
                    ],
                    "error": (
                        f"Unable to parse the HTML page: {error}"
                    ),
                }

            title = parser.get_title()
            readable_text = parser.get_text()

            for link in parser.get_links(
                response[
                    "final_url"
                ]
            ):
                safe_url = (
                    self._validate_result_url_syntax(
                        link.get(
                            "url",
                            "",
                        )
                    )
                )

                if safe_url is None:
                    continue

                extracted_links.append(
                    {
                        "text": _truncate(
                            str(
                                link.get(
                                    "text",
                                    "",
                                )
                            ),
                            MAX_EXTRACTED_LINK_TEXT_CHARS,
                        ),
                        "url": safe_url,
                    }
                )

                if (
                    len(
                        extracted_links
                    )
                    >= MAX_EXTRACTED_PAGE_LINKS
                ):
                    break
        else:
            readable_text = (
                _normalize_multiline_text(
                    decoded
                )
            )

        if not readable_text:
            return {
                "success": False,
                "tool": "fetch_web_page",
                "internet_enabled": True,
                "offline": False,
                "source_url": url.strip(),
                "final_url": response[
                    "final_url"
                ],
                "error": (
                    "The page did not contain readable text."
                ),
            }

        truncated = (
            len(readable_text)
            > max_chars
        )
        returned_text = (
            readable_text[:max_chars]
        )

        return {
            "success": True,
            "tool": "fetch_web_page",
            "internet_enabled": True,
            "offline": False,
            "source_url": url.strip(),
            "final_url": response[
                "final_url"
            ],
            "title": (
                title
                or response[
                    "final_url"
                ]
            ),
            "content_type": content_type,
            "status": response[
                "status"
            ],
            "size_bytes": response[
                "size_bytes"
            ],
            "returned_chars": len(
                returned_text
            ),
            "truncated": truncated,
            "text": returned_text,
            "link_count": len(
                extracted_links
            ),
            "links": extracted_links,
            "fetched_at": response[
                "fetched_at"
            ],
        }

    def fetch_public_json(
        self,
        url: str,
    ) -> dict[str, Any]:
        """Fetch bounded public JSON for a deterministic host provider.

        This method is deliberately not a model tool. Its response never
        echoes the request URL, which can contain a provider credential.
        """

        if not self._enabled:
            return {
                "success": False,
                "error_code": "internet_disabled",
                "error": "Internet access is disabled. Use `/internet on` to enable it.",
            }

        try:
            response = self._open_public_url(
                url,
                accept="application/json,text/json;q=0.9,*/*;q=0.1",
            )
        except ProviderHTTPError as error:
            return _provider_failure(error.status, error.reason)
        except InternetError as error:
            return {
                "success": False,
                "error_code": "network_error",
                "error": str(error),
            }

        if str(response["content_type"]).lower() not in {"application/json", "text/json"}:
            return {
                "success": False,
                "error_code": "unsupported_content_type",
                "error": "The provider did not return JSON.",
            }
        try:
            payload = json.loads(
                self._decode_body(response["body"], response["charset"])
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return {
                "success": False,
                "error_code": "invalid_json",
                "error": "The provider returned invalid JSON.",
            }
        if not isinstance(payload, dict):
            return {
                "success": False,
                "error_code": "invalid_json",
                "error": "The provider returned an unexpected JSON payload.",
            }
        return {"success": True, "data": payload}
