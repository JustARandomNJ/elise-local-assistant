# Elise 1.1.0-dev3 — Reusable Workflow Templates

Milestone 3 expands Elise from one fixed workflow into a small library of
approved deterministic templates.

## Added templates

### Document summary

```text
/new-summary-workflow <source_path> <destination_path>
```

Produces Markdown beginning with:

```text
# Document Summary
```

It summarizes purpose, key facts, decisions, current state, and important
constraints without turning ordinary descriptive text into tasks.

### Action-item extraction

```text
/new-actions-workflow <source_path> <destination_path>
```

Produces Markdown beginning with:

```text
# Action Items
```

It extracts only explicit tasks, open work, and follow-ups supported by the
source. It does not invent owners or deadlines.

The older command remains available:

```text
/new-workflow <source_path> <destination_path>
```

That legacy command continues to create the original next-steps workflow.

### Two-document comparison

```text
/new-compare-workflow <source_a> <source_b> <destination_path>
```

Produces Markdown beginning with:

```text
# Document Comparison
```

It compares similarities, differences, conflicts, and information present in
one source but absent from the other. It does not decide which document is
correct unless the source text itself establishes that.

## Shared deterministic actions

The new templates all use the same eight-step structure:

1. Locate source file or files
2. Read source file or files
3. Generate source-grounded output
4. Preview destination
5. Wait for exact write confirmation
6. Write confirmed output
7. Reindex documents
8. Report completion

The model selects neither the workflow nor its tools. The user selects a
host-defined template, and the host executes only its allowlisted actions.

## Safety behavior

- Sources and destinations remain restricted to `documents/`.
- Summary and action-item templates require exactly one source.
- Comparison requires exactly two distinct sources.
- A destination cannot be one of the source files.
- Each source is limited to 20,000 characters.
- Combined comparison input is limited to 30,000 characters.
- Generated output is limited to 8,000 characters.
- File text is marked as untrusted data in the model prompt.
- Source text and generated output remain in memory.
- SQLite stores paths, counts, statuses, and SHA-256 hashes, not full text.
- Existing confirmation, stale-preview, audited-write, and reindex protections
  remain active.
- Interrupted confirmations can still be resumed after restart.
- Old `read_summarize_write` workflow records remain executable.
- Arbitrary model-generated plans and arbitrary tool selection remain disabled.

## Files

Copy or replace:

```text
app_113_final.py                 -> app.py
workflow_113_final.py            -> workflow.py
workflow_execution_113_final.py  -> workflow_execution.py
regression_suite_130_final.py    -> regression_suite.py
```

## Branch

Start from current `main`:

```powershell
git checkout main
git pull origin main
git status
git checkout -b feature/workflow-templates
```

## Backups

Store backups outside the repository:

```powershell
Copy-Item .\app.py "$HOME\Documents\app_before_113.py"
Copy-Item .\workflow.py "$HOME\Documents\workflow_before_113.py"
Copy-Item .\workflow_execution.py "$HOME\Documents\workflow_execution_before_113.py"
Copy-Item .\regression_suite.py "$HOME\Documents\regression_suite_before_130.py"
```

## Compile and test

Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then run:

```powershell
python -m py_compile app.py
python -m py_compile workflow.py
python -m py_compile workflow_execution.py
python -m py_compile regression_suite.py
python regression_suite.py
```

Expected:

```text
Elise Regression Suite 1.3.0
Summary: 12 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

The new test is:

```text
[PASS] workflow-templates: approved reusable workflow templates
```

## Manual tests

Start Elise:

```powershell
python app.py
```

### Summary

```text
/new-summary-workflow documents/elise_project_notes.md documents/m3_summary.md
/run-workflow <id>
```

Type exactly `yes` after reviewing the preview.

### Action items

```text
/new-actions-workflow documents/elise_project_notes.md documents/m3_actions.md
/run-workflow <id>
```

Type exactly `yes` after reviewing the preview.

### Comparison

Choose two existing text documents:

```text
/new-compare-workflow documents/first.md documents/second.md documents/m3_comparison.md
/run-workflow <id>
```

Type exactly `yes` after reviewing the preview.

Inspect any completed workflow:

```text
/workflow <id>
```

## Clean up manual outputs

Remove only files created for testing:

```powershell
Remove-Item .\documents\m3_summary.md -ErrorAction SilentlyContinue
Remove-Item .\documents\m3_actions.md -ErrorAction SilentlyContinue
Remove-Item .\documents\m3_comparison.md -ErrorAction SilentlyContinue
```

## Commit

```powershell
git add app.py workflow.py workflow_execution.py regression_suite.py README_ELISE_110_M3.md
git status
git commit -m "Add reusable workflow templates"
git push -u origin feature/workflow-templates
```

Open a pull request into `main`, wait for `Offline regression`, and merge only
after the local and GitHub suites pass.
