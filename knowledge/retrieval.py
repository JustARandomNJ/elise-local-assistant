from __future__ import annotations

import re
from dataclasses import dataclass
from .freshness import freshness_warning
from .models import KnowledgeHit
from .store import KnowledgeStore

FACTUAL_PATTERNS=(r"^\s*(?:what|who|where|when)\s+(?:is|are|was|were)\b",r"^\s*(?:explain|define|describe)\b",r"^\s*(?:tell me about|how does|how do)\b",r"\bwhat is the difference between\b")
EXCLUDE_PATTERNS=(r"\b(?:i feel|i relate to|i just want to vent|should i|pick one|choose one)\b",r"^\s*why\s*\??$",r"\b(?:play|open|show)\b.*\b(?:video|song|spotify|youtube)\b")
CURRENT_PATTERNS=r"\b(?:currently|current|today|now|latest|upcoming|this season|release date|officeholder|ceo)\b"


def should_search_knowledge(query: str) -> bool:
    text=" ".join(query.casefold().split())
    return bool(text and not any(re.search(p,text) for p in EXCLUDE_PATTERNS) and any(re.search(p,text) for p in FACTUAL_PATTERNS))


def asks_for_current_information(query: str) -> bool:
    return bool(re.search(CURRENT_PATTERNS,query,re.I))


def retrieve(store: KnowledgeStore, query: str, limit: int=5) -> list[KnowledgeHit]:
    return store.search(query,limit) if should_search_knowledge(query) else []


def build_grounded_context(hits: list[KnowledgeHit], sections_by_id: dict[int,str], query: str, max_chars: int=8000) -> str:
    """Create a bounded, explicitly untrusted data block for the model."""
    if not hits: return "No offline encyclopedic reference was retrieved."
    blocks=[]; used=0
    for hit in hits:
        text=sections_by_id.get(hit.section_id,hit.snippet)
        warning=freshness_warning(hit.freshness_class,hit.retrieved_at,asks_for_current_information(query))
        block=(f"<knowledge_reference document_id=\"{hit.document_id}\" section_id=\"{hit.section_id}\">\n"
               f"SOURCE: English Wikipedia\nTITLE: {hit.title}\nHEADING: {hit.heading}\nREVISION: {hit.revision_id or 'unknown'}\nRETRIEVED: {hit.retrieved_at or 'unknown'}\n"
               +(f"FRESHNESS WARNING: {warning}\n" if warning else "")+f"UNTRUSTED_REFERENCE_TEXT:\n{text}\n</knowledge_reference>")
        if used+len(block)>max_chars: break
        blocks.append(block); used+=len(block)
    return "\n\n".join(blocks)
