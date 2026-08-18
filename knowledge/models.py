from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class KnowledgeSource(str, Enum):
    WIKIPEDIA_EN = "wikipedia_en"


class FreshnessClass(str, Enum):
    STATIC = "static"
    SLOW = "slow"
    CURRENT = "current"


class IngestionStatus(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class KnowledgeDocument:
    id: int | None
    title: str
    normalized_title: str
    source: KnowledgeSource
    source_url: str
    revision_id: str | None
    retrieved_at: str | None
    license_name: str | None
    license_url: str | None
    freshness_class: FreshnessClass
    content_hash: str | None
    status: IngestionStatus
    status_detail: str | None = None


@dataclass(frozen=True)
class KnowledgeSection:
    id: int | None
    document_id: int
    heading: str
    ordinal: int
    text: str
    approximate_token_count: int


@dataclass(frozen=True)
class KnowledgeHit:
    document_id: int
    section_id: int
    title: str
    heading: str
    snippet: str
    score: float
    source: KnowledgeSource
    revision_id: str | None
    retrieved_at: str | None
    freshness_class: FreshnessClass
