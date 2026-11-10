# Elise

Elise is a private, offline-first AI assistant that runs in the terminal. It uses a local language model (`qwen3.5:4b` through Ollama) and adds structured long-term memory, local document search, an offline knowledge base, confirmation-gated file tools, and deterministic workflows. Everything is stored on your machine. Internet access is off by default and must be turned on explicitly.

The design rule throughout is that **the model proposes, the host decides.** The model never chooses arbitrary tools, writes files on its own, or changes permissions. Deterministic host code validates every action, every write needs your exact confirmation, and failures fail closed.

## Features

- **Local chat** with `qwen3.5:4b` through Ollama, with no cloud LLM or paid API.
- **Structured memory.** SQLite-backed memories with categories, retrieval policies, expiry dates, and an explanation of why each memory was used (`/memory-why`).
- **Three privacy tiers.** `ordinary`, `personal`, and `sensitive`. Sensitive memories live in a separate encrypted vault (Fernet, with a PBKDF2-SHA256 key derived from a passphrase that is never written to disk).
- **Passive and approval-gated memory.** Elise can propose one concise memory after a response. Host policy validates it, checks it for secrets, and either stores or discards it. Credentials, card numbers, and government IDs are never stored.
- **Profile import.** Review-staged migration of a JSON profile into memory, with duplicate detection and a preview before anything is saved.
- **Local document search.** Indexes text files in `documents/` and returns attributed results.
- **Offline knowledge base.** A separate SQLite FTS5 database of Wikipedia articles, ingested politely and with resume support, with freshness labels and license attribution on every document.
- **Safe file tools.** Reads are automatic. Creating, replacing, appending, and making directories all require a preview and an exact `yes`. Path traversal is rejected, and every action is recorded in a redacted audit log.
- **Workflows.** Host-defined templates for document summaries, action-item extraction, and two-document comparison. Each template follows a fixed eight-step plan with a confirmation pause, survives a restart, and verifies hashes before writing.
- **Optional internet.** Web search (Bing RSS with DuckDuckGo fallbacks) and single-page fetches, with URL filtering. Fetched content is treated as untrusted data.
- **Media.** Spotify login, device selection, and playback, plus YouTube "play the latest video from a creator," shown in a dedicated display window.
- **Natural-language commands.** High-confidence phrases such as "search my documents for X" map to the same slash commands, so they go through the same permission and confirmation checks.

## Architecture

| Area | Main files |
|---|---|
| App, chat loop, command handling | `app.py`, `command_router.py`, `natural_command_intents.py`, `conversational_intent.py` |
| Memory | `memory.py`, `memory_review.py`, `passive_memory.py`, `private_memory.py`, `profile_import.py` |
| Documents and knowledge | `document_search.py`, `knowledge/` package, `knowledge_cli.py` |
| Tools and safety | `tools.py`, `audit.py`, `internet.py` |
| Workflows | `workflow.py`, `workflow_execution.py` |
| Media | `spotify_media.py`, `media_*.py`, `music_console.py`, `terminal_music_display.py` |
| Testing | `regression_suite.py`, `test_*.py`, `.github/workflows/elise-regression.yml` |

Runtime data is kept in separate local databases under `data/`: memory in `elise.db`, the tool log in `tool_audit.db`, workflow state in `workflows.db`, and public reference knowledge in `knowledge.db`. The knowledge base and personal memory are fully separate and never read from each other.

## Privacy

Memories, documents, settings, tokens, profiles, and audit logs all live under paths that git ignores (`data/`, `documents/`, `profiles/`, `*.db`, `.env`, and so on). Nothing personal belongs in the repository.

The encrypted vault protects data at rest from casual inspection and from accidental upload. It does not protect an unlocked session or a compromised user account.

## Setup

Requires Python 3.13 and [Ollama](https://ollama.com).

```powershell
ollama pull qwen3.5:4b

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python app.py
```

Type `/help` inside Elise to see all commands.

## Common commands

```text
/remember preference I prefer concise answers
/memories
/search-memories <query>
/forget <id>

/private-memory unlock
/private-memory add <category> explicit_only

/search <query>                 # local documents
/reindex

/knowledge search <query>       # offline knowledge base

/new-summary-workflow documents/notes.md documents/summary.md
/run-workflow <id>

/internet on
/web-search <query>
```

Build the offline knowledge base:

```powershell
python knowledge_cli.py init
python knowledge_cli.py ingest-wikipedia --seed vital --limit 10
python knowledge_cli.py search "binary search"
```

## Testing

The regression suite never touches your real data. It creates temporary databases, documents, settings, and audit logs and deletes them after each check. By default it makes no web requests and never prompts the model.

```powershell
python regression_suite.py
python -m pytest
```

The suite covers memory, privacy tiers and the encrypted vault, passive memory, profile import, document search, file tool safeguards, audit redaction, internet settings, workflows, media, and app routing. GitHub Actions runs it on Windows for every push to `main` or `feature/**` and for every pull request. Optional live checks are available locally:

```powershell
.\run_regression.ps1 -LiveInternet -LiveModel
```

More detail: [Regression testing](REGRESSION_TESTING.md) · [CI setup](GITHUB_ACTIONS_SETUP.md) · [Passive memory](PASSIVE_MEMORY.md) · [Offline knowledge](OFFLINE_KNOWLEDGE.md) · [Natural-language commands](NATURAL_LANGUAGE_COMMANDS.md)

## Status

Version 1.1.0-dev6, a personal project under active development.
