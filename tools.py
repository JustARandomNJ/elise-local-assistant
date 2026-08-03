from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any

from internet import (
    DEFAULT_PAGE_CHARS,
    DEFAULT_SEARCH_RESULTS,
    MAX_PAGE_CHARS,
    MAX_SEARCH_RESULTS,
    InternetManager,
)


ALLOWED_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".json",
    ".csv",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".log",
}

MAX_READ_BYTES = 200_000
DEFAULT_READ_CHARS = 6_000
MAX_RETURNED_CHARS = 20_000
MAX_LIST_RESULTS = 200
MAX_WRITE_CHARS = 8_000
MAX_WRITE_BYTES = 32_000
MAX_EDIT_TEXT_CHARS = 8_000
EDIT_CONTEXT_CHARS = 160

IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
}


TOOL_POLICIES: dict[str, dict[str, Any]] = {
    "search_web": {
        "description": (
            "Search the public web through the configured read-only search "
            "provider. Available only after persistent internet opt-in."
        ),
        "access_mode": "network_read",
        "risk_level": "medium",
        "permission_mode": "automatic",
        "requires_confirmation": False,
    },
    "fetch_web_page": {
        "description": (
            "Fetch bounded readable text from one public HTTP(S) page. "
            "Local/private destinations and unsafe URL forms are blocked."
        ),
        "access_mode": "network_read",
        "risk_level": "medium",
        "permission_mode": "automatic",
        "requires_confirmation": False,
    },
    "get_current_time": {
        "description": (
            "Return the host computer's current local date, "
            "time, and timezone."
        ),
        "access_mode": "read",
        "risk_level": "low",
        "permission_mode": "automatic",
        "requires_confirmation": False,
    },
    "list_files": {
        "description": (
            "List files inside the approved project or documents "
            "directory without modifying anything."
        ),
        "access_mode": "read",
        "risk_level": "low",
        "permission_mode": "automatic",
        "requires_confirmation": False,
    },
    "read_text_file": {
        "description": (
            "Read a supported text file from an approved directory. "
            "The file must be below the configured size limit."
        ),
        "access_mode": "read",
        "risk_level": "low",
        "permission_mode": "automatic",
        "requires_confirmation": False,
    },
    "create_text_file": {
        "description": (
            "Create a new supported text file inside an approved root. "
            "The tool refuses to overwrite an existing path."
        ),
        "access_mode": "write",
        "risk_level": "medium",
        "permission_mode": "confirmation",
        "requires_confirmation": True,
    },
    "replace_text_file": {
        "description": (
            "Atomically replace the full contents of an existing supported "
            "text file inside an approved root."
        ),
        "access_mode": "write",
        "risk_level": "high",
        "permission_mode": "confirmation",
        "requires_confirmation": True,
    },
    "replace_text_occurrence": {
        "description": (
            "Replace one exact, case-sensitive text occurrence inside an "
            "existing supported text file. The operation refuses zero or "
            "multiple matches."
        ),
        "access_mode": "write",
        "risk_level": "medium",
        "permission_mode": "confirmation",
        "requires_confirmation": True,
    },
    "append_text_file": {
        "description": (
            "Append text to an existing supported text file inside an "
            "approved root."
        ),
        "access_mode": "write",
        "risk_level": "medium",
        "permission_mode": "confirmation",
        "requires_confirmation": True,
    },
    "create_directory": {
        "description": (
            "Create one new directory inside an approved root. "
            "Parent directories must already exist."
        ),
        "access_mode": "write",
        "risk_level": "medium",
        "permission_mode": "confirmation",
        "requires_confirmation": True,
    },
}


class ToolError(Exception):
    """Raised when a tool request is invalid or unsafe."""


