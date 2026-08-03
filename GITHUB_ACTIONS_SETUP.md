# GitHub Actions CI for Elise

This workflow automatically runs Elise's offline-safe regression suite on a
GitHub-hosted Windows runner.

## What runs automatically

The workflow runs when:

- code is pushed to `main`;
- code is pushed to any `feature/**` branch;
- a pull request targets `main`;
- it is started manually from the Actions tab.

The automatic job runs the same nine offline checks that passed locally:

- project structure and Python compilation;
- structured memory lifecycle;
- local document indexing and search;
- filesystem permissions and write safeguards;
- audit persistence and redaction;
- offline-safe internet settings, URL filtering, and compression;
- app routing and grounding functions.

## What does not run

CI does not use:

- the local Ollama server;
- `qwen3.5:4b`;
- live internet regression checks;
- your real memories, documents, settings, or audit database.

The `ollama` Python client is installed only because `app.py` imports it during
the app-level offline checks.

## Files

Copy these paths into the Elise repository:

```text
.github/workflows/elise-regression.yml
requirements-ci.txt
```

## Install on a feature branch

```powershell
git checkout -b feature/github-actions-ci
```

Extract the bundle into the Elise project root. Confirm the workflow is in the
nested `.github\workflows` directory:

```powershell
Get-ChildItem .github\workflows
Get-Content requirements-ci.txt
```

Run the local offline suite one more time:

```powershell
python regression_suite.py
```

Then commit and push:

```powershell
git add .github/workflows/elise-regression.yml requirements-ci.txt
git commit -m "Add GitHub Actions regression workflow"
git push -u origin feature/github-actions-ci
```

Because the branch matches `feature/**`, that push should start the workflow.
Open the repository's **Actions** tab and select **Elise Regression**.

## Expected GitHub result

The job should show:

```text
Summary: 9 passed, 0 failed, 0 skipped
Regression suite PASSED.
```

The workflow also uploads `github-actions.json` as a downloadable artifact for
14 days. The artifact step runs even when regression checks fail.

## Manual targeted run

In GitHub:

1. Open **Actions**.
2. Select **Elise Regression**.
3. Select **Run workflow**.
4. Optionally enter groups such as:

```text
memory,documents
tools
internet,app
```

An empty field runs the complete offline suite.

## Merge after CI passes

```powershell
git checkout main
git pull origin main
git merge --no-ff feature/github-actions-ci -m "Merge GitHub Actions regression workflow"
git push origin main
```

After the merge, another CI run will verify `main`.

## Optional branch protection

Once the workflow has passed on `main`, GitHub can require it before merging:

1. Open repository **Settings**.
2. Open **Branches** or **Rulesets**.
3. Create a rule for `main`.
4. Require a pull request before merging.
5. Require status checks to pass.
6. Select the `Offline regression` check.

Do this after the first successful `main` run so the check name is available.
