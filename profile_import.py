from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from memory import ALLOWED_CATEGORIES, MemoryStore
from memory_review import (
    MemoryReviewEngine,
    MemoryReviewOutcome,
)


PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 256_000
MAX_PROFILE_ITEMS = 100
MAX_PROFILE_NAME_CHARS = 120
MAX_PROFILE_DESCRIPTION_CHARS = 1_000
MAX_PROFILE_NOTE_CHARS = 500
MIN_PROFILE_CONFIDENCE = 0.80


@dataclass(frozen=True)
class ProfileMemoryItem:
    index: int
    content: str
    category: str
    confidence: float
    include: bool
    note: str


@dataclass(frozen=True)
class ProfileDocument:
    path: Path
    profile_name: str
    description: str
    schema_version: int
    sha256: str
    items: tuple[ProfileMemoryItem, ...]


@dataclass(frozen=True)
class ProfileItemResult:
    index: int
    content: str
    category: str
    status: str
    suggestion_id: int | None = None
    related_memory_id: int | None = None
    relation: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class ProfileImportReport:
    profile_name: str
    profile_hash: str
    total_items: int
    included_items: int
    staged: int
    duplicates: int
    blocked: int
    skipped: int
    results: tuple[ProfileItemResult, ...]


RelationClassifier = Callable[
    [
        ProfileMemoryItem,
        list[dict[str, Any]],
    ],
    dict[str, Any],
]


def _normalize_line(
    value: Any,
) -> str:
    return " ".join(
        str(
            value
        ).strip().split()
    )


def _validate_profile_item(
    raw_item: Any,
    *,
    index: int,
) -> ProfileMemoryItem:
    if not isinstance(
        raw_item,
        dict,
    ):
        raise ValueError(
            f"Profile item {index} must be a JSON object."
        )

    content = _normalize_line(
        raw_item.get(
            "content",
            "",
        )
    )
    category = _normalize_line(
        raw_item.get(
            "category",
            "",
        )
    ).lower()
    note = _normalize_line(
        raw_item.get(
            "note",
            "",
        )
    )
    include = raw_item.get(
        "include",
        True,
    )

    if not isinstance(
        include,
        bool,
    ):
        raise ValueError(
            f"Profile item {index} include must be true or false."
        )

    if not content:
        raise ValueError(
            f"Profile item {index} content cannot be empty."
        )

    if "\n" in content:
        raise ValueError(
            f"Profile item {index} content must be one line."
        )

    if len(
        content
    ) > 500:
        raise ValueError(
            f"Profile item {index} content exceeds 500 characters."
        )

    if category not in ALLOWED_CATEGORIES:
        raise ValueError(
            f"Profile item {index} has invalid category. "
            "Use: "
            + ", ".join(
                sorted(
                    ALLOWED_CATEGORIES
                )
            )
        )

    try:
        confidence = float(
            raw_item.get(
                "confidence",
                0.95,
            )
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            f"Profile item {index} confidence must be numeric."
        ) from error

    if not (
        MIN_PROFILE_CONFIDENCE
        <= confidence
        <= 1.0
    ):
        raise ValueError(
            f"Profile item {index} confidence must be between "
            f"{MIN_PROFILE_CONFIDENCE:.2f} and 1.00."
        )

    if len(
        note
    ) > MAX_PROFILE_NOTE_CHARS:
        raise ValueError(
            f"Profile item {index} note exceeds "
            f"{MAX_PROFILE_NOTE_CHARS} characters."
        )

    return ProfileMemoryItem(
        index=index,
        content=content,
        category=category,
        confidence=confidence,
        include=include,
        note=note,
    )


