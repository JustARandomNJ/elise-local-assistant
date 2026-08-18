from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise RuntimeError(f"Unsupported knowledge database schema version {version}; expected {SCHEMA_VERSION}")
    if version == SCHEMA_VERSION:
        return
    connection.executescript("""
        CREATE TABLE knowledge_documents (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            normalized_title TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('wikipedia_en')),
            source_url TEXT NOT NULL,
            revision_id TEXT,
            retrieved_at TEXT,
            license_name TEXT,
            license_url TEXT,
            freshness_class TEXT NOT NULL CHECK(freshness_class IN ('static','slow','current')),
            content_hash TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending','complete','failed','skipped')),
            status_detail TEXT,
            UNIQUE(source, normalized_title)
        );
        CREATE TABLE knowledge_sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            heading TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL,
            approximate_token_count INTEGER NOT NULL CHECK(approximate_token_count >= 0),
            UNIQUE(document_id, ordinal)
        );
        CREATE INDEX knowledge_documents_status_idx ON knowledge_documents(status);
        CREATE INDEX knowledge_sections_document_idx ON knowledge_sections(document_id, ordinal);
        CREATE VIRTUAL TABLE knowledge_sections_fts USING fts5(
            title, heading, text,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER knowledge_sections_ai AFTER INSERT ON knowledge_sections BEGIN
          INSERT INTO knowledge_sections_fts(rowid,title,heading,text)
          SELECT new.id,d.title,new.heading,new.text FROM knowledge_documents d WHERE d.id=new.document_id;
        END;
        CREATE TRIGGER knowledge_sections_ad AFTER DELETE ON knowledge_sections BEGIN
          DELETE FROM knowledge_sections_fts WHERE rowid=old.id;
        END;
        CREATE TRIGGER knowledge_sections_au AFTER UPDATE ON knowledge_sections BEGIN
          DELETE FROM knowledge_sections_fts WHERE rowid=old.id;
          INSERT INTO knowledge_sections_fts(rowid,title,heading,text)
          SELECT new.id,d.title,new.heading,new.text FROM knowledge_documents d WHERE d.id=new.document_id;
        END;
        PRAGMA user_version = 1;
    """)
    connection.commit()
