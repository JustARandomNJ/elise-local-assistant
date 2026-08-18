from __future__ import annotations

import argparse
from pathlib import Path
import ssl
from urllib.error import HTTPError, URLError

from knowledge.commands import handle_knowledge_command
from knowledge.ingestion import WikipediaIngestor, sanitized_wikimedia_error, titles_from_file
from knowledge.models import IngestionStatus
from knowledge.store import KnowledgeStore
from knowledge.wikipedia import WikipediaClient

DEFAULT_DB=Path(__file__).resolve().parent/"data"/"knowledge.db"


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(description="Administer Elise's separate offline public knowledge database")
    root.add_argument("--database",type=Path,default=DEFAULT_DB)
    commands=root.add_subparsers(dest="command",required=True)
    commands.add_parser("init"); commands.add_parser("status"); commands.add_parser("verify")
    search=commands.add_parser("search"); search.add_argument("query")
    ingest=commands.add_parser("ingest-wikipedia")
    source=ingest.add_mutually_exclusive_group(required=True); source.add_argument("--seed",choices=["vital"]); source.add_argument("--titles",type=Path)
    ingest.add_argument("--limit",type=int); ingest.add_argument("--delay",type=float,default=1.0); ingest.add_argument("--refresh",action="store_true")
    return root


def main(argv: list[str]|None=None) -> int:
    args=parser().parse_args(argv); store=KnowledgeStore(args.database)
    if args.command=="init": print(f"Initialized knowledge database: {store.path}"); return 0
    if args.command=="status": print(handle_knowledge_command("/knowledge status",store)); return 0
    if args.command=="search": print(handle_knowledge_command(f"/knowledge search {args.query}",store)); return 0
    if args.command=="verify":
        issues=store.verify(); print("Knowledge database verified." if not issues else "\n".join(issues)); return 0 if not issues else 1
    if args.limit is not None and args.limit<1: raise SystemExit("--limit must be positive")
    client=WikipediaClient(delay_seconds=args.delay); ingestor=WikipediaIngestor(store,client)
    try:
        titles=titles_from_file(args.titles) if args.titles else ingestor.seed_titles(args.seed,args.limit or 1000)
        results=ingestor.ingest(titles,limit=args.limit,refresh=args.refresh)
    except (HTTPError,URLError,ssl.SSLError) as error:
        print(f"Error: {sanitized_wikimedia_error(error)}")
        return 1
    return 1 if any(result.status==IngestionStatus.FAILED for result in results) else 0


if __name__=="__main__": raise SystemExit(main())
