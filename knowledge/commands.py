from __future__ import annotations

from .store import KnowledgeStore


def handle_knowledge_command(command: str, store: KnowledgeStore) -> str:
    parts=command.strip().split(maxsplit=2)
    if len(parts)<2 or parts[1]=="status":
        status=store.status()
        return ("Knowledge base:\n"
                f"{status['documents']:,} documents\n{status['sections']:,} sections\n"
                f"Source: English Wikipedia\nLast ingestion: {status['last_ingestion'] or 'never'}\n"
                f"Database size: {status['database_size']:,} bytes")
    action=parts[1].casefold()
    argument=parts[2].strip() if len(parts)>2 else ""
    if action=="search":
        if not argument: return "Usage: /knowledge search <query>"
        hits=store.search(argument,10)
        return "No matching offline knowledge found." if not hits else "\n".join(f"{i}. {hit.title} — {hit.heading}" for i,hit in enumerate(hits,1))
    if action=="show":
        if not argument.isdigit(): return "Usage: /knowledge show <document-id>"
        document=store.get_document(int(argument))
        if not document: return "Knowledge document not found."
        sections=store.sections_for_document(int(argument),3)
        preview="\n\n".join(f"{s['heading']}: {s['text'][:500]}" for s in sections)[:1800]
        return (f"{document['title']} (document {document['id']})\nSource: {document['source']}\nRevision: {document['revision_id'] or 'unknown'}\n"
                f"Retrieved: {document['retrieved_at'] or 'unknown'}\nFreshness: {document['freshness_class']}\nStatus: {document['status']}\n\nBounded preview:\n{preview}")
    if action=="sources":
        rows=store.sources()
        return "No sources stored." if not rows else "\n".join(f"{r['source']}: {r['count']} documents — {r['license_name'] or 'license unknown'} ({r['license_url'] or 'URL unavailable'})" for r in rows)
    return "Usage: /knowledge [status|search <query>|show <document-id>|sources]"
