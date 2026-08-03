from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit
from typing import Any


MAX_REQUEST_PREVIEW_CHARS = 500
MAX_ARGUMENTS_JSON_CHARS = 2_000
MAX_RESULT_SUMMARY_CHARS = 1_000


class ToolAuditLog:
    """
    Persistent audit log for Elise tool requests.

    The audit log intentionally stores metadata and concise summaries only.
    It never stores full file contents returned by read_text_file.
    """

    def __init__(
        self,
        db_path: str | Path,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    request_preview TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    access_mode TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    permission_mode TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    approved INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    result_summary TEXT NOT NULL,
                    error TEXT
                )
                """
            )

    @staticmethod
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

    @classmethod
    def _sanitize_arguments(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Redact write payloads while retaining verifiable metadata.

        Tool audit records store text length and SHA-256, never the
        content supplied to create, replace, append, or targeted-edit
        operations.
        """

        sanitized: dict[str, Any] = {}

        for key, item in value.items():
            normalized_key = str(
                key
            ).strip().lower()

            if (
                normalized_key == "url"
                and isinstance(
                    item,
                    str,
                )
            ):
                try:
                    parsed_url = urlsplit(
                        item
                    )
                    safe_query = (
                        "<redacted>"
                        if parsed_url.query
                        else ""
                    )
                    sanitized[str(key)] = urlunsplit(
                        (
                            parsed_url.scheme,
                            parsed_url.netloc,
                            parsed_url.path,
                            safe_query,
                            "",
                        )
                    )
                except ValueError:
                    sanitized[str(key)] = (
                        "[Invalid URL omitted]"
                    )
            elif (
                normalized_key
                in {
                    "content",
                    "old_text",
                    "new_text",
                }
                and isinstance(
                    item,
                    str,
                )
            ):
                encoded = item.encode(
                    "utf-8"
                )
                sanitized[str(key)] = {
                    "redacted": True,
                    "chars": len(
                        item
                    ),
                    "bytes": len(
                        encoded
                    ),
                    "sha256": hashlib.sha256(
                        encoded
                    ).hexdigest(),
                }
            else:
                sanitized[str(key)] = item

        return sanitized

    @classmethod
    def _safe_json(
        cls,
        value: dict[str, Any],
    ) -> str:
        try:
            serialized = json.dumps(
                cls._sanitize_arguments(
                    value
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        except (
            TypeError,
            ValueError,
        ):
            serialized = json.dumps(
                {
                    "unserializable_arguments": (
                        "Arguments could not be serialized."
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )

        return cls._truncate(
            serialized,
            MAX_ARGUMENTS_JSON_CHARS,
        )

    def record(
        self,
        *,
        source: str,
        request_text: str,
        tool_name: str,
        arguments: dict[str, Any],
        policy: dict[str, Any],
        approved: bool,
        result: dict[str, Any],
        result_summary: str,
    ) -> int:
        """
        Record one requested tool execution or rejection.

        Full tool payloads are not stored. Read results never store file
        contents, and write requests redact content, old_text, and new_text
        into length and SHA-256 metadata before insertion.
        """

        created_at = datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )

        success = bool(
            result.get("success")
        )

        error_value = result.get("error")

        if error_value is None:
            error_text: str | None = None
        else:
            error_text = self._truncate(
                str(error_value),
                MAX_RESULT_SUMMARY_CHARS,
            )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tool_audit (
                    created_at,
                    source,
                    request_preview,
                    tool_name,
                    arguments_json,
                    access_mode,
                    risk_level,
                    permission_mode,
                    requires_confirmation,
                    approved,
                    success,
                    result_summary,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    self._truncate(
                        source.strip()
                        or "unknown",
                        100,
                    ),
                    self._truncate(
                        (
                            "[Write request text omitted from audit log.]"
                            if str(
                                policy.get(
                                    "access_mode",
                                    "unknown",
                                )
                            )
                            == "write"
                            else request_text.strip()
                        ),
                        MAX_REQUEST_PREVIEW_CHARS,
                    ),
                    self._truncate(
                        tool_name.strip()
                        or "unknown",
                        100,
                    ),
                    self._safe_json(
                        arguments
                    ),
                    self._truncate(
                        str(
                            policy.get(
                                "access_mode",
                                "unknown",
                            )
                        ),
                        50,
                    ),
                    self._truncate(
                        str(
                            policy.get(
                                "risk_level",
                                "unknown",
                            )
                        ),
                        50,
                    ),
                    self._truncate(
                        str(
                            policy.get(
                                "permission_mode",
                                "unknown",
                            )
                        ),
                        50,
                    ),
                    int(
                        bool(
                            policy.get(
                                "requires_confirmation",
                                True,
                            )
                        )
                    ),
                    int(bool(approved)),
                    int(success),
                    self._truncate(
                        result_summary,
                        MAX_RESULT_SUMMARY_CHARS,
                    ),
                    error_text,
                ),
            )

            return int(
                cursor.lastrowid
            )

    def list_recent(
        self,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        """Return the newest audit entries first."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
        ):
            raise ValueError(
                "Audit-log limit must be an integer."
            )

        if not 1 <= limit <= 100:
            raise ValueError(
                "Audit-log limit must be between 1 and 100."
            )

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT
                    id,
                    created_at,
                    source,
                    request_preview,
                    tool_name,
                    arguments_json,
                    access_mode,
                    risk_level,
                    permission_mode,
                    requires_confirmation,
                    approved,
                    success,
                    result_summary,
                    error
                FROM tool_audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def count(self) -> int:
        """Return the number of recorded audit entries."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM tool_audit
                """
            ).fetchone()

        if row is None:
            return 0

        return int(row["count"])