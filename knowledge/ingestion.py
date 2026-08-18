from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
import ssl

from .freshness import classify_freshness
from .models import IngestionStatus, KnowledgeDocument, KnowledgeSection, KnowledgeSource
from .store import KnowledgeStore, normalize_title
from .text_processing import approximate_tokens, content_hash, extract_sections
from .wikipedia import WikipediaClient, extract_seed_titles, validate_title


@dataclass(frozen=True)
class IngestionResult:
    title: str
    status: IngestionStatus
    detail: str
    section_count: int = 0


def sanitized_wikimedia_error(error: BaseException) -> str:
    """Return a stable, credential-free description of a provider failure."""
    if isinstance(error, HTTPError):
        if error.code == 403: return "Wikimedia access denied (API access policy rejection)"
        if error.code == 429: return "Wikimedia rate limit exceeded"
        if error.code == 404: return "article not found"
        if 500 <= error.code <= 599: return f"Wikimedia provider failure (HTTP {error.code})"
        return f"Wikimedia request failed (HTTP {error.code})"
    reason=error.reason if isinstance(error,URLError) else error
    if isinstance(reason,(ssl.SSLError,ssl.SSLCertVerificationError)):
        return "Wikimedia TLS verification failed"
    if isinstance(error,URLError): return "Wikimedia network request failed"
    return "Wikimedia request failed"


def titles_from_file(path: str | Path) -> list[str]:
    unique: dict[str,str]={}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        value=raw.strip()
        if not value or value.startswith("#"): continue
        title=validate_title(value); unique.setdefault(normalize_title(title),title)
    return list(unique.values())


class WikipediaIngestor:
    def __init__(self, store: KnowledgeStore, client: WikipediaClient, progress: Callable[[str],None]=print) -> None:
        self.store=store; self.client=client; self.progress=progress

    def seed_titles(self, seed: str="vital", limit: int=1000) -> list[str]:
        if seed!="vital": raise ValueError("Only the fixed 'vital' seed is supported")
        return extract_seed_titles(self.client.fetch_vital_seed(),limit)

    def ingest(self, titles: Iterable[str], *, limit: int|None=None, refresh: bool=False) -> list[IngestionResult]:
        unique: dict[str,str]={}
        for title in titles:
            safe=validate_title(title); unique.setdefault(normalize_title(safe),safe)
        selected=list(unique.values())[:limit]
        results=[]; total=len(selected)
        for number,title in enumerate(selected,1):
            existing=self.store.document_for_title(title)
            if existing and existing["status"]==IngestionStatus.COMPLETE.value and not refresh:
                result=IngestionResult(title,IngestionStatus.COMPLETE,"already stored",len(self.store.sections_for_document(existing["id"],limit=20)))
            else:
                result=self._ingest_one(title)
            results.append(result)
            suffix=f" — {result.section_count} sections" if result.section_count else ""
            self.progress(f"[{number}/{total}] {title} — {result.detail}{suffix}")
        return results

    def _ingest_one(self,title: str) -> IngestionResult:
        pending=self._document(title,status=IngestionStatus.PENDING)
        document_id,_=self.store.upsert_document(pending)
        try:
            page=self.client.fetch_page(title)
            extracted,truncated=extract_sections(page.html)
            if not extracted:
                raise ValueError("no useful article prose")
            freshness=classify_freshness(page.title,[s.heading for s in extracted])
            complete=KnowledgeDocument(document_id,page.title,normalize_title(page.title),KnowledgeSource.WIKIPEDIA_EN,page.source_url,page.revision_id,page.retrieved_at,page.license_name,page.license_url,freshness,content_hash(extracted),IngestionStatus.COMPLETE,"truncated to safety bounds" if truncated else None)
            document_id,unchanged=self.store.upsert_document(complete)
            if unchanged:
                return IngestionResult(page.title,IngestionStatus.COMPLETE,"unchanged revision",len(self.store.sections_for_document(document_id,20)))
            sections=[KnowledgeSection(None,document_id,s.heading,i,s.text,approximate_tokens(s.text)) for i,s in enumerate(extracted)]
            self.store.replace_sections(document_id,sections)
            return IngestionResult(page.title,IngestionStatus.COMPLETE,"stored"+(" (truncated)" if truncated else ""),len(sections))
        except (HTTPError,URLError,ssl.SSLError) as error:
            detail=sanitized_wikimedia_error(error)
            status=IngestionStatus.SKIPPED if isinstance(error,HTTPError) and error.code==404 else IngestionStatus.FAILED
        except Exception as error:
            detail=str(error)[:160].replace("\n"," "); status=IngestionStatus.FAILED
        failed=self._document(title,status=status,detail=detail)
        self.store.upsert_document(failed)
        return IngestionResult(title,status,f"{status.value} — {detail}")

    @staticmethod
    def _document(title: str, status: IngestionStatus, detail: str|None=None) -> KnowledgeDocument:
        from .freshness import classify_freshness
        from urllib.parse import quote
        return KnowledgeDocument(None,title,normalize_title(title),KnowledgeSource.WIKIPEDIA_EN,f"https://en.wikipedia.org/wiki/{quote(title.replace(' ','_'),safe='')}",None,None,"Creative Commons Attribution-ShareAlike 4.0","https://creativecommons.org/licenses/by-sa/4.0/",classify_freshness(title),None,status,detail)
