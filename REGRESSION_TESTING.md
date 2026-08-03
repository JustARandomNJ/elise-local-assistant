# Elise Regression Harness

This adds a standard-library-only, non-destructive regression suite to Elise.

## Safety

The default suite does **not** modify your real:

- `data/elise.db`
- `data/tool_audit.db`
- `documents/`
- internet settings
- project source files

Memory databases, documents, settings, audit logs, and filesystem tool roots are
created in temporary directories and deleted after each check.

Default checks make no live web request and do not prompt Ollama.

## Install

Create the feature branch from the stable 1.0.8 checkpoint:

```powershell
git checkout -b feature/regression-testing
```

Copy these files into the Elise project root:

```text
regression_suite.py
run_regression.ps1
REGRESSION_TESTING.md
```

Add the lines from `gitignore_regression_additions.txt` to `.gitignore`.

## First run

From the activated virtual environment:

```powershell
python regression_suite.py
```

or:

```powershell
.un_regression.ps1
```

The default run checks:

- required files and Python compilation;
- memory add/search/import/delete/persistence;
- document indexing, filtering, attribution, and reindex;
- tool permissions and fail-closed unknown tools;
- safe reads and path traversal rejection;
- confirmation-gated create/replace/append/directory operations;
- exact targeted edits and stale-preview protection;
- audit persistence and redaction;
- internet opt-in persistence, malformed settings, URL filtering, gzip/deflate;
- pure app routing, stable-version parsing, and grounding validation.

## Save a JSON report

```powershell
python regression_suite.py `
  --json-report regression_reportsaseline-1.0.8.json
```

## Optional live checks

These are opt-in because they use external services or model compute:

```powershell
python regression_suite.py --live-internet
```

This performs one bounded read-only fetch from Python.org.

```powershell
python regression_suite.py --live-model
```

This sends one minimal prompt to the configured Ollama model.

Run both:

```powershell
python regression_suite.py `
  --live-internet `
  --live-model `
  --json-report regression_reportsull-live.json
```

## Run selected groups

```powershell
python regression_suite.py --group memory,documents
python regression_suite.py --group tools --verbose
python regression_suite.py --group internet,app
```

Available groups:

```text
structure
memory
documents
tools
audit
internet
app
live-internet
live-model
```

## Exit codes

```text
0  all selected checks passed or were intentionally skipped
1  one or more checks failed
2  invalid command-line arguments
```

## Commit after the baseline passes

```powershell
git add regression_suite.py run_regression.ps1 REGRESSION_TESTING.md .gitignore
git commit -m "Add isolated Elise regression test harness"
git push -u origin feature/regression-testing
```

Do not merge the branch until the default suite passes on the actual Elise
project.
