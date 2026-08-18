# Elise Offline Knowledge Base

Elise keeps public reference knowledge in `data/knowledge.db`. This database is architecturally and operationally separate from `data/elise.db` and the encrypted personal-memory vault. Wikipedia ingestion never calls a memory API, and memory commands never access knowledge tables.

## Architecture and schema

The `knowledge` package contains typed models, versioned schema setup, FTS retrieval, Wikipedia access, extraction/chunking, ingestion, freshness rules, command formatting, and conversation retrieval. Ordinary SQLite tables hold `knowledge_documents` metadata and ordered `knowledge_sections`; the external-content FTS5 table `knowledge_sections_fts` indexes title, heading, and body while preserving section/document foreign-key relationships. `PRAGMA user_version` is currently 1. An unknown schema version raises an error rather than rebuilding data.

The schema is suitable for larger curated corpora without redesign. Phase 1 intentionally does not include vectors, dumps, scheduling, multilingual content, or an uncontrolled crawler.

## Wikipedia source and ingestion

The source provider constructs HTTPS URLs only for `api.wikimedia.org` using Wikimedia's Core REST page HTML operation. Requests use a descriptive Elise User-Agent, identity encoding, one sequential request, a one-second default delay, bounded response reads, bounded retry/backoff, and `Retry-After` handling. Permanent 400/401/403/404 responses are not retried.

The `vital` seed is the fixed English Wikipedia Vital Articles Level 4 list. Seed extraction accepts only relative `/wiki/` article links, rejects URL authorities, queries, fragments, and non-article namespaces, then normalizes and deduplicates titles. A UTF-8 local title file is also supported. Article-body links are never crawled.

```text
python knowledge_cli.py init
python knowledge_cli.py ingest-wikipedia --seed vital --limit 10
python knowledge_cli.py status
python knowledge_cli.py search "binary search"
python knowledge_cli.py verify
```

Use `--delay` only for development. Completed documents are skipped on a normal rerun, making interrupted ingestion resumable. `--refresh` explicitly refetches completed pages. Each page is committed separately; an individual failure is recorded and the batch continues.

## Extraction and retrieval

The HTML parser excludes scripts, styles, navigation, edit controls, hidden elements, tables/page furniture, and citation-only superscripts. It preserves the lead, headings, paragraphs, and useful list items. Paragraphs are grouped into heading-aware chunks targeting roughly 300–800 words, with sentence-aware splitting and hard character/article/section-count limits. Truncation is recorded in sanitized status metadata.

FTS5 retrieval weights title, heading, and body at 8:3:1 and adds deterministic exact/near title bonuses. It returns structured hits with source, revision, retrieval date, and freshness. Retrieval is local SQLite only: it neither invokes Ollama nor performs network access.

## Freshness and attribution

Freshness is an enum (`static`, `slow`, `current`) assigned by deterministic rules. Present-time questions grounded in a `current` snapshot receive an explicit warning; cached data is not represented as verified current information. Wikipedia title, revision, retrieval timestamp, license name, and license URL remain attached to every document. `/knowledge show` provides bounded metadata/preview, and `/knowledge sources` summarizes attribution and licenses.

## Conversation, privacy, and security boundary

Conservative deterministic intent detection searches knowledge for factual/conceptual requests, while excluding venting, advice/choice prompts, bare conversational follow-ups, and media commands. Recent session context resolves entities before lookup. Retrieved sections enter the model prompt inside explicit `knowledge_reference` delimiters as **untrusted reference data**. Their text cannot change policy, authorize tools, modify permissions, or become personal memory.

Offline retrieval requires no internet once documents are stored. Future work can add vector retrieval, dump import, official documentation providers, refresh scheduling, languages, and 10k/50k corpora behind the same document/section/source boundary.