def load_profile(
    profile_path: str | Path,
) -> ProfileDocument:
    path = Path(
        profile_path
    ).resolve()

    if not path.exists():
        raise FileNotFoundError(
            f"Profile file not found: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Profile path is not a file: {path}"
        )

    if path.suffix.lower() != ".json":
        raise ValueError(
            "Profile imports require a .json file."
        )

    raw_bytes = path.read_bytes()

    if len(
        raw_bytes
    ) > MAX_PROFILE_BYTES:
        raise ValueError(
            f"Profile exceeds {MAX_PROFILE_BYTES} bytes."
        )

    profile_hash = hashlib.sha256(
        raw_bytes
    ).hexdigest()

    try:
        payload = json.loads(
            raw_bytes.decode(
                "utf-8"
            )
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            f"Profile is not valid UTF-8 JSON: {error}"
        ) from error

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Profile JSON root must be an object."
        )

    try:
        schema_version = int(
            payload.get(
                "schema_version",
                0,
            )
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "Profile schema_version must be an integer."
        ) from error

    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported profile schema version. "
            f"Expected {PROFILE_SCHEMA_VERSION}."
        )

    profile_name = _normalize_line(
        payload.get(
            "profile_name",
            "",
        )
    )
    description = _normalize_line(
        payload.get(
            "description",
            "",
        )
    )

    if not profile_name:
        raise ValueError(
            "Profile profile_name cannot be empty."
        )

    if len(
        profile_name
    ) > MAX_PROFILE_NAME_CHARS:
        raise ValueError(
            "Profile profile_name is too long."
        )

    if len(
        description
    ) > MAX_PROFILE_DESCRIPTION_CHARS:
        raise ValueError(
            "Profile description is too long."
        )

    raw_items = payload.get(
        "memories"
    )

    if not isinstance(
        raw_items,
        list,
    ):
        raise ValueError(
            'Profile JSON must contain a "memories" list.'
        )

    if not raw_items:
        raise ValueError(
            "Profile memories list cannot be empty."
        )

    if len(
        raw_items
    ) > MAX_PROFILE_ITEMS:
        raise ValueError(
            f"Profile exceeds {MAX_PROFILE_ITEMS} memory items."
        )

    items: list[
        ProfileMemoryItem
    ] = []
    seen: set[
        tuple[
            str,
            str,
        ]
    ] = set()

    for offset, raw_item in enumerate(
        raw_items,
        start=1,
    ):
        item = _validate_profile_item(
            raw_item,
            index=offset,
        )
        duplicate_key = (
            item.category,
            item.content.casefold(),
        )

        if duplicate_key in seen:
            raise ValueError(
                f"Profile item {offset} duplicates an earlier item."
            )

        seen.add(
            duplicate_key
        )
        items.append(
            item
        )

    return ProfileDocument(
        path=path,
        profile_name=profile_name,
        description=description,
        schema_version=schema_version,
        sha256=profile_hash,
        items=tuple(
            items
        ),
    )


