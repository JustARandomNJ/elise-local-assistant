from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Iterator

from .models import FreshnessClass, IngestionStatus, KnowledgeDocument, KnowledgeHit, KnowledgeSection, KnowledgeSource
from .schema import initialize_schema


def normalize_title(title: str) -> str:
    return " ".join(title.replace("_", " ").split()).casefold()


class KnowledgeStore:
    def __init__(self, path: str | Path = "data/knowledge.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            initialize_schema(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def upsert_document(self, document: KnowledgeDocument) -> tuple[int, bool]:
        """Return (id, unchanged_complete_revision)."""
        with self.connect() as db:
            old = db.execute("SELECT id, revision_id, status FROM knowledge_documents WHERE source=? AND normalized_title=?", (document.source.value, document.normalized_title)).fetchone()
            unchanged = bool(old and old["revision_id"] == document.revision_id and old["status"] == IngestionStatus.COMPLETE.value)
            db.execute("""INSERT INTO knowledge_documents
                (title,normalized_title,source,source_url,revision_id,retrieved_at,license_name,license_url,freshness_class,content_hash,status,status_detail)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,normalized_title) DO UPDATE SET
                title=excluded.title,source_url=excluded.source_url,revision_id=excluded.revision_id,retrieved_at=excluded.retrieved_at,
                license_name=excluded.license_name,license_url=excluded.license_url,freshness_class=excluded.freshness_class,
                content_hash=excluded.content_hash,status=excluded.status,status_detail=excluded.status_detail""",
                (document.title,document.normalized_title,document.source.value,document.source_url,document.revision_id,document.retrieved_at,
                 document.license_name,document.license_url,document.freshness_class.value,document.content_hash,document.status.value,document.status_detail))
            row = db.execute("SELECT id FROM knowledge_documents WHERE source=? AND normalized_title=?", (document.source.value,document.normalized_title)).fetchone()
            db.commit()
            return int(row[0]), unchanged

    def replace_sections(self, document_id: int, sections: Iterable[KnowledgeSection]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM knowledge_sections WHERE document_id=?", (document_id,))
            db.executemany("INSERT INTO knowledge_sections(document_id,heading,ordinal,text,approximate_token_count) VALUES(?,?,?,?,?)",
                ((document_id,s.heading,s.ordinal,s.text,s.approximate_token_count) for s in sections))
            db.commit()

    def get_document(self, document_id: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()

    def document_for_title(self, title: str, source: KnowledgeSource = KnowledgeSource.WIKIPEDIA_EN) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM knowledge_documents WHERE source=? AND normalized_title=?", (source.value, normalize_title(title))).fetchone()

    def search(self, query: str, limit: int = 5) -> list[KnowledgeHit]:
        terms = re.findall(r"[\w]+", query, re.UNICODE)[:20]
        if not terms or limit < 1:
            return []
        fts_query = " AND ".join(f'"{term.replace(chr(34), chr(34)*2)}"' for term in terms)
        normalized = normalize_title(query)
        with self.connect() as db:
            try:
                rows = db.execute("""SELECT d.id document_id,s.id section_id,d.title,s.heading,
                    snippet(knowledge_sections_fts,2,'[',']',' … ',24) snippet,
                    bm25(knowledge_sections_fts,8.0,3.0,1.0) rank,d.source,d.revision_id,d.retrieved_at,d.freshness_class,
                    CASE WHEN d.normalized_title=? THEN 100 WHEN d.normalized_title LIKE ? THEN 30 ELSE 0 END title_bonus
                    FROM knowledge_sections_fts
                    JOIN knowledge_sections s ON s.id=knowledge_sections_fts.rowid
                    JOIN knowledge_documents d ON d.id=s.document_id
                    WHERE knowledge_sections_fts MATCH ? AND d.status='complete'
                    ORDER BY title_bonus DESC, rank ASC LIMIT ?""", (normalized, f"%{normalized}%", fts_query, min(limit, 50))).fetchall()
            except sqlite3.OperationalError:
                return []
        return [KnowledgeHit(int(r["document_id"]),int(r["section_id"]),r["title"],r["heading"],r["snippet"],float(r["title_bonus"])-float(r["rank"]),KnowledgeSource(r["source"]),r["revision_id"],r["retrieved_at"],FreshnessClass(r["freshness_class"])) for r in rows]

    def sections_for_document(self, document_id: int, limit: int = 5) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute("SELECT * FROM knowledge_sections WHERE document_id=? ORDER BY ordinal LIMIT ?", (document_id,min(limit,20))).fetchall()

    def section_texts(self, section_ids: Iterable[int]) -> dict[int, str]:
        ids=[int(value) for value in section_ids][:50]
        if not ids: return {}
        placeholders=",".join("?" for _ in ids)
        with self.connect() as db:
            return {int(row["id"]): row["text"] for row in db.execute(f"SELECT id,text FROM knowledge_sections WHERE id IN ({placeholders})",ids)}

    def status(self) -> dict[str, object]:
        with self.connect() as db:
            documents = db.execute("SELECT count(*) FROM knowledge_documents WHERE status='complete'").fetchone()[0]
            sections = db.execute("SELECT count(*) FROM knowledge_sections").fetchone()[0]
            last = db.execute("SELECT max(retrieved_at) FROM knowledge_documents WHERE status='complete'").fetchone()[0]
        return {"documents": documents, "sections": sections, "last_ingestion": last, "database_size": self.path.stat().st_size if self.path.exists() else 0}

    def sources(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute("SELECT source,license_name,license_url,count(*) count FROM knowledge_documents WHERE status='complete' GROUP BY source,license_name,license_url ORDER BY source").fetchall()

    def verify(self) -> list[str]:
        with self.connect() as db:
            issues = [row[0] for row in db.execute("PRAGMA integrity_check") if row[0] != "ok"]
            issues += [f"foreign key: {tuple(row)}" for row in db.execute("PRAGMA foreign_key_check")]
            try: db.execute("INSERT INTO knowledge_sections_fts(knowledge_sections_fts) VALUES('integrity-check')")
            except sqlite3.DatabaseError as error: issues.append(f"FTS: {error}")
            return issues