class ToolManager:
    """
    Strict allowlist for Elise's local tools.

    Read operations may run automatically. Write operations are exposed only
    through confirmation-gated policies enforced by the host application.
    This class intentionally provides no deletion, shell execution, process
    control, uploads, authenticated sessions, or arbitrary path/network access.
    Optional web reads are delegated to InternetManager after persistent opt-in.
    """

    def __init__(
        self,
        project_directory: str | Path,
        documents_directory: str | Path,
        internet_manager: InternetManager | None = None,
    ) -> None:
        self.project_directory = Path(
            project_directory
        ).resolve()

        self.documents_directory = Path(
            documents_directory
        ).resolve()

        self.allowed_roots = {
            "project": self.project_directory,
            "documents": self.documents_directory,
        }

        self.internet_manager = internet_manager

    def available_tools(self) -> dict[str, str]:
        """Return only tools currently exposed to Elise."""

        internet_enabled = (
            self.internet_manager is not None
            and self.internet_manager.is_enabled
        )

        return {
            name: str(policy["description"])
            for name, policy
            in TOOL_POLICIES.items()
            if (
                policy.get(
                    "access_mode"
                )
                != "network_read"
                or internet_enabled
            )
        }

    @staticmethod
    def tool_policies() -> dict[str, dict[str, Any]]:
        """Return a copy of every tool's permission policy."""

        return {
            name: dict(policy)
            for name, policy
            in TOOL_POLICIES.items()
        }

    @staticmethod
    def get_tool_policy(
        tool_name: str,
    ) -> dict[str, Any]:
        """
        Return one tool's policy.

        Unknown tools receive a fail-closed policy so future code cannot treat
        an unregistered capability as automatically approved.
        """

        policy = TOOL_POLICIES.get(
            tool_name.strip()
        )

        if policy is None:
            return {
                "description": (
                    "Unknown or unregistered tool."
                ),
                "access_mode": "unknown",
                "risk_level": "high",
                "permission_mode": "blocked",
                "requires_confirmation": True,
            }

        return dict(policy)

    def ollama_tool_schemas(self) -> list[dict[str, Any]]:
        """
        Return JSON schemas for Ollama's native tool-calling interface.

        ToolManager.execute() remains the final authority and validates every
        model-provided argument before any operation runs. The host application
        separately requires explicit confirmation before write tools execute.
        """

        root_property = {
            "type": "string",
            "enum": [
                "project",
                "documents",
            ],
            "description": (
                "The approved root containing the target. Translate "
                "'project root' to 'project' and 'documents folder' "
                "to 'documents'."
            ),
        }

        path_property = {
            "type": "string",
            "description": (
                "An exact relative path inside the approved root. "
                "Never convert it to an absolute path."
            ),
        }

        content_property = {
            "type": "string",
            "maxLength": MAX_WRITE_CHARS,
            "description": (
                "The exact UTF-8 text to write. The host will display the "
                "full content and require explicit user confirmation."
            ),
        }

        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "get_current_time",
                    "description": (
                        "Get the host computer's current local date, time, "
                        "UTC offset, and timezone. Use only when the user "
                        "asks for the current date or time."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": (
                        "List files and directories inside the approved "
                        "Elise project or documents root."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": {
                                "type": "string",
                                "description": (
                                    "A relative directory path inside the "
                                    "approved root. Use '.' for the root."
                                ),
                                "default": ".",
                            },
                            "recursive": {
                                "type": "boolean",
                                "description": (
                                    "Whether to include nested files and "
                                    "directories."
                                ),
                                "default": False,
                            },
                        },
                        "required": [
                            "root",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_text_file",
                    "description": (
                        "Read one supported text file inside an approved root. "
                        "If a supplied relative path looks invalid, still "
                        "request it exactly and let the host validator decide."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                            "max_chars": {
                                "type": "integer",
                                "minimum": 1000,
                                "maximum": MAX_RETURNED_CHARS,
                                "description": (
                                    "Maximum number of text characters to "
                                    "return."
                                ),
                                "default": DEFAULT_READ_CHARS,
                            },
                        },
                        "required": [
                            "root",
                            "path",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_text_file",
                    "description": (
                        "Create a new text file. Use only when the user asks "
                        "to create or save a new file and supplies or requests "
                        "the exact content. This never overwrites a file."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                            "content": content_property,
                        },
                        "required": [
                            "root",
                            "path",
                            "content",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "replace_text_file",
                    "description": (
                        "Replace the complete contents of an existing text "
                        "file. Use only when the user clearly requests full "
                        "replacement and the exact new content is known."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                            "content": content_property,
                        },
                        "required": [
                            "root",
                            "path",
                            "content",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "replace_text_occurrence",
                    "description": (
                        "Replace exactly one case-sensitive occurrence of old "
                        "text inside an existing text file. Use for targeted "
                        "edits only. The host refuses zero or multiple matches, "
                        "shows before/after context, and requires confirmation."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                            "old_text": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_EDIT_TEXT_CHARS,
                                "description": (
                                    "The exact case-sensitive text currently "
                                    "present in the file. It must occur once."
                                ),
                            },
                            "new_text": {
                                "type": "string",
                                "maxLength": MAX_EDIT_TEXT_CHARS,
                                "description": (
                                    "The exact replacement text. An empty "
                                    "string removes the matched passage."
                                ),
                            },
                        },
                        "required": [
                            "root",
                            "path",
                            "old_text",
                            "new_text",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "append_text_file",
                    "description": (
                        "Append exact text to an existing text file. Use only "
                        "when the user explicitly asks to append rather than "
                        "replace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                            "content": content_property,
                        },
                        "required": [
                            "root",
                            "path",
                            "content",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_directory",
                    "description": (
                        "Create one new directory inside an approved root. "
                        "Parent directories must already exist."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": root_property,
                            "path": path_property,
                        },
                        "required": [
                            "root",
                            "path",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        ]

        if (
            self.internet_manager is not None
            and self.internet_manager.is_enabled
        ):
            schemas.extend(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "description": (
                                "Search the current public web. Use for recent, "
                                "changing, niche, or explicitly online facts. "
                                "Results include titles, URLs, and snippets."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 500,
                                        "description": (
                                            "A focused web search query."
                                        ),
                                    },
                                    "max_results": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": MAX_SEARCH_RESULTS,
                                        "default": DEFAULT_SEARCH_RESULTS,
                                    },
                                },
                                "required": [
                                    "query",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "fetch_web_page",
                            "description": (
                                "Fetch readable text from one public HTTP(S) "
                                "page. Use when the user provides a URL or a "
                                "specific page must be inspected."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "url": {
                                        "type": "string",
                                        "description": (
                                            "The exact public HTTP(S) URL."
                                        ),
                                    },
                                    "max_chars": {
                                        "type": "integer",
                                        "minimum": 1000,
                                        "maximum": MAX_PAGE_CHARS,
                                        "default": DEFAULT_PAGE_CHARS,
                                    },
                                },
                                "required": [
                                    "url",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                ]
            )

        return schemas

    @staticmethod
    def _is_within(
        candidate: Path,
        root: Path,
    ) -> bool:
        """Return True when candidate is inside root."""

        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _get_root(
        self,
        root_name: str,
    ) -> Path:
        cleaned_name = root_name.strip().lower()

        root = self.allowed_roots.get(
            cleaned_name
        )

        if root is None:
            raise ToolError(
                "Invalid root. Allowed roots are: "
                + ", ".join(
                    sorted(self.allowed_roots)
                )
            )

        return root

    def _resolve_safe_path(
        self,
        root_name: str,
        relative_path: str,
    ) -> tuple[Path, Path]:
        root = self._get_root(root_name)

        cleaned_relative_path = (
            relative_path.strip()
        )

        if not cleaned_relative_path:
            candidate = root
        else:
            supplied_path = Path(
                cleaned_relative_path
            )

            if supplied_path.is_absolute():
                raise ToolError(
                    "Absolute paths are not allowed."
                )

            path_probe = root

            for part in supplied_path.parts:
                if part in {
                    "",
                    ".",
                }:
                    continue

                path_probe = path_probe / part

                if path_probe.is_symlink():
                    raise ToolError(
                        "Symbolic-link paths are not allowed."
                    )

            candidate = (
                root / supplied_path
            ).resolve()

        if not self._is_within(
            candidate,
            root,
        ):
            raise ToolError(
                "The requested path is outside "
                "the approved directory."
            )

        return root, candidate

    def get_current_time(
        self,
    ) -> dict[str, Any]:
        """Return the host's current local time."""

        current_time = datetime.now().astimezone()

        return {
            "success": True,
            "tool": "get_current_time",
            "local_time": current_time.isoformat(
                timespec="seconds"
            ),
            "timezone": str(
                current_time.tzinfo
            ),
        }

    def list_files(
        self,
        root: str = "documents",
        path: str = ".",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """List files under an approved directory."""

        approved_root, target = (
            self._resolve_safe_path(
                root_name=root,
                relative_path=path,
            )
        )

        if not target.exists():
            raise ToolError(
                "The requested path does not exist."
            )

        if not target.is_dir():
            raise ToolError(
                "The requested path is not a directory."
            )

        entries: list[dict[str, Any]] = []

        if recursive:
            for (
                current_directory,
                directory_names,
                file_names,
            ) in os.walk(target):
                directory_names[:] = [
                    name
                    for name in directory_names
                    if name not in IGNORED_DIRECTORY_NAMES
                ]

                current_path = Path(
                    current_directory
                ).resolve()

                if not self._is_within(
                    current_path,
                    approved_root,
                ):
                    directory_names[:] = []
                    continue

                for directory_name in sorted(
                    directory_names,
                    key=str.lower,
                ):
                    candidate = (
                        current_path
                        / directory_name
                    ).resolve()

                    if not self._is_within(
                        candidate,
                        approved_root,
                    ):
                        continue

                    entries.append(
                        {
                            "path": str(
                                candidate.relative_to(
                                    approved_root
                                )
                            ),
                            "type": "directory",
                        }
                    )

                    if (
                        len(entries)
                        >= MAX_LIST_RESULTS
                    ):
                        break

                if (
                    len(entries)
                    >= MAX_LIST_RESULTS
                ):
                    break

                for file_name in sorted(
                    file_names,
                    key=str.lower,
                ):
                    candidate = (
                        current_path
                        / file_name
                    ).resolve()

                    if not self._is_within(
                        candidate,
                        approved_root,
                    ):
                        continue

                    entries.append(
                        {
                            "path": str(
                                candidate.relative_to(
                                    approved_root
                                )
                            ),
                            "type": "file",
                        }
                    )

                    if (
                        len(entries)
                        >= MAX_LIST_RESULTS
                    ):
                        break

                if (
                    len(entries)
                    >= MAX_LIST_RESULTS
                ):
                    break
        else:
            for candidate in sorted(
                target.iterdir(),
                key=lambda item: str(item).lower(),
            ):
                if (
                    candidate.is_dir()
                    and candidate.name
                    in IGNORED_DIRECTORY_NAMES
                ):
                    continue

                resolved_candidate = (
                    candidate.resolve()
                )

                if not self._is_within(
                    resolved_candidate,
                    approved_root,
                ):
                    continue

                entries.append(
                    {
                        "path": str(
                            resolved_candidate.relative_to(
                                approved_root
                            )
                        ),
                        "type": (
                            "directory"
                            if resolved_candidate.is_dir()
                            else "file"
                        ),
                    }
                )

                if (
                    len(entries)
                    >= MAX_LIST_RESULTS
                ):
                    break

        return {
            "success": True,
            "tool": "list_files",
            "root": root,
            "requested_path": path,
            "recursive": recursive,
            "entries": entries,
            "truncated": (
                len(entries)
                >= MAX_LIST_RESULTS
            ),
            "ignored_directories": sorted(
                IGNORED_DIRECTORY_NAMES
            ),
        }

    def read_text_file(
        self,
        root: str,
        path: str,
        max_chars: int = DEFAULT_READ_CHARS,
    ) -> dict[str, Any]:
        """Read one supported text file from an approved directory."""

        if (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
        ):
            raise ToolError(
                "max_chars must be an integer."
            )

        if not (
            1 <= max_chars <= MAX_RETURNED_CHARS
        ):
            raise ToolError(
                f"max_chars must be between 1 and "
                f"{MAX_RETURNED_CHARS}."
            )

        approved_root, target = (
            self._resolve_safe_path(
                root_name=root,
                relative_path=path,
            )
        )

        if not target.exists():
            raise ToolError(
                "The requested file does not exist."
            )

        if not target.is_file():
            raise ToolError(
                "The requested path is not a file."
            )

        if (
            target.suffix.lower()
            not in ALLOWED_TEXT_EXTENSIONS
        ):
            raise ToolError(
                "Unsupported file type. Allowed extensions: "
                + ", ".join(
                    sorted(ALLOWED_TEXT_EXTENSIONS)
                )
            )

        file_size = target.stat().st_size

        if file_size > MAX_READ_BYTES:
            raise ToolError(
                f"File is too large to read safely "
                f"({file_size} bytes; limit is "
                f"{MAX_READ_BYTES} bytes)."
            )

        try:
            content = target.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise ToolError(
                f"Unable to read the file: {error}"
            ) from error

        relative_name = str(
            target.relative_to(
                approved_root
            )
        )

        content_was_truncated = (
            len(content) > max_chars
        )

        returned_content = content[:max_chars]

        return {
            "success": True,
            "tool": "read_text_file",
            "root": root,
            "path": relative_name,
            "size_bytes": file_size,
            "returned_chars": len(
                returned_content
            ),
            "truncated": content_was_truncated,
            "content": returned_content,
        }


    @staticmethod
    def _validate_write_content(
        content: Any,
    ) -> tuple[str, bytes, str]:
        """Validate and encode text intended for a write tool."""

        if not isinstance(
            content,
            str,
        ):
            raise ToolError(
                "content must be a string."
            )

        if "\x00" in content:
            raise ToolError(
                "Text content cannot contain null characters."
            )

        if len(content) > MAX_WRITE_CHARS:
            raise ToolError(
                f"Content is too long ({len(content)} characters; "
                f"limit is {MAX_WRITE_CHARS})."
            )

        payload = content.encode(
            "utf-8"
        )

        if len(payload) > MAX_WRITE_BYTES:
            raise ToolError(
                f"UTF-8 content is too large ({len(payload)} bytes; "
                f"limit is {MAX_WRITE_BYTES} bytes)."
            )

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        return content, payload, digest

    def _resolve_text_write_target(
        self,
        root: str,
        path: str,
    ) -> tuple[Path, Path]:
        """Resolve and validate a text-file target inside an approved root."""

        approved_root, target = (
            self._resolve_safe_path(
                root_name=root,
                relative_path=path,
            )
        )

        if target == approved_root:
            raise ToolError(
                "A file path is required."
            )

        if (
            target.suffix.lower()
            not in ALLOWED_TEXT_EXTENSIONS
        ):
            raise ToolError(
                "Unsupported file type. Allowed extensions: "
                + ", ".join(
                    sorted(ALLOWED_TEXT_EXTENSIONS)
                )
            )

        if not target.parent.exists():
            raise ToolError(
                "The target parent directory does not exist."
            )

        if not target.parent.is_dir():
            raise ToolError(
                "The target parent path is not a directory."
            )

        if target.exists() and not target.is_file():
            raise ToolError(
                "The target path is not a regular file."
            )

        return approved_root, target

    @staticmethod
    def _validate_exact_arguments(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        allowed: set[str],
        required: set[str],
    ) -> None:
        """Reject unexpected or missing tool arguments."""

        unexpected = (
            set(arguments)
            - allowed
        )

        if unexpected:
            raise ToolError(
                f"Unexpected {tool_name} arguments: "
                + ", ".join(
                    sorted(unexpected)
                )
            )

        missing = (
            required
            - set(arguments)
        )

        if missing:
            raise ToolError(
                f"{tool_name} requires: "
                + ", ".join(
                    sorted(missing)
                )
            )

    @staticmethod
    def _validate_edit_text(
        value: Any,
        *,
        field_name: str,
        allow_empty: bool,
    ) -> tuple[str, bytes, str]:
        """Validate one exact text argument for a targeted edit."""

        if not isinstance(
            value,
            str,
        ):
            raise ToolError(
                f"{field_name} must be a string."
            )

        if "\x00" in value:
            raise ToolError(
                f"{field_name} cannot contain null characters."
            )

        if not allow_empty and value == "":
            raise ToolError(
                f"{field_name} cannot be empty."
            )

        if len(value) > MAX_EDIT_TEXT_CHARS:
            raise ToolError(
                f"{field_name} is too long "
                f"({len(value)} characters; "
                f"limit is {MAX_EDIT_TEXT_CHARS})."
            )

        payload = value.encode(
            "utf-8"
        )

        if len(payload) > MAX_WRITE_BYTES:
            raise ToolError(
                f"{field_name} is too large "
                f"({len(payload)} UTF-8 bytes; "
                f"limit is {MAX_WRITE_BYTES})."
            )

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        return value, payload, digest

    def _read_text_target_for_edit(
        self,
        *,
        root: str,
        path: str,
    ) -> tuple[
        Path,
        Path,
        str,
        bytes,
        str,
    ]:
        """Read and validate an existing UTF-8 text file for editing."""

        approved_root, target = (
            self._resolve_text_write_target(
                root=root,
                path=path,
            )
        )

        if not target.exists():
            raise ToolError(
                "The target file does not exist."
            )

        size_bytes = target.stat().st_size

        if size_bytes > MAX_READ_BYTES:
            raise ToolError(
                f"The target file is too large to edit safely "
                f"({size_bytes} bytes; limit is {MAX_READ_BYTES})."
            )

        try:
            payload = target.read_bytes()
        except OSError as error:
            raise ToolError(
                f"Unable to read the target file: {error}"
            ) from error

        try:
            text = payload.decode(
                "utf-8"
            )
        except UnicodeDecodeError as error:
            raise ToolError(
                "The target file is not valid UTF-8 text."
            ) from error

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        return (
            approved_root,
            target,
            text,
            payload,
            digest,
        )

    @staticmethod
    def _build_occurrence_passages(
        text: str,
        *,
        occurrence_index: int,
        old_text: str,
        new_text: str,
    ) -> dict[str, Any]:
        """Build bounded before/after passages around one exact match."""

        context_start = max(
            0,
            occurrence_index
            - EDIT_CONTEXT_CHARS,
        )
        occurrence_end = (
            occurrence_index
            + len(old_text)
        )
        context_end = min(
            len(text),
            occurrence_end
            + EDIT_CONTEXT_CHARS,
        )

        prefix = text[
            context_start:occurrence_index
        ]
        suffix = text[
            occurrence_end:context_end
        ]

        before_passage = (
            prefix
            + old_text
            + suffix
        )
        after_passage = (
            prefix
            + new_text
            + suffix
        )

        line_number = (
            text.count(
                "\n",
                0,
                occurrence_index,
            )
            + 1
        )

        previous_newline = text.rfind(
            "\n",
            0,
            occurrence_index,
        )
        column_number = (
            occurrence_index
            - previous_newline
        )

        return {
            "before_passage": before_passage,
            "after_passage": after_passage,
            "context_truncated_before": (
                context_start > 0
            ),
            "context_truncated_after": (
                context_end < len(text)
            ),
            "line_number": line_number,
            "column_number": column_number,
        }

    def preview_tool_action(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Validate a confirmation-gated action and return an exact preview.

        This method never modifies the filesystem.
        """

        cleaned_name = tool_name.strip()
        arguments = arguments or {}

        try:
            policy = self.get_tool_policy(
                cleaned_name
            )

            if not policy.get(
                "requires_confirmation",
                True,
            ):
                raise ToolError(
                    "This tool does not require a write-action preview."
                )

            if cleaned_name in {
                "create_text_file",
                "replace_text_file",
                "append_text_file",
            }:
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                        "content",
                    },
                    required={
                        "root",
                        "path",
                        "content",
                    },
                )

                root_name = str(
                    arguments["root"]
                )
                supplied_path = str(
                    arguments["path"]
                )
                content, payload, digest = (
                    self._validate_write_content(
                        arguments["content"]
                    )
                )
                approved_root, target = (
                    self._resolve_text_write_target(
                        root=root_name,
                        path=supplied_path,
                    )
                )

                exists = target.exists()

                if (
                    cleaned_name
                    == "create_text_file"
                    and exists
                ):
                    raise ToolError(
                        "The target file already exists. "
                        "Creation will not overwrite it."
                    )

                if (
                    cleaned_name
                    in {
                        "replace_text_file",
                        "append_text_file",
                    }
                    and not exists
                ):
                    raise ToolError(
                        "The target file does not exist."
                    )

                action_names = {
                    "create_text_file": "create new text file",
                    "replace_text_file": (
                        "replace complete file contents"
                    ),
                    "append_text_file": (
                        "append text to existing file"
                    ),
                }

                return {
                    "success": True,
                    "tool": cleaned_name,
                    "action": action_names[
                        cleaned_name
                    ],
                    "root": root_name,
                    "path": str(
                        target.relative_to(
                            approved_root
                        )
                    ),
                    "target_exists": exists,
                    "current_size_bytes": (
                        target.stat().st_size
                        if exists
                        else 0
                    ),
                    "content": content,
                    "content_chars": len(
                        content
                    ),
                    "content_bytes": len(
                        payload
                    ),
                    "content_sha256": digest,
                }

            if cleaned_name == "replace_text_occurrence":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                        "old_text",
                        "new_text",
                    },
                    required={
                        "root",
                        "path",
                        "old_text",
                        "new_text",
                    },
                )

                root_name = str(
                    arguments["root"]
                )
                supplied_path = str(
                    arguments["path"]
                )
                (
                    old_text,
                    old_payload,
                    old_digest,
                ) = self._validate_edit_text(
                    arguments["old_text"],
                    field_name="old_text",
                    allow_empty=False,
                )
                (
                    new_text,
                    new_payload,
                    new_digest,
                ) = self._validate_edit_text(
                    arguments["new_text"],
                    field_name="new_text",
                    allow_empty=True,
                )

                (
                    approved_root,
                    target,
                    current_text,
                    current_payload,
                    source_digest,
                ) = self._read_text_target_for_edit(
                    root=root_name,
                    path=supplied_path,
                )

                occurrence_count = (
                    current_text.count(
                        old_text
                    )
                )

                if occurrence_count == 0:
                    raise ToolError(
                        "The exact old text was not found in the target file."
                    )

                if occurrence_count > 1:
                    raise ToolError(
                        "The exact old text appears "
                        f"{occurrence_count} times. "
                        "Provide a longer, unique passage."
                    )

                occurrence_index = (
                    current_text.find(
                        old_text
                    )
                )
                updated_text = (
                    current_text[
                        :occurrence_index
                    ]
                    + new_text
                    + current_text[
                        occurrence_index
                        + len(old_text):
                    ]
                )
                updated_payload = (
                    updated_text.encode(
                        "utf-8"
                    )
                )

                if len(updated_payload) > MAX_READ_BYTES:
                    raise ToolError(
                        "The edited file would exceed the safe size limit "
                        f"of {MAX_READ_BYTES} bytes."
                    )

                passages = (
                    self._build_occurrence_passages(
                        current_text,
                        occurrence_index=(
                            occurrence_index
                        ),
                        old_text=old_text,
                        new_text=new_text,
                    )
                )

                return {
                    "success": True,
                    "tool": cleaned_name,
                    "action": (
                        "replace one exact text occurrence"
                    ),
                    "root": root_name,
                    "path": str(
                        target.relative_to(
                            approved_root
                        )
                    ),
                    "target_exists": True,
                    "current_size_bytes": len(
                        current_payload
                    ),
                    "result_size_bytes": len(
                        updated_payload
                    ),
                    "occurrence_count": 1,
                    "old_text": old_text,
                    "old_text_chars": len(
                        old_text
                    ),
                    "old_text_bytes": len(
                        old_payload
                    ),
                    "old_text_sha256": (
                        old_digest
                    ),
                    "new_text": new_text,
                    "new_text_chars": len(
                        new_text
                    ),
                    "new_text_bytes": len(
                        new_payload
                    ),
                    "new_text_sha256": (
                        new_digest
                    ),
                    "source_sha256": (
                        source_digest
                    ),
                    **passages,
                }

            if cleaned_name == "create_directory":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                    },
                    required={
                        "root",
                        "path",
                    },
                )

                root_name = str(
                    arguments["root"]
                )
                supplied_path = str(
                    arguments["path"]
                )
                approved_root, target = (
                    self._resolve_safe_path(
                        root_name=root_name,
                        relative_path=supplied_path,
                    )
                )

                if target == approved_root:
                    raise ToolError(
                        "A new directory path is required."
                    )

                if target.exists():
                    raise ToolError(
                        "The target path already exists."
                    )

                if not target.parent.exists():
                    raise ToolError(
                        "The parent directory does not exist."
                    )

                if not target.parent.is_dir():
                    raise ToolError(
                        "The parent path is not a directory."
                    )

                return {
                    "success": True,
                    "tool": cleaned_name,
                    "action": "create new directory",
                    "root": root_name,
                    "path": str(
                        target.relative_to(
                            approved_root
                        )
                    ),
                    "target_exists": False,
                }

            raise ToolError(
                f"Unknown or non-previewable tool: "
                f"{cleaned_name}"
            )

        except ToolError as error:
            return {
                "success": False,
                "tool": cleaned_name,
                "error": str(error),
            }

    def create_text_file(
        self,
        root: str,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        """Create a new UTF-8 text file without overwriting."""

        content, payload, digest = (
            self._validate_write_content(
                content
            )
        )
        approved_root, target = (
            self._resolve_text_write_target(
                root=root,
                path=path,
            )
        )

        if target.exists():
            raise ToolError(
                "The target file already exists. "
                "Creation will not overwrite it."
            )

        try:
            with target.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(
                    handle.fileno()
                )
        except FileExistsError as error:
            raise ToolError(
                "The target file already exists. "
                "Creation will not overwrite it."
            ) from error
        except OSError as error:
            raise ToolError(
                f"Unable to create the file: {error}"
            ) from error

        return {
            "success": True,
            "tool": "create_text_file",
            "root": root,
            "path": str(
                target.relative_to(
                    approved_root
                )
            ),
            "chars_written": len(
                content
            ),
            "bytes_written": len(
                payload
            ),
            "content_sha256": digest,
        }

    def replace_text_file(
        self,
        root: str,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        """Atomically replace the contents of an existing UTF-8 text file."""

        content, payload, digest = (
            self._validate_write_content(
                content
            )
        )
        approved_root, target = (
            self._resolve_text_write_target(
                root=root,
                path=path,
            )
        )

        if not target.exists():
            raise ToolError(
                "The target file does not exist."
            )

        previous_size = target.stat().st_size
        temporary_path: Path | None = None

        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary_path = Path(
                temporary_name
            )

            with os.fdopen(
                descriptor,
                "wb",
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(
                    handle.fileno()
                )

            os.replace(
                temporary_path,
                target,
            )
            temporary_path = None

        except OSError as error:
            raise ToolError(
                f"Unable to replace the file: {error}"
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

        return {
            "success": True,
            "tool": "replace_text_file",
            "root": root,
            "path": str(
                target.relative_to(
                    approved_root
                )
            ),
            "previous_size_bytes": previous_size,
            "chars_written": len(
                content
            ),
            "bytes_written": len(
                payload
            ),
            "content_sha256": digest,
        }

    def append_text_file(
        self,
        root: str,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        """Append UTF-8 text to an existing supported text file."""

        content, payload, digest = (
            self._validate_write_content(
                content
            )
        )
        approved_root, target = (
            self._resolve_text_write_target(
                root=root,
                path=path,
            )
        )

        if not target.exists():
            raise ToolError(
                "The target file does not exist."
            )

        previous_size = target.stat().st_size

        try:
            with target.open("ab") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(
                    handle.fileno()
                )
        except OSError as error:
            raise ToolError(
                f"Unable to append to the file: {error}"
            ) from error

        return {
            "success": True,
            "tool": "append_text_file",
            "root": root,
            "path": str(
                target.relative_to(
                    approved_root
                )
            ),
            "previous_size_bytes": previous_size,
            "chars_appended": len(
                content
            ),
            "bytes_appended": len(
                payload
            ),
            "new_size_bytes": target.stat().st_size,
            "content_sha256": digest,
        }

    def replace_text_occurrence(
        self,
        root: str,
        path: str,
        old_text: str,
        new_text: str,
        expected_file_sha256: str,
    ) -> dict[str, Any]:
        """
        Atomically replace one exact, case-sensitive text occurrence.

        The expected file hash binds execution to the version shown during the
        confirmation preview and prevents stale-preview edits.
        """

        (
            old_text,
            old_payload,
            old_digest,
        ) = self._validate_edit_text(
            old_text,
            field_name="old_text",
            allow_empty=False,
        )
        (
            new_text,
            new_payload,
            new_digest,
        ) = self._validate_edit_text(
            new_text,
            field_name="new_text",
            allow_empty=True,
        )

        if not isinstance(
            expected_file_sha256,
            str,
        ):
            raise ToolError(
                "expected_file_sha256 must be a string."
            )

        (
            approved_root,
            target,
            current_text,
            current_payload,
            source_digest,
        ) = self._read_text_target_for_edit(
            root=root,
            path=path,
        )

        if source_digest != expected_file_sha256:
            raise ToolError(
                "The target file changed after the confirmation preview. "
                "No edit was performed; request the edit again."
            )

        occurrence_count = (
            current_text.count(
                old_text
            )
        )

        if occurrence_count == 0:
            raise ToolError(
                "The exact old text is no longer present. "
                "No edit was performed."
            )

        if occurrence_count > 1:
            raise ToolError(
                "The exact old text now appears "
                f"{occurrence_count} times. "
                "No edit was performed."
            )

        occurrence_index = (
            current_text.find(
                old_text
            )
        )
        updated_text = (
            current_text[
                :occurrence_index
            ]
            + new_text
            + current_text[
                occurrence_index
                + len(old_text):
            ]
        )
        updated_payload = (
            updated_text.encode(
                "utf-8"
            )
        )

        if len(updated_payload) > MAX_READ_BYTES:
            raise ToolError(
                "The edited file would exceed the safe size limit "
                f"of {MAX_READ_BYTES} bytes."
            )

        result_digest = hashlib.sha256(
            updated_payload
        ).hexdigest()
        previous_size = len(
            current_payload
        )
        temporary_path: Path | None = None

        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary_path = Path(
                temporary_name
            )

            with os.fdopen(
                descriptor,
                "wb",
            ) as handle:
                handle.write(
                    updated_payload
                )
                handle.flush()
                os.fsync(
                    handle.fileno()
                )

            os.replace(
                temporary_path,
                target,
            )
            temporary_path = None

        except OSError as error:
            raise ToolError(
                f"Unable to apply the targeted edit: {error}"
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

        return {
            "success": True,
            "tool": "replace_text_occurrence",
            "root": root,
            "path": str(
                target.relative_to(
                    approved_root
                )
            ),
            "occurrences_replaced": 1,
            "previous_size_bytes": (
                previous_size
            ),
            "new_size_bytes": len(
                updated_payload
            ),
            "old_text_chars": len(
                old_text
            ),
            "old_text_bytes": len(
                old_payload
            ),
            "old_text_sha256": (
                old_digest
            ),
            "new_text_chars": len(
                new_text
            ),
            "new_text_bytes": len(
                new_payload
            ),
            "new_text_sha256": (
                new_digest
            ),
            "source_sha256": (
                source_digest
            ),
            "result_sha256": (
                result_digest
            ),
        }

    def create_directory(
        self,
        root: str,
        path: str,
    ) -> dict[str, Any]:
        """Create exactly one new directory inside an approved root."""

        approved_root, target = (
            self._resolve_safe_path(
                root_name=root,
                relative_path=path,
            )
        )

        if target == approved_root:
            raise ToolError(
                "A new directory path is required."
            )

        if target.exists():
            raise ToolError(
                "The target path already exists."
            )

        if not target.parent.exists():
            raise ToolError(
                "The parent directory does not exist."
            )

        if not target.parent.is_dir():
            raise ToolError(
                "The parent path is not a directory."
            )

        try:
            target.mkdir(
                parents=False,
                exist_ok=False,
            )
        except OSError as error:
            raise ToolError(
                f"Unable to create the directory: {error}"
            ) from error

        return {
            "success": True,
            "tool": "create_directory",
            "root": root,
            "path": str(
                target.relative_to(
                    approved_root
                )
            ),
        }

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """
        Validate and execute one allowlisted tool.

        Unknown tools and unexpected arguments are rejected.
        """

        cleaned_name = tool_name.strip()
        arguments = arguments or {}

        try:
            policy = self.get_tool_policy(
                cleaned_name
            )

            if (
                policy.get(
                    "requires_confirmation",
                    True,
                )
                and not confirmed
            ):
                raise ToolError(
                    "This write tool requires explicit host confirmation."
                )

            if cleaned_name == "search_web":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "query",
                        "max_results",
                    },
                    required={
                        "query",
                    },
                )

                if self.internet_manager is None:
                    raise ToolError(
                        "The internet manager is unavailable."
                    )

                raw_max_results = arguments.get(
                    "max_results",
                    DEFAULT_SEARCH_RESULTS,
                )

                if (
                    isinstance(
                        raw_max_results,
                        bool,
                    )
                    or not isinstance(
                        raw_max_results,
                        int,
                    )
                ):
                    raise ToolError(
                        "max_results must be an integer."
                    )

                return self.internet_manager.search_web(
                    query=str(
                        arguments["query"]
                    ),
                    max_results=raw_max_results,
                )

            if cleaned_name == "fetch_web_page":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "url",
                        "max_chars",
                    },
                    required={
                        "url",
                    },
                )

                if self.internet_manager is None:
                    raise ToolError(
                        "The internet manager is unavailable."
                    )

                raw_max_chars = arguments.get(
                    "max_chars",
                    DEFAULT_PAGE_CHARS,
                )

                if (
                    isinstance(
                        raw_max_chars,
                        bool,
                    )
                    or not isinstance(
                        raw_max_chars,
                        int,
                    )
                ):
                    raise ToolError(
                        "max_chars must be an integer."
                    )

                return self.internet_manager.fetch_web_page(
                    url=str(
                        arguments["url"]
                    ),
                    max_chars=raw_max_chars,
                )

            if cleaned_name == "get_current_time":
                if arguments:
                    raise ToolError(
                        "get_current_time does not accept arguments."
                    )

                return self.get_current_time()

            if cleaned_name == "list_files":
                allowed_arguments = {
                    "root",
                    "path",
                    "recursive",
                }

                unexpected = (
                    set(arguments)
                    - allowed_arguments
                )

                if unexpected:
                    raise ToolError(
                        "Unexpected list_files arguments: "
                        + ", ".join(
                            sorted(unexpected)
                        )
                    )

                root = str(
                    arguments.get(
                        "root",
                        "documents",
                    )
                )

                path = str(
                    arguments.get(
                        "path",
                        ".",
                    )
                )

                recursive_value = arguments.get(
                    "recursive",
                    False,
                )

                if not isinstance(
                    recursive_value,
                    bool,
                ):
                    raise ToolError(
                        "recursive must be true or false."
                    )

                return self.list_files(
                    root=root,
                    path=path,
                    recursive=recursive_value,
                )

            if cleaned_name == "read_text_file":
                allowed_arguments = {
                    "root",
                    "path",
                    "max_chars",
                }

                unexpected = (
                    set(arguments)
                    - allowed_arguments
                )

                if unexpected:
                    raise ToolError(
                        "Unexpected read_text_file arguments: "
                        + ", ".join(
                            sorted(unexpected)
                        )
                    )

                if "root" not in arguments:
                    raise ToolError(
                        "read_text_file requires a root argument."
                    )

                if "path" not in arguments:
                    raise ToolError(
                        "read_text_file requires a path argument."
                    )

                raw_max_chars = arguments.get(
                    "max_chars",
                    DEFAULT_READ_CHARS,
                )

                if (
                    isinstance(raw_max_chars, bool)
                    or not isinstance(
                        raw_max_chars,
                        int,
                    )
                ):
                    raise ToolError(
                        "max_chars must be an integer."
                    )

                return self.read_text_file(
                    root=str(arguments["root"]),
                    path=str(arguments["path"]),
                    max_chars=raw_max_chars,
                )

            if cleaned_name in {
                "create_text_file",
                "replace_text_file",
                "append_text_file",
            }:
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                        "content",
                    },
                    required={
                        "root",
                        "path",
                        "content",
                    },
                )

                root = str(
                    arguments["root"]
                )
                path = str(
                    arguments["path"]
                )
                content = arguments["content"]

                if cleaned_name == "create_text_file":
                    return self.create_text_file(
                        root=root,
                        path=path,
                        content=content,
                    )

                if cleaned_name == "replace_text_file":
                    return self.replace_text_file(
                        root=root,
                        path=path,
                        content=content,
                    )

                return self.append_text_file(
                    root=root,
                    path=path,
                    content=content,
                )

            if cleaned_name == "replace_text_occurrence":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                        "old_text",
                        "new_text",
                        "expected_file_sha256",
                    },
                    required={
                        "root",
                        "path",
                        "old_text",
                        "new_text",
                        "expected_file_sha256",
                    },
                )

                return self.replace_text_occurrence(
                    root=str(
                        arguments["root"]
                    ),
                    path=str(
                        arguments["path"]
                    ),
                    old_text=arguments[
                        "old_text"
                    ],
                    new_text=arguments[
                        "new_text"
                    ],
                    expected_file_sha256=str(
                        arguments[
                            "expected_file_sha256"
                        ]
                    ),
                )

            if cleaned_name == "create_directory":
                self._validate_exact_arguments(
                    tool_name=cleaned_name,
                    arguments=arguments,
                    allowed={
                        "root",
                        "path",
                    },
                    required={
                        "root",
                        "path",
                    },
                )

                return self.create_directory(
                    root=str(
                        arguments["root"]
                    ),
                    path=str(
                        arguments["path"]
                    ),
                )

            raise ToolError(
                f"Unknown or disallowed tool: "
                f"{cleaned_name}"
            )

        except ToolError as error:
            return {
                "success": False,
                "tool": cleaned_name,
                "error": str(error),
            }