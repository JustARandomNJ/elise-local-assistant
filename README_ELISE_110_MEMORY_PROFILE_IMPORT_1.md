# Elise 1.1.0-dev5.1 — Profile Import Corrective Patch

The first real-project run found two issues. The profile data and staging
design are unchanged.

## Fixed

### Windows SQLite cleanup

`sqlite3.Connection` commits or rolls back when used as a context manager, but
it does not close its file handle. `MemoryStore._connect` now always closes
the connection in `finally`.

The regression suite now renames its temporary database while the store object
is still alive, catching open-handle regressions on Windows.

### Literal `\n` output

Profile preview and staging reports now render actual blank lines instead of
printing the characters `\n`.

## Preview assessment

The starter profile preview succeeded:

- all 14 items validated;
- previewing changed no memories;
- item 4 correctly matched confirmed memory `#7` as a probable duplicate;
- the remaining included items were ready for review.

Do not run `/import-profile` until this patch passes all 14 tests.

## Install

Replace:

```text
app_1151_final.py              -> app.py
memory_1151_final.py           -> memory.py
memory_review_151_final.py     -> memory_review.py
profile_import_151_final.py    -> profile_import.py
regression_suite_151_final.py  -> regression_suite.py
```

The profile JSON is unchanged.

## Test

```powershell
python -m py_compile app.py
python -m py_compile memory.py
python -m py_compile memory_review.py
python -m py_compile profile_import.py
python -m py_compile regression_suite.py
python regression_suite.py
```

Expected:

```text
Elise Regression Suite 1.5.1
Summary: 14 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

## Preview again

```powershell
python app.py
```

Expected:

```text
Elise 1.1.0-dev5.1
```

Then:

```text
/preview-profile profiles/chatgpt_memory_profile.json
```

The output should have normal blank lines and no visible `\n` prefixes. Stop
after previewing and share the output before staging the profile.
