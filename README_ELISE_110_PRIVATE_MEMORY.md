# Elise 1.1.0-dev6 — Private Memory Controls

This milestone creates the privacy boundary required before importing deeper personal context.

## Memory tiers

- `ordinary`: normal durable context.
- `personal`: more private context, stored locally in SQLite with explicit retrieval policy.
- `sensitive`: manual-only content in a separate passphrase-encrypted vault.

## Retrieval policies

- `when_relevant`: eligible for normal chat retrieval.
- `explicit_only`: excluded from normal chat; available through explicit search.
- `never_prompt`: stored but never inserted into model context or search results.

Expired memories are excluded from retrieval.

## Sensitive vault

Sensitive content is encrypted with Fernet using a key derived from a passphrase through PBKDF2-SHA256. The passphrase and derived key are never written to disk. The local salt and encrypted vault remain under the already ignored `data/` directory.

This protects data at rest from casual inspection and repository upload. It does not protect an unlocked session or a compromised Windows account.

## Install

Replace or add:

```text
app_160_final.py             -> app.py
memory_160_final.py          -> memory.py
memory_review_160_final.py   -> memory_review.py
profile_import_160_final.py  -> profile_import.py
private_memory_160_final.py  -> private_memory.py
regression_suite_160_final.py -> regression_suite.py
requirements-ci_160.txt      -> requirements-ci.txt
```

Install encryption dependency:

```powershell
python -m pip install cryptography==46.0.4
```

## Test

```powershell
python -m py_compile app.py memory.py memory_review.py profile_import.py private_memory.py regression_suite.py
python regression_suite.py
```

Expected:

```text
Elise Regression Suite 1.6.0
Summary: 15 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

## Commands

```text
/remember-personal <policy> <category> <text>
/memory <id>
/edit-memory <id> <text>
/set-memory-policy <id> <policy>
/expire-memory <id> <YYYY-MM-DD|never>
/memory-why [limit]
/private-memory status
/private-memory unlock
/private-memory lock
/private-memory add <category> <explicit_only|never_prompt> [YYYY-MM-DD|never]
/private-memory list
/private-memory search <query>
/private-memory show <S-id>
/private-memory edit <S-id>
/private-memory policy <S-id> <explicit_only|never_prompt>
/private-memory expire <S-id> <YYYY-MM-DD|never>
/private-memory forget <S-id>
```

Sensitive add/edit/unlock commands use hidden terminal input. Automatic memory review remains ordinary-only. Reviewed profile imports may stage `personal` items, but `sensitive` profile items are blocked and must be entered manually into the encrypted vault.
