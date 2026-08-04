# Elise 1.1.0-dev4.2 — Duplicate Validation Patch

This patch addresses the second manual-test failure in automatic memory review.

## Fixed

### False duplicate suppression

A model-proposed `duplicate` relation is no longer trusted by itself. The host
now verifies:

- the related memory exists;
- its category matches;
- the texts have substantial token overlap.

An invalid duplicate claim becomes a normal new suggestion.

### Silent duplicate on explicit preferences

When the model returns `should_suggest: false` for an explicit preference, the
host builds a conservative candidate directly from the user's wording. Elise
can therefore report a genuine duplicate instead of staying silent.

## Clean up memory #12 first

Inside Elise:

```text
/memories preference
```

Confirm that `#12` is the malformed entry beginning with `User the user...`.
Then run:

```text
/forget 12
/quit
```

## Install

Replace:

```text
app_1142_final.py              -> app.py
memory_1142_final.py           -> memory.py
memory_review_142_final.py     -> memory_review.py
regression_suite_142_final.py  -> regression_suite.py
```

## Test

```powershell
python -m py_compile app.py
python -m py_compile memory.py
python -m py_compile memory_review.py
python -m py_compile regression_suite.py
python regression_suite.py
```

Expected:

```text
Elise Regression Suite 1.4.2
Summary: 13 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

## Manual retest

```text
I prefer code examples to include brief comments explaining safety checks.
```

Expected: a new pending suggestion, not a duplicate of an unrelated memory.

Then:

```text
I want all project summaries to use a formal academic tone.
```

Expected: a new suggestion after removing the old malformed memory, or a
duplicate notice only if a genuinely equivalent confirmed memory exists.

## Commit

```powershell
git add app.py memory.py memory_review.py regression_suite.py README_ELISE_110_MEMORY_M1_2.md
git status
git commit -m "Validate automatic memory duplicate matches"
git push
```
