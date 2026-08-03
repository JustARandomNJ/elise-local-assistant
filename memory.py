from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Sequence


ALLOWED_CATEGORIES = {
    "fact",
    "preference",
    "goal",
    "project",
    "observation",
}

ALLOWED_STATUSES = {
    "confirmed",
    "observed",
    "hypothesis",
}


MEMORY_SEARCH_STOP_WORDS = {
    "a",
    "about",
    "according",
    "all",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "based",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "know",
    "me",
    "my",
    "of",
    "on",
    "or",
    "remember",
    "that",
    "the",
    "this",
    "to",
    "using",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
}


MEMORY_QUERY_EXPANSIONS = {
    "communicate": {
        "communication",
        "feedback",
        "direct",
        "style",
        "tone",
        "concise",
        "friendly",
    },
    "communication": {
        "communicate",
        "feedback",
        "direct",
        "style",
        "tone",
        "concise",
        "friendly",
    },
    "talk": {
        "communication",
        "feedback",
        "direct",
        "style",
        "tone",
    },
    "respond": {
        "response",
        "feedback",
        "direct",
        "style",
        "tone",
        "concise",
    },
    "response": {
        "respond",
        "feedback",
        "direct",
        "style",
        "tone",
        "concise",
    },
    "hardware": {
        "esp32",
        "esp32-s3",
        "computer",
        "device",
        "board",
    },
    "device": {
        "hardware",
        "esp32",
        "esp32-s3",
        "computer",
        "board",
    },
}


STATUS_WEIGHTS = {
    "confirmed": 1.25,
    "observed": 1.0,
    "hypothesis": 0.75,
}