def build_profile_relation_messages(
    *,
    item: ProfileMemoryItem,
    existing_memories: Sequence[
        dict[str, Any]
    ],
) -> list[dict[str, str]]:
    same_category = [
        memory
        for memory in existing_memories
        if str(
            memory.get(
                "category",
                "",
            )
        )
        == item.category
    ]

    existing_json = json.dumps(
        same_category,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    system_message = """
You classify one proposed personal-memory item against confirmed memories.

Return exactly one JSON object with:
{
  "relation": "new" | "duplicate" | "conflict",
  "related_memory_id": integer | null,
  "reason": "brief explanation"
}

Rules:
- Do not rewrite the proposed memory.
- duplicate means the same durable meaning is already stored.
- conflict means the same durable attribute is represented but the values are
  materially incompatible and the proposed item should replace that memory if
  approved.
- new means neither duplicate nor conflict.
- Use only a memory ID shown in the supplied list.
- Use null for new.
- Be conservative. Topic overlap alone is not a duplicate or conflict.
- Output JSON only.
""".strip()

    user_message = (
        "PROPOSED_MEMORY:\n"
        + json.dumps(
            {
                "content": item.content,
                "category": item.category,
            },
            ensure_ascii=False,
        )
        + "\n\nCONFIRMED_MEMORIES_SAME_CATEGORY:\n"
        + existing_json
    )

    return [
        {
            "role": "system",
            "content": system_message,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]


def parse_profile_relation(
    raw_value: Any,
) -> dict[str, Any]:
    value = raw_value

    if isinstance(
        raw_value,
        str,
    ):
        cleaned = raw_value.strip()

        try:
            value = json.loads(
                cleaned
            )
        except json.JSONDecodeError:
            start = cleaned.find(
                "{"
            )
            end = cleaned.rfind(
                "}"
            )

            if (
                start < 0
                or end <= start
            ):
                return {
                    "relation": "new",
                    "related_memory_id": None,
                    "reason": (
                        "No valid relation classification was returned."
                    ),
                }

            try:
                value = json.loads(
                    cleaned[
                        start:
                        end
                        + 1
                    ]
                )
            except json.JSONDecodeError:
                return {
                    "relation": "new",
                    "related_memory_id": None,
                    "reason": (
                        "No valid relation classification was returned."
                    ),
                }

    if not isinstance(
        value,
        dict,
    ):
        return {
            "relation": "new",
            "related_memory_id": None,
            "reason": (
                "No valid relation classification was returned."
            ),
        }

    relation = _normalize_line(
        value.get(
            "relation",
            "new",
        )
    ).lower()

    if relation not in {
        "new",
        "duplicate",
        "conflict",
    }:
        relation = "new"

    raw_related = value.get(
        "related_memory_id"
    )

    try:
        related_memory_id = (
            None
            if raw_related
            is None
            else int(
                raw_related
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        related_memory_id = None

    reason = _normalize_line(
        value.get(
            "reason",
            "",
        )
    )

    if not reason:
        reason = (
            "Imported profile item was compared with confirmed memory."
        )

    return {
        "relation": relation,
        "related_memory_id": related_memory_id,
        "reason": reason[
            :350
        ],
    }


class ProfileImportEngine:
    """
    Stage profile memories through the existing approval-gated suggestion flow.

    Confirmed memory is never written by this engine.
    """

    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        classify_relation: RelationClassifier
        | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.classify_relation = (
            classify_relation
        )

    @staticmethod
    def _memory_dicts(
        rows: Sequence[
            Any
        ],
    ) -> list[dict[str, Any]]:
        return (
            MemoryReviewEngine
            ._memory_dicts(
                rows
            )
        )

    def preview(
        self,
        profile: ProfileDocument,
    ) -> tuple[
        ProfileItemResult,
        ...,
    ]:
        existing_memories = (
            self._memory_dicts(
                self.memory_store
                .list_all()
            )
        )
        reviewer = MemoryReviewEngine(
            memory_store=(
                self.memory_store
            ),
            extract_candidate=(
                lambda _text, _existing: {
                    "should_suggest": False,
                }
            ),
        )
        results: list[
            ProfileItemResult
        ] = []

        for item in profile.items:
            if not item.include:
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=item.content,
                        category=item.category,
                        status="excluded",
                        detail=(
                            "include is false"
                        ),
                    )
                )
                continue

            source_text = (
                "I am reviewing a durable profile memory candidate: "
                + item.content
            )
            prefilter_reason = (
                reviewer._prefilter(
                    source_text
                )
            )

            if prefilter_reason is not None:
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=item.content,
                        category=item.category,
                        status="blocked",
                        detail=(
                            prefilter_reason
                        ),
                    )
                )
                continue

            normalized = (
                reviewer
                ._normalize_candidate_content(
                    item.content
                )
            )
            duplicate_id = (
                reviewer
                ._find_deterministic_duplicate(
                    normalized,
                    item.category,
                    existing_memories,
                )
            )

            if duplicate_id is not None:
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=normalized,
                        category=item.category,
                        status="probable_duplicate",
                        related_memory_id=(
                            duplicate_id
                        ),
                        relation="duplicate",
                        detail=(
                            "Host similarity check found a likely existing memory."
                        ),
                    )
                )
                continue

            results.append(
                ProfileItemResult(
                    index=item.index,
                    content=normalized,
                    category=item.category,
                    status="ready",
                    detail=(
                        "Ready to stage for approval."
                    ),
                )
            )

        return tuple(
            results
        )

    def stage(
        self,
        profile: ProfileDocument,
    ) -> ProfileImportReport:
        existing_rows = (
            self.memory_store
            .list_all()
        )
        existing_memories = (
            self._memory_dicts(
                existing_rows
            )
        )

        results: list[
            ProfileItemResult
        ] = []
        staged = 0
        duplicates = 0
        blocked = 0
        skipped = 0
        included_items = 0

        for item in profile.items:
            if not item.include:
                skipped += 1
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=item.content,
                        category=item.category,
                        status="excluded",
                        detail=(
                            "include is false"
                        ),
                    )
                )
                continue

            included_items += 1

            classification = {
                "relation": "new",
                "related_memory_id": None,
                "reason": (
                    "Imported from reviewed profile."
                ),
            }

            if self.classify_relation is not None:
                try:
                    classification = (
                        parse_profile_relation(
                            self.classify_relation(
                                item,
                                existing_memories,
                            )
                        )
                    )
                except Exception:
                    classification = {
                        "relation": "new",
                        "related_memory_id": None,
                        "reason": (
                            "Relation classification failed; staged conservatively as new."
                        ),
                    }

            relation = str(
                classification.get(
                    "relation",
                    "new",
                )
            )
            related_memory_id = (
                classification.get(
                    "related_memory_id"
                )
            )
            classification_reason = (
                _normalize_line(
                    classification.get(
                        "reason",
                        "",
                    )
                )
            )
            provenance_reason = (
                f"Profile '{profile.profile_name}' item {item.index}. "
            )

            if item.note:
                provenance_reason += (
                    item.note
                    + " "
                )

            provenance_reason += (
                classification_reason
            )
            provenance_reason = (
                provenance_reason[
                    :500
                ]
            )

            candidate_payload = {
                "should_suggest": True,
                "content": item.content,
                "category": item.category,
                "confidence": (
                    item.confidence
                ),
                "reason": (
                    provenance_reason
                ),
                "relation": relation,
                "related_memory_id": (
                    related_memory_id
                ),
            }

            reviewer = MemoryReviewEngine(
                memory_store=(
                    self.memory_store
                ),
                extract_candidate=(
                    lambda _text, _existing, payload=candidate_payload: payload
                ),
            )
            source_text = (
                "I am reviewing profile "
                f"{profile.sha256[:12]} "
                f"item {item.index} as a durable memory candidate: "
                + item.content
            )
            outcome = reviewer.review(
                source_text
            )

            if outcome.status == "suggested":
                staged += 1
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=(
                            outcome.content
                            or item.content
                        ),
                        category=item.category,
                        status="staged",
                        suggestion_id=(
                            outcome.suggestion_id
                        ),
                        related_memory_id=(
                            outcome.related_memory_id
                        ),
                        relation=(
                            outcome.relation
                        ),
                        detail=(
                            outcome.reason
                            or ""
                        ),
                    )
                )
            elif outcome.status in {
                "duplicate",
                "duplicate_pending",
            }:
                duplicates += 1
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=(
                            outcome.content
                            or item.content
                        ),
                        category=item.category,
                        status=(
                            outcome.status
                        ),
                        related_memory_id=(
                            outcome.related_memory_id
                        ),
                        relation=(
                            outcome.relation
                        ),
                        detail=(
                            outcome.reason
                            or outcome.detail
                            or ""
                        ),
                    )
                )
            elif outcome.status in {
                "rejected",
                "skipped",
            }:
                blocked += 1
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=item.content,
                        category=item.category,
                        status=(
                            outcome.status
                        ),
                        detail=(
                            outcome.detail
                            or "blocked"
                        ),
                    )
                )
            else:
                skipped += 1
                results.append(
                    ProfileItemResult(
                        index=item.index,
                        content=item.content,
                        category=item.category,
                        status=(
                            outcome.status
                        ),
                        detail=(
                            outcome.detail
                            or ""
                        ),
                    )
                )

        return ProfileImportReport(
            profile_name=(
                profile.profile_name
            ),
            profile_hash=(
                profile.sha256
            ),
            total_items=len(
                profile.items
            ),
            included_items=(
                included_items
            ),
            staged=staged,
            duplicates=duplicates,
            blocked=blocked,
            skipped=skipped,
            results=tuple(
                results
            ),
        )
