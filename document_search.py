from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXTENSIONS = {
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


SEARCH_STOP_WORDS = {
    "a",
    "about",
    "according",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "document",
    "documents",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "local",
    "me",
    "my",
    "of",
    "on",
    "or",
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
    "you",
    "your",
}


@dataclass(frozen=True)
class DocumentChunk:
    """One searchable section of a local document."""

    source: str
    chunk_number: int
    text: str


@dataclass(frozen=True)
class SearchResult:
    """One ranked local-document search result."""

    source: str
    chunk_number: int
    score: float
    text: str


class DocumentStore:
    """
    Lightweight offline document index.

    This version uses transparent keyword matching instead of embeddings.
    It is fast, dependency-free, and easy to inspect while Elise is still
    in the prototype stage.
    """

    def __init__(
        self,
        root_directory: str | Path,
        chunk_words: int = 220,
        overlap_words: int = 40,
    ) -> None:
        self.root_directory = Path(root_directory)
        self.chunk_words = chunk_words
        self.overlap_words = overlap_words

        if self.chunk_words <= 0:
            raise ValueError(
                "chunk_words must be greater than zero."
            )

        if self.overlap_words < 0:
            raise ValueError(
                "overlap_words cannot be negative."
            )

        if self.overlap_words >= self.chunk_words:
            raise ValueError(
                "overlap_words must be smaller than chunk_words."
            )

        self.root_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._chunks: list[DocumentChunk] = []
        self._documents: list[str] = []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        Convert text into normalized searchable terms.

        Characters commonly found in technical terms are retained, allowing
        searches for strings such as ESP32-S3, C++, filenames, and identifiers.
        """

        return re.findall(
            r"[a-z0-9_+#.-]+",
            text.lower(),
        )

    @classmethod
    def _tokenize_query(cls, text: str) -> list[str]:
        """
        Extract meaningful terms from a user's search query.

        Common conversational words are removed so phrases such as
        "according to my local documents" do not retrieve irrelevant files.
        """

        return [
            token
            for token in cls._tokenize(text)
            if token not in SEARCH_STOP_WORDS
            and len(token) > 1
        ]

    def _create_chunks(
        self,
        source: str,
        text: str,
    ) -> list[DocumentChunk]:
        """
        Split one document into overlapping word-based sections.
        """

        words = text.split()

        if not words:
            return []

        chunks: list[DocumentChunk] = []
        start = 0
        chunk_number = 1

        while start < len(words):
            end = min(
                start + self.chunk_words,
                len(words),
            )

            chunk_text = " ".join(
                words[start:end]
            ).strip()

            if chunk_text:
                chunks.append(
                    DocumentChunk(
                        source=source,
                        chunk_number=chunk_number,
                        text=chunk_text,
                    )
                )

            if end >= len(words):
                break

            start = end - self.overlap_words
            chunk_number += 1

        return chunks

    def reindex(self) -> tuple[int, int]:
        """
        Rebuild the document index from the configured directory.

        Returns:
            A tuple containing:
            - Number of indexed files
            - Number of searchable chunks
        """

        self._chunks.clear()
        self._documents.clear()

        for path in sorted(
            self.root_directory.rglob("*")
        ):
            if not path.is_file():
                continue

            if (
                path.suffix.lower()
                not in SUPPORTED_EXTENSIONS
            ):
                continue

            try:
                text = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue

            if not text.strip():
                continue

            relative_path = str(
                path.relative_to(
                    self.root_directory
                )
            )

            self._documents.append(relative_path)

            document_chunks = self._create_chunks(
                source=relative_path,
                text=text,
            )

            self._chunks.extend(document_chunks)

        return (
            len(self._documents),
            len(self._chunks),
        )

    def list_documents(self) -> list[str]:
        """Return the paths of all currently indexed documents."""

        return list(self._documents)

    def search(
        self,
        query: str,
        top_k: int = 4,
    ) -> list[SearchResult]:
        """
        Search the indexed document chunks.

        Scoring considers:
        - Frequency of meaningful query terms in the text
        - Query terms appearing in the filename
        - Exact meaningful phrase matches
        - Percentage of unique query terms covered by the result
        """

        cleaned_query = query.strip()

        if not cleaned_query:
            return []

        if top_k <= 0:
            return []

        query_tokens = self._tokenize_query(
            cleaned_query
        )

        if not query_tokens:
            return []

        unique_query_tokens = set(query_tokens)
        normalized_phrase = " ".join(query_tokens)

        results: list[SearchResult] = []

        for chunk in self._chunks:
            normalized_text = chunk.text.lower()

            text_tokens = self._tokenize(
                chunk.text
            )

            filename_tokens = self._tokenize(
                chunk.source
            )

            token_counts: dict[str, int] = {}

            for token in text_tokens:
                token_counts[token] = (
                    token_counts.get(token, 0) + 1
                )

            score = 0.0

            for token in query_tokens:
                score += token_counts.get(
                    token,
                    0,
                )

                if token in filename_tokens:
                    score += 2.0

                # This allows terms to match within technical filenames
                # such as "elise_project_notes.md".
                if token in chunk.source.lower():
                    score += 1.0

            if (
                normalized_phrase
                and normalized_phrase
                in normalized_text
            ):
                score += 5.0

            matched_unique_tokens = sum(
                1
                for token in unique_query_tokens
                if (
                    token in token_counts
                    or token in filename_tokens
                    or token in chunk.source.lower()
                )
            )

            # A result must match at least one meaningful term.
            if matched_unique_tokens == 0:
                continue

            coverage = (
                matched_unique_tokens
                / len(unique_query_tokens)
            )

            # Reward results covering more of the query.
            score += coverage * 2.0

            # Reward complete coverage of all meaningful terms.
            if (
                matched_unique_tokens
                == len(unique_query_tokens)
            ):
                score += 2.0

            if score <= 0:
                continue

            results.append(
                SearchResult(
                    source=chunk.source,
                    chunk_number=(
                        chunk.chunk_number
                    ),
                    score=score,
                    text=chunk.text,
                )
            )

        results.sort(
            key=lambda result: (
                result.score,
                -result.chunk_number,
            ),
            reverse=True,
        )

        return results[:top_k]

    @staticmethod
    def build_prompt_context(
        results: list[SearchResult],
    ) -> str:
        """
        Format retrieved excerpts for inclusion in Elise's prompt.
        """

        if not results:
            return (
                "No relevant local documents "
                "were found."
            )

        sections: list[str] = []

        for result in results:
            sections.append(
                "\n".join(
                    [
                        (
                            f"SOURCE: {result.source} "
                            f"(section "
                            f"{result.chunk_number})"
                        ),
                        (
                            "RELEVANCE SCORE: "
                            f"{result.score:.2f}"
                        ),
                        "EXCERPT:",
                        result.text,
                    ]
                )
            )

        return "\n\n---\n\n".join(
            sections
        )