class MemoryStore:
    """Persistent, structured SQLite storage for Elise."""

    def __init__(
        self,
        db_path: str | Path = "data/elise.db",
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()
        self._migrate_existing_database()

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
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL DEFAULT 'fact',
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _migrate_existing_database(self) -> None:
        """
        Upgrade databases created by older Elise versions.

        Existing memories are preserved and treated as confirmed facts.
        """

        with self._connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(memories)"
                ).fetchall()
            }

            missing_columns = {
                "category": (
                    "TEXT NOT NULL DEFAULT 'fact'"
                ),
                "status": (
                    "TEXT NOT NULL DEFAULT 'confirmed'"
                ),
                "confidence": (
                    "REAL NOT NULL DEFAULT 1.0"
                ),
                "source": (
                    "TEXT NOT NULL DEFAULT 'user'"
                ),
            }

            for (
                column_name,
                column_definition,
            ) in missing_columns.items():
                if column_name in columns:
                    continue

                connection.execute(
                    f"""
                    ALTER TABLE memories
                    ADD COLUMN {column_name}
                    {column_definition}
                    """
                )

    @staticmethod
    def _normalize_token(
        token: str,
    ) -> str:
        """
        Apply lightweight normalization for common word endings.

        This is not a full linguistic stemmer, but it helps queries such as
        "priorities" match memories containing "priority".
        """

        normalized = token.lower().strip()

        if (
            len(normalized) > 4
            and normalized.endswith("ies")
        ):
            return normalized[:-3] + "y"

        if (
            len(normalized) > 5
            and normalized.endswith("ing")
        ):
            return normalized[:-3]

        if (
            len(normalized) > 4
            and normalized.endswith("ed")
        ):
            return normalized[:-2]

        if (
            len(normalized) > 3
            and normalized.endswith("s")
            and not normalized.endswith("ss")
        ):
            return normalized[:-1]

        return normalized

    @classmethod
    def _tokenize(
        cls,
        text: str,
    ) -> list[str]:
        """Convert text into normalized search terms."""

        raw_tokens = re.findall(
            r"[a-z0-9_+#.-]+",
            text.lower(),
        )

        return [
            cls._normalize_token(token)
            for token in raw_tokens
            if token
        ]

    @classmethod
    def _tokenize_query(
        cls,
        text: str,
    ) -> list[str]:
        """
        Extract meaningful memory-search terms and add a small number of
        controlled synonyms for common personal-context questions.
        """

        base_tokens: list[str] = []

        for raw_token in re.findall(
            r"[a-z0-9_+#.-]+",
            text.lower(),
        ):
            if raw_token in MEMORY_SEARCH_STOP_WORDS:
                continue

            normalized = cls._normalize_token(
                raw_token
            )

            if len(normalized) <= 1:
                continue

            base_tokens.append(normalized)

        expanded_tokens = list(base_tokens)

        for token in base_tokens:
            expansions = MEMORY_QUERY_EXPANSIONS.get(
                token,
                set(),
            )

            expanded_tokens.extend(
                cls._normalize_token(expansion)
                for expansion in expansions
            )

        return list(
            dict.fromkeys(expanded_tokens)
        )

    @staticmethod
    def _validate_memory(
        content: str,
        category: str,
        status: str,
        confidence: float,
    ) -> tuple[str, str, str, float]:
        cleaned_content = content.strip()
        cleaned_category = (
            category.strip().lower()
        )
        cleaned_status = (
            status.strip().lower()
        )

        if not cleaned_content:
            raise ValueError(
                "Memory content cannot be empty."
            )

        if (
            cleaned_category
            not in ALLOWED_CATEGORIES
        ):
            raise ValueError(
                "Invalid category. Use: "
                + ", ".join(
                    sorted(ALLOWED_CATEGORIES)
                )
            )

        if (
            cleaned_status
            not in ALLOWED_STATUSES
        ):
            raise ValueError(
                "Invalid status. Use: "
                + ", ".join(
                    sorted(ALLOWED_STATUSES)
                )
            )

        try:
            cleaned_confidence = float(
                confidence
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                "Confidence must be a number "
                "from 0.0 to 1.0."
            ) from error

        if not (
            0.0
            <= cleaned_confidence
            <= 1.0
        ):
            raise ValueError(
                "Confidence must be between "
                "0.0 and 1.0."
            )

        return (
            cleaned_content,
            cleaned_category,
            cleaned_status,
            cleaned_confidence,
        )

    def add(
        self,
        content: str,
        category: str = "fact",
        status: str = "confirmed",
        confidence: float = 1.0,
        source: str = "user",
    ) -> bool:
        """
        Add one structured memory.

        Returns False when identical content already exists.
        """

        (
            cleaned_content,
            cleaned_category,
            cleaned_status,
            cleaned_confidence,
        ) = self._validate_memory(
            content=content,
            category=category,
            status=status,
            confidence=confidence,
        )

        cleaned_source = (
            source.strip()
            or "unknown"
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO memories (
                    content,
                    category,
                    status,
                    confidence,
                    source
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cleaned_content,
                    cleaned_category,
                    cleaned_status,
                    cleaned_confidence,
                    cleaned_source,
                ),
            )

            return cursor.rowcount == 1

    def list_all(
        self,
        category: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return all memories or memories from one category."""

        with self._connect() as connection:
            if category is None:
                return connection.execute(
                    """
                    SELECT
                        id,
                        content,
                        category,
                        status,
                        confidence,
                        source,
                        created_at
                    FROM memories
                    ORDER BY id
                    """
                ).fetchall()

            cleaned_category = (
                category.strip().lower()
            )

            if (
                cleaned_category
                not in ALLOWED_CATEGORIES
            ):
                raise ValueError(
                    "Invalid category. Use: "
                    + ", ".join(
                        sorted(
                            ALLOWED_CATEGORIES
                        )
                    )
                )

            return connection.execute(
                """
                SELECT
                    id,
                    content,
                    category,
                    status,
                    confidence,
                    source,
                    created_at
                FROM memories
                WHERE category = ?
                ORDER BY id
                """,
                (cleaned_category,),
            ).fetchall()

    def delete(
        self,
        memory_id: int,
    ) -> bool:
        """Delete one persistent memory by ID."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM memories
                WHERE id = ?
                """,
                (memory_id,),
            )

            return cursor.rowcount == 1

    def search(
        self,
        query: str,
        top_k: int = 6,
    ) -> list[sqlite3.Row]:
        """
        Retrieve persistent memories relevant to a question.

        Confirmed memories and higher-confidence memories receive more weight.
        """

        cleaned_query = query.strip()

        if not cleaned_query or top_k <= 0:
            return []

        query_tokens = self._tokenize_query(
            cleaned_query
        )

        if not query_tokens:
            return []

        unique_query_tokens = set(
            query_tokens
        )

        normalized_query_phrase = " ".join(
            query_tokens
        )

        all_memories = self.list_all()
        scored_memories: list[
            tuple[float, int]
        ] = []

        for memory in all_memories:
            content = memory["content"]
            category = memory["category"]
            status = memory["status"]
            source = memory["source"]
            confidence = float(
                memory["confidence"]
            )

            content_tokens = self._tokenize(
                content
            )

            metadata_tokens = set(
                self._tokenize(
                    f"{category} {status} {source}"
                )
            )

            token_counts: dict[str, int] = {}

            for token in content_tokens:
                token_counts[token] = (
                    token_counts.get(
                        token,
                        0,
                    )
                    + 1
                )

            matched_tokens: set[str] = set()
            base_score = 0.0

            for token in unique_query_tokens:
                content_frequency = (
                    token_counts.get(
                        token,
                        0,
                    )
                )

                if content_frequency > 0:
                    matched_tokens.add(token)
                    base_score += min(
                        content_frequency,
                        4,
                    )

                if token in metadata_tokens:
                    matched_tokens.add(token)
                    base_score += 1.5

            if not matched_tokens:
                continue

            coverage = (
                len(matched_tokens)
                / len(unique_query_tokens)
            )

            base_score += coverage * 4.0

            normalized_content = " ".join(
                content_tokens
            )

            if (
                normalized_query_phrase
                and normalized_query_phrase
                in normalized_content
            ):
                base_score += 5.0

            if (
                len(matched_tokens)
                == len(unique_query_tokens)
            ):
                base_score += 2.0

            status_weight = (
                STATUS_WEIGHTS.get(
                    status,
                    0.75,
                )
            )

            confidence_weight = (
                0.5
                + (confidence * 0.5)
            )

            final_score = (
                base_score
                * status_weight
                * confidence_weight
            )

            scored_memories.append(
                (
                    final_score,
                    int(memory["id"]),
                )
            )

        scored_memories.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )

        selected_rows: list[
            sqlite3.Row
        ] = []

        with self._connect() as connection:
            for (
                score,
                memory_id,
            ) in scored_memories[:top_k]:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        content,
                        category,
                        status,
                        confidence,
                        source,
                        created_at,
                        ? AS relevance_score
                    FROM memories
                    WHERE id = ?
                    """,
                    (
                        score,
                        memory_id,
                    ),
                ).fetchone()

                if row is not None:
                    selected_rows.append(row)

        return selected_rows

    def import_profile(
        self,
        profile_path: str | Path,
    ) -> tuple[int, int]:
        """
        Import structured memories from a JSON profile.

        Returns the number added and number skipped as duplicates.
        """

        path = Path(profile_path)

        if not path.exists():
            raise FileNotFoundError(
                f"Profile file not found: {path}"
            )

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            profile_data: dict[
                str,
                Any,
            ] = json.load(file)

        raw_memories = profile_data.get(
            "memories"
        )

        if not isinstance(
            raw_memories,
            list,
        ):
            raise ValueError(
                'Profile JSON must contain '
                'a "memories" list.'
            )

        added = 0
        skipped = 0

        for item in raw_memories:
            if not isinstance(item, dict):
                raise ValueError(
                    "Every imported memory must "
                    "be a JSON object."
                )

            was_added = self.add(
                content=str(
                    item.get(
                        "content",
                        "",
                    )
                ),
                category=str(
                    item.get(
                        "category",
                        "fact",
                    )
                ),
                status=str(
                    item.get(
                        "status",
                        "confirmed",
                    )
                ),
                confidence=float(
                    item.get(
                        "confidence",
                        1.0,
                    )
                ),
                source=str(
                    item.get(
                        "source",
                        "profile_import",
                    )
                ),
            )

            if was_added:
                added += 1
            else:
                skipped += 1

        return added, skipped

    def build_prompt(
        self,
        memories: Sequence[
            sqlite3.Row
        ] | None = None,
    ) -> str:
        """
        Format selected memories for inclusion in Elise's prompt.

        Passing None formats every memory for backward compatibility.
        Passing an empty sequence reports that no relevant memories were found.
        """

        if memories is None:
            selected_memories = (
                self.list_all()
            )
        else:
            selected_memories = list(
                memories
            )

        if not selected_memories:
            return (
                "No relevant persistent "
                "memories were retrieved."
            )

        grouped: dict[
            str,
            list[str],
        ] = {}

        for memory in selected_memories:
            category = memory["category"]

            grouped.setdefault(
                category,
                [],
            ).append(
                (
                    f"- Memory {memory['id']} "
                    f"[{memory['status']}; "
                    f"confidence "
                    f"{memory['confidence']:.2f}] "
                    f"{memory['content']}"
                )
            )

        sections: list[str] = []

        for (
            category,
            category_memories,
        ) in grouped.items():
            sections.append(
                category.upper()
                + ":\n"
                + "\n".join(
                    category_memories
                )
            )

        return "\n\n".join(sections)