# Passive memory

Elise may classify one minimal semantic proposition after an ordinary response. The classifier proposes; deterministic host policy validates the typed schema, checks user intent and secrets, resolves existing memories, and chooses storage or discard. Classification failures never block conversation and the classifier has no tools.

## Privacy and storage

- `ordinary`: harmless preferences, skills, and project settings. High-confidence useful durable candidates may enter SQLite with `when_relevant` retrieval.
- `personal`: durable context such as important goals and responsibilities. It uses higher save and retrieval thresholds and is not retrieved from generic lexical overlap.
- `sensitive`: deeply personal context. Passive storage is allowed only for durable, useful information about the user at the highest confidence threshold, with no secret match and an unlocked encrypted vault. Retrieval is forced to `explicit_only`. A locked or unavailable vault causes discard, never SQLite fallback.
- `secret`: credentials, authentication material, payment-card numbers, government identifiers, recovery codes, private keys, and similar values are never passively stored. Explicit secret-save requests require a future dedicated high-security flow; Elise is not a password manager.

Retrieval policies are `always_relevant`, `when_relevant`, `explicit_only`, and `never_model_context` (the legacy `never_prompt` value remains supported). Sensitive vault records never enter ordinary model prompts, SQLite search, diagnostics, or summaries.

## Exclusions and overrides

Elise avoids automatic memories from moods, venting, meals, temporary location or schedules, jokes, speculation, hypotheticals, quotations, rewriting, translation, document content, and third-party facts. It stores a concise proposition, not a turn, quote, timestamp, or emotional context.

`don't remember/save/store` suppresses passive review of the current content. `keep this private` can only raise privacy. `this is temporary` forces discard. Existing `/remember`, `/forget`, `/approve-memory`, `/reject-memory`, private-vault commands, and approval-gated suggestions remain available.

Commands:

- `/memory-auto status|on|off`
- `/memory-auto ordinary|personal|sensitive on|off`
- `/memory-recent [limit]` for plaintext-free decision metadata
- `/memory-policy` for effective thresholds

Defaults enable ordinary and personal automatic memory. Sensitive allocation is enabled as a policy but succeeds only while encrypted storage is available and unlocked. Secrets are always disabled. Settings contain booleans only.

## Failure and audit behavior

Malformed output, unknown schema values, tool-like fields, timeouts, locked vaults, and database errors fail closed. Diagnostics contain only decision ID, category, privacy tier, durability, action, reason code, and time—never raw messages or candidate/vault plaintext. The classifier is probabilistic and not infallible; thresholds should be tuned with `passive_memory_eval.json`, prioritizing precision over recall.
