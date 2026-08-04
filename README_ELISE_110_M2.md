# Elise 1.1.0-dev2 — Workflow Execution Milestone 2

Milestone 2 connects the persistent workflow state machine to Elise's existing
safe local tools.

## Supported workflow

```text
Read a documents/ text file
→ generate a source-only Markdown summary
→ preview a create-or-replace operation
→ pause for exact user confirmation
→ write through ToolManager
→ reindex local documents
→ persist completion metadata
```

The workflow remains deliberately narrow. It does not let Qwen invent arbitrary
plans or choose arbitrary tools.

## Safety behavior

- Source and destination paths are constrained to `documents/`.
- Files are accessed through the existing `ToolManager`.
- Source text and generated output remain in process memory.
- SQLite stores paths, bounded summaries, counts, statuses, and SHA-256 hashes,
  not full source or output text.
- A truncated source is rejected rather than partially summarized.
- The summary prompt treats file text as untrusted data.
- The model receives no workflow tools.
- The generated output is limited to 8,000 characters.
- The complete proposed output is displayed before writing.
- Only the exact response `yes` approves the write.
- Any other response cancels the workflow without writing.
- Ctrl+C or end-of-input at confirmation leaves the workflow paused.
- A paused workflow can be resumed after restart.
- Resume re-reads the source, regenerates the summary, and verifies hashes.
- Source changes, regenerated-summary changes, and destination
  existence/size changes fail closed.
- Writes remain recorded in the existing tool audit database.
- Reindex failure is reported after the successful write and stops the
  remaining workflow.

## Files

Replace or add:

```text
app_112_final.py                 -> app.py
workflow_112_final.py            -> workflow.py
workflow_execution_112_final.py  -> workflow_execution.py
regression_suite_120_final.py    -> regression_suite.py
```

## Start from a new branch

```powershell
git checkout main
git pull origin main
git checkout -b feature/workflow-execution
```

## Back up the replaced files

```powershell
Copy-Item .\app.py .\app_before_112.py
Copy-Item .\workflow.py .\workflow_before_112.py
Copy-Item .\regression_suite.py .\regression_suite_before_120.py
```

Keep backups outside the repository before committing, or leave them ignored.

## Compile and test

Activate the virtual environment first:

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

Expected default result:

```text
Elise Regression Suite 1.2.0
Summary: 11 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

The added check is:

```text
[PASS] workflow-execution: safe read-summarize-confirm-write execution
```

## Manual approved-write test

Start Elise:

```powershell
python app.py
```

Create a workflow:

```text
/new-workflow documents/elise_project_notes.md documents/next_steps.md
```

Use the ID Elise prints:

```text
/run-workflow <id>
```

Elise should:

1. locate and read the source;
2. generate a Markdown summary;
3. display the exact destination and complete proposed content;
4. ask for the exact word `yes`.

Type:

```text
yes
```

Then inspect:

```text
/workflow <id>
```

All eight steps should be `completed`, and `documents/next_steps.md` should
exist and be indexed.

## Manual denial test

Create another workflow with a new destination:

```text
/new-workflow documents/elise_project_notes.md documents/denied_test.md
/run-workflow <id>
```

At confirmation, type anything other than `yes`.

Expected:

- workflow status: `cancelled`;
- steps 1–4 remain completed;
- unfinished steps are skipped;
- `documents/denied_test.md` is not created.

## Manual restart-resume test

Create another workflow and run it. At the confirmation prompt, press Ctrl+C.

Expected:

```text
status: waiting_for_confirmation
```

Exit and restart Elise, then run:

```text
/resume-workflow <id>
```

Elise rebuilds and verifies the source, summary, and preview before asking
again.

## Commit after testing

Add only the implementation files and optionally this guide:

```powershell
git add app.py workflow.py workflow_execution.py regression_suite.py README_ELISE_110_M2.md
git status
git commit -m "Add safe workflow execution"
git push -u origin feature/workflow-execution
```

Open a pull request into `main` and wait for `Offline regression` to pass.
