from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gc
import gzip
import importlib
import json
import os
from pathlib import Path
import platform
import py_compile
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Callable
import zlib


SUITE_VERSION = "1.2.0"
DEFAULT_GROUPS = {
    "structure",
    "memory",
    "documents",
    "tools",
    "audit",
    "internet",
    "app",
    "workflow",
    "workflow-execution",
}
OPTIONAL_GROUPS = {
    "live-internet",
    "live-model",
}
REQUIRED_PROJECT_FILES = {
    "app.py",
    "memory.py",
    "document_search.py",
    "tools.py",
    "audit.py",
    "internet.py",
    "workflow.py",
    "workflow_execution.py",
}


@dataclass
class CheckResult:
    group: str
    name: str
    status: str
    duration_ms: int
    detail: str = ""
    traceback: str = ""


class CheckSkipped(Exception):
    """Raised when a check is intentionally not applicable."""


class RegressionRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        selected_groups: set[str],
        verbose: bool,
    ) -> None:
        self.project_root = project_root.resolve()
        self.selected_groups = selected_groups
        self.verbose = verbose
        self.results: list[CheckResult] = []

    def run(
        self,
        group: str,
        name: str,
        check: Callable[[], str | None],
    ) -> None:
        if group not in self.selected_groups:
            return

        started = time.perf_counter()

        try:
            detail = check() or ""
            status = "PASS"
            error_trace = ""
        except CheckSkipped as error:
            status = "SKIP"
            detail = str(error)
            error_trace = ""
        except Exception as error:
            status = "FAIL"
            detail = f"{type(error).__name__}: {error}"
            error_trace = traceback.format_exc()

        elapsed_ms = int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        )
        result = CheckResult(
            group=group,
            name=name,
            status=status,
            duration_ms=elapsed_ms,
            detail=detail,
            traceback=error_trace,
        )
        self.results.append(
            result
        )

        symbol = {
            "PASS": "[PASS]",
            "FAIL": "[FAIL]",
            "SKIP": "[SKIP]",
        }[status]
        print(
            f"{symbol} {group}: {name} "
            f"({elapsed_ms} ms)"
        )

        if (
            detail
            and (
                self.verbose
                or status != "PASS"
            )
        ):
            print(
                "       "
                + detail.replace(
                    "\n",
                    "\n       ",
                )
            )

        if (
            error_trace
            and self.verbose
        ):
            print(
                error_trace
            )

    def summary(self) -> dict[str, Any]:
        counts = {
            status: sum(
                result.status == status
                for result in self.results
            )
            for status in (
                "PASS",
                "FAIL",
                "SKIP",
            )
        }

        return {
            "suite_version": SUITE_VERSION,
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(
                timespec="seconds"
            ),
            "project_root": str(
                self.project_root
            ),
            "python": sys.version,
            "platform": platform.platform(),
            "selected_groups": sorted(
                self.selected_groups
            ),
            "counts": counts,
            "results": [
                asdict(
                    result
                )
                for result in self.results
            ],
        }


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise AssertionError(
            message
        )


def import_fresh(
    module_name: str,
):
    importlib.invalidate_caches()

    if module_name in sys.modules:
        del sys.modules[
            module_name
        ]

    return importlib.import_module(
        module_name
    )


def import_project_module(
    project_root: Path,
    module_name: str,
):
    """
    Import one project module from its exact file path.

    This prevents unrelated installed packages or stale modules with common
    names such as ``app`` from satisfying a regression import accidentally.
    """

    module_path = (
        project_root
        / f"{module_name}.py"
    ).resolve()

    require(
        module_path.is_file(),
        f"Project module does not exist: {module_path}",
    )

    unique_name = (
        "_elise_regression_"
        + module_name
        + "_"
        + str(
            abs(
                hash(
                    str(
                        module_path
                    )
                )
            )
        )
    )
    importlib.invalidate_caches()

    if unique_name in sys.modules:
        del sys.modules[
            unique_name
        ]

    specification = (
        importlib.util.spec_from_file_location(
            unique_name,
            module_path,
        )
    )
    require(
        specification is not None
        and specification.loader is not None,
        f"Could not build an import specification for {module_path}",
    )

    module = (
        importlib.util.module_from_spec(
            specification
        )
    )
    sys.modules[
        unique_name
    ] = module

    try:
        specification.loader.exec_module(
            module
        )
    except Exception:
        sys.modules.pop(
            unique_name,
            None,
        )
        raise

    loaded_path = Path(
        str(
            getattr(
                module,
                "__file__",
                "",
            )
        )
    ).resolve()
    require(
        loaded_path == module_path,
        (
            "Regression imported the wrong project module. "
            f"Expected {module_path}; loaded {loaded_path}."
        ),
    )

    return module


def row_value(
    row: Any,
    key: str,
) -> Any:
    if isinstance(
        row,
        sqlite3.Row,
    ):
        return row[
            key
        ]

    if isinstance(
        row,
        dict,
    ):
        return row[
            key
        ]

    return getattr(
        row,
        key,
    )



def release_resource(
    resource: Any,
) -> None:
    """
    Best-effort release for stores that keep a SQLite connection open.

    Elise store implementations have changed over time. Some expose close(),
    while others retain a connection in an attribute such as connection,
    _connection, conn, or _conn. This helper supports both shapes.
    """

    if resource is None:
        return

    close_method = getattr(
        resource,
        "close",
        None,
    )

    if callable(
        close_method
    ):
        try:
            close_method()
        except Exception:
            pass

    try:
        attributes = vars(
            resource
        )
    except TypeError:
        attributes = {}

    for attribute_name in (
        "connection",
        "_connection",
        "conn",
        "_conn",
        "database_connection",
        "_database_connection",
    ):
        connection = attributes.get(
            attribute_name
        )

        if isinstance(
            connection,
            sqlite3.Connection,
        ):
            try:
                connection.close()
            except Exception:
                pass


def release_resources(
    *resources: Any,
) -> None:
    for resource in resources:
        release_resource(
            resource
        )

    # CPython normally releases SQLite handles immediately, but explicitly
    # collecting here avoids delayed Windows file locks from cyclic references.
    gc.collect()


def check_required_files(
    project_root: Path,
) -> str:
    missing = sorted(
        name
        for name in REQUIRED_PROJECT_FILES
        if not (
            project_root
            / name
        ).is_file()
    )
    require(
        not missing,
        "Missing project files: "
        + ", ".join(
            missing
        ),
    )

    return (
        f"{len(REQUIRED_PROJECT_FILES)} required files present"
    )


def check_compile_project(
    project_root: Path,
) -> str:
    compiled: list[str] = []

    for filename in sorted(
        REQUIRED_PROJECT_FILES
    ):
        path = (
            project_root
            / filename
        )
        py_compile.compile(
            str(
                path
            ),
            doraise=True,
        )
        compiled.append(
            filename
        )

    return (
        "Compiled: "
        + ", ".join(
            compiled
        )
    )


def check_git_worktree(
    project_root: Path,
) -> str:
    git_directory = (
        project_root
        / ".git"
    )

    if not git_directory.exists():
        raise CheckSkipped(
            "No local .git directory found."
        )

    process = subprocess.run(
        [
            "git",
            "status",
            "--short",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    require(
        process.returncode == 0,
        process.stderr.strip()
        or "git status failed",
    )

    changed = [
        line
        for line in process.stdout.splitlines()
        if line.strip()
    ]

    return (
        "Working tree clean"
        if not changed
        else (
            f"Working tree has {len(changed)} changed path(s); "
            "this is informational during development."
        )
    )


def check_memory_store(
    project_root: Path,
) -> str:
    memory_module = import_fresh(
        "memory"
    )
    MemoryStore = (
        memory_module.MemoryStore
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-memory-",
        ignore_cleanup_errors=True,
    ) as temporary_directory:
        root = Path(
            temporary_directory
        )
        database = (
            root
            / "data"
            / "elise.db"
        )
        store = MemoryStore(
            database
        )

        first_content = (
            "Regression fixture prefers direct practical feedback."
        )
        second_content = (
            "Regression fixture owns an ESP32-S3 board."
        )

        require(
            store.add(
                content=first_content,
                category="preference",
                status="confirmed",
                confidence=1.0,
                source="regression",
            )
            is True,
            "First memory was not added.",
        )
        require(
            store.add(
                content=first_content,
                category="preference",
                status="confirmed",
                confidence=1.0,
                source="regression",
            )
            is False,
            "Duplicate memory was not rejected.",
        )
        require(
            store.add(
                content=second_content,
                category="project",
                status="observed",
                confidence=0.8,
                source="regression",
            )
            is True,
            "Second memory was not added.",
        )

        try:
            store.add(
                content="Invalid category fixture",
                category="invalid",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "Invalid memory category was accepted."
            )

        all_rows = store.list_all()
        require(
            len(
                all_rows
            )
            == 2,
            "Unexpected memory count after insertions.",
        )

        preference_rows = store.list_all(
            "preference"
        )
        require(
            len(
                preference_rows
            )
            == 1,
            "Category filtering returned the wrong count.",
        )

        search_rows = store.search(
            "How should you communicate and give feedback?",
            top_k=6,
        )
        require(
            search_rows,
            "Relevant memory search returned no results.",
        )
        require(
            first_content
            in [
                row_value(
                    row,
                    "content",
                )
                for row in search_rows
            ],
            "Communication query did not retrieve the preference fixture.",
        )

        prompt = store.build_prompt(
            search_rows
        )
        require(
            first_content in prompt,
            "Memory prompt omitted the retrieved memory.",
        )
        require(
            "PREFERENCE:" in prompt,
            "Memory prompt omitted category grouping.",
        )

        reopened = MemoryStore(
            database
        )
        require(
            len(
                reopened.list_all()
            )
            == 2,
            "Memory database did not persist after reopening.",
        )

        profile_path = (
            root
            / "profile.json"
        )
        profile_path.write_text(
            json.dumps(
                {
                    "memories": [
                        {
                            "content": first_content,
                            "category": "preference",
                            "status": "confirmed",
                            "confidence": 1.0,
                            "source": "profile",
                        },
                        {
                            "content": (
                                "Regression fixture prioritizes local software."
                            ),
                            "category": "goal",
                            "status": "confirmed",
                            "confidence": 1.0,
                            "source": "profile",
                        },
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        added, skipped = (
            reopened.import_profile(
                profile_path
            )
        )
        require(
            (
                added,
                skipped,
            )
            == (
                1,
                1,
            ),
            "Profile import did not report one addition and one duplicate.",
        )

        first_id = int(
            row_value(
                reopened.list_all(
                    "preference"
                )[0],
                "id",
            )
        )
        require(
            reopened.delete(
                first_id
            )
            is True,
            "Memory deletion failed.",
        )
        require(
            reopened.delete(
                first_id
            )
            is False,
            "Deleting a missing memory did not return False.",
        )

        release_resources(
            reopened,
            store,
        )
        del reopened
        del store
        del all_rows
        del preference_rows
        del search_rows

    return (
        "Add, duplicate rejection, search, prompt, import, persistence, "
        "and deletion passed"
    )


def check_document_store(
    project_root: Path,
) -> str:
    document_module = import_fresh(
        "document_search"
    )
    DocumentStore = (
        document_module.DocumentStore
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-documents-"
    ) as temporary_directory:
        documents = (
            Path(
                temporary_directory
            )
            / "documents"
        )
        nested = (
            documents
            / "nested"
        )
        nested.mkdir(
            parents=True
        )
        (
            documents
            / "alpha.md"
        ).write_text(
            (
                "# Zephyr Notes\n\n"
                "The zephyr regression token is AURORA-417. "
                "This file discusses RTOS scheduling and device trees."
            ),
            encoding="utf-8",
        )
        (
            nested
            / "beta.txt"
        ).write_text(
            (
                "The document regression token is COBALT-902. "
                "This file covers persistent local indexing."
            ),
            encoding="utf-8",
        )
        (
            documents
            / "ignored.bin"
        ).write_bytes(
            b"\x00\x01\x02"
        )

        store = DocumentStore(
            documents
        )
        file_count, chunk_count = (
            store.reindex()
        )
        require(
            file_count >= 2,
            "Fewer than two supported documents were indexed.",
        )
        require(
            chunk_count >= 2,
            "Fewer than two document sections were indexed.",
        )

        listed = [
            str(
                value
            )
            for value in store.list_documents()
        ]
        require(
            any(
                "alpha.md"
                in value
                for value in listed
            ),
            "alpha.md is missing from indexed documents.",
        )
        require(
            any(
                "beta.txt"
                in value
                for value in listed
            ),
            "beta.txt is missing from indexed documents.",
        )
        require(
            not any(
                "ignored.bin"
                in value
                for value in listed
            ),
            "Unsupported binary file was indexed.",
        )

        results = store.search(
            "AURORA-417 zephyr scheduling",
            top_k=4,
        )
        require(
            results,
            "Document search returned no Zephyr fixture.",
        )
        require(
            any(
                "AURORA-417"
                in str(
                    row_value(
                        result,
                        "text",
                    )
                )
                for result in results
            ),
            "Document search did not retrieve the exact fixture token.",
        )
        require(
            any(
                "alpha.md"
                in str(
                    row_value(
                        result,
                        "source",
                    )
                )
                for result in results
            ),
            "Document source attribution omitted alpha.md.",
        )

        context = store.build_prompt_context(
            results
        )
        require(
            "AURORA-417" in context,
            "Prompt context omitted the retrieved document text.",
        )
        require(
            "alpha.md" in context,
            "Prompt context omitted the source filename.",
        )

        (
            documents
            / "gamma.md"
        ).write_text(
            (
                "# Reindex Verification\n\n"
                "This regression reindex beacon verifies that newly added "
                "documents become searchable during the same session. "
                "The unique marker is EMBER-331."
            ),
            encoding="utf-8",
        )
        second_file_count, _ = (
            store.reindex()
        )
        require(
            second_file_count
            >= file_count
            + 1,
            "Reindex did not detect a newly created document.",
        )
        new_results = store.search(
            "regression reindex beacon newly added document",
            top_k=4,
        )
        require(
            new_results,
            "Newly created document was not searchable after reindex.",
        )
        require(
            any(
                "gamma.md"
                in str(
                    row_value(
                        result,
                        "source",
                    )
                )
                for result in new_results
            ),
            "Reindex search did not return gamma.md as a source.",
        )
        require(
            any(
                "EMBER-331"
                in str(
                    row_value(
                        result,
                        "text",
                    )
                )
                for result in new_results
            ),
            "Reindex search omitted the new fixture content.",
        )

        release_resources(
            store
        )
        del store
        del results
        del new_results

    return (
        "Indexing, filtering, search, attribution, context, and reindex passed"
    )


def build_tool_environment(
    root: Path,
):
    internet_module = import_fresh(
        "internet"
    )
    tools_module = import_fresh(
        "tools"
    )

    project_directory = (
        root
        / "project"
    )
    documents_directory = (
        root
        / "documents"
    )
    project_directory.mkdir(
        parents=True
    )
    documents_directory.mkdir(
        parents=True
    )
    manager = (
        internet_module.InternetManager(
            root
            / "internet_settings.json"
        )
    )
    tool_manager = (
        tools_module.ToolManager(
            project_directory=(
                project_directory
            ),
            documents_directory=(
                documents_directory
            ),
            internet_manager=manager,
        )
    )

    return (
        internet_module,
        tools_module,
        manager,
        tool_manager,
        project_directory,
        documents_directory,
    )


def check_tool_manager(
    project_root: Path,
) -> str:
    with tempfile.TemporaryDirectory(
        prefix="elise-regression-tools-"
    ) as temporary_directory:
        root = Path(
            temporary_directory
        )
        (
            internet_module,
            tools_module,
            internet_manager,
            manager,
            project_directory,
            documents_directory,
        ) = build_tool_environment(
            root
        )

        (
            documents_directory
            / "readme.md"
        ).write_text(
            "Regression read fixture.",
            encoding="utf-8",
        )

        policies = manager.tool_policies()
        require(
            policies[
                "create_text_file"
            ][
                "requires_confirmation"
            ]
            is True,
            "Write policy lost confirmation requirement.",
        )
        unknown_policy = (
            manager.get_tool_policy(
                "definitely_unknown"
            )
        )
        require(
            unknown_policy[
                "permission_mode"
            ]
            == "blocked",
            "Unknown tool policy is not fail-closed.",
        )

        disabled_tools = (
            manager.available_tools()
        )
        require(
            "search_web"
            not in disabled_tools,
            "Network tool remained exposed while internet was disabled.",
        )
        internet_manager.set_enabled(
            True
        )
        enabled_tools = (
            manager.available_tools()
        )
        require(
            "search_web"
            in enabled_tools,
            "Network tool was not exposed after opt-in.",
        )
        internet_manager.set_enabled(
            False
        )

        listing = manager.execute(
            "list_files",
            {
                "root": "documents",
                "path": ".",
                "recursive": False,
            },
        )
        require(
            listing.get(
                "success"
            )
            is True,
            "Safe file listing failed.",
        )
        require(
            any(
                item.get(
                    "path"
                )
                == "readme.md"
                for item in listing.get(
                    "entries",
                    [],
                )
            ),
            "Safe file listing omitted the fixture.",
        )

        read_result = manager.execute(
            "read_text_file",
            {
                "root": "documents",
                "path": "readme.md",
                "max_chars": 100,
            },
        )
        require(
            read_result.get(
                "content"
            )
            == "Regression read fixture.",
            "Read tool returned unexpected content.",
        )

        traversal = manager.execute(
            "read_text_file",
            {
                "root": "documents",
                "path": "../app.py",
                "max_chars": 100,
            },
        )
        require(
            traversal.get(
                "success"
            )
            is False,
            "Path traversal was not blocked.",
        )

        create_arguments = {
            "root": "documents",
            "path": "created.md",
            "content": (
                "alpha unique phrase"
            ),
        }
        unconfirmed = manager.execute(
            "create_text_file",
            create_arguments,
        )
        require(
            unconfirmed.get(
                "success"
            )
            is False,
            "Write executed without explicit confirmation.",
        )
        preview = (
            manager.preview_tool_action(
                "create_text_file",
                create_arguments,
            )
        )
        require(
            preview.get(
                "success"
            )
            is True,
            "Create preview failed.",
        )
        require(
            not (
                documents_directory
                / "created.md"
            ).exists(),
            "Preview modified the filesystem.",
        )
        created = manager.execute(
            "create_text_file",
            create_arguments,
            confirmed=True,
        )
        require(
            created.get(
                "success"
            )
            is True,
            "Confirmed create failed.",
        )

        appended = manager.execute(
            "append_text_file",
            {
                "root": "documents",
                "path": "created.md",
                "content": (
                    "\nbeta appended phrase"
                ),
            },
            confirmed=True,
        )
        require(
            appended.get(
                "success"
            )
            is True,
            "Confirmed append failed.",
        )

        replaced = manager.execute(
            "replace_text_file",
            {
                "root": "documents",
                "path": "created.md",
                "content": (
                    "start UNIQUE_TARGET end"
                ),
            },
            confirmed=True,
        )
        require(
            replaced.get(
                "success"
            )
            is True,
            "Confirmed full replacement failed.",
        )

        edit_arguments = {
            "root": "documents",
            "path": "created.md",
            "old_text": "UNIQUE_TARGET",
            "new_text": "UPDATED_TARGET",
        }
        edit_preview = (
            manager.preview_tool_action(
                "replace_text_occurrence",
                edit_arguments,
            )
        )
        require(
            edit_preview.get(
                "success"
            )
            is True,
            "Targeted edit preview failed.",
        )
        source_hash = edit_preview.get(
            "source_sha256"
        )
        require(
            isinstance(
                source_hash,
                str,
            )
            and len(
                source_hash
            )
            == 64,
            "Targeted preview did not return a source hash.",
        )

        (
            documents_directory
            / "created.md"
        ).write_text(
            "start UNIQUE_TARGET end changed",
            encoding="utf-8",
        )
        stale = manager.execute(
            "replace_text_occurrence",
            {
                **edit_arguments,
                "expected_file_sha256": (
                    source_hash
                ),
            },
            confirmed=True,
        )
        require(
            stale.get(
                "success"
            )
            is False,
            "Stale targeted-edit preview was not rejected.",
        )

        fresh_preview = (
            manager.preview_tool_action(
                "replace_text_occurrence",
                edit_arguments,
            )
        )
        targeted = manager.execute(
            "replace_text_occurrence",
            {
                **edit_arguments,
                "expected_file_sha256": (
                    fresh_preview[
                        "source_sha256"
                    ]
                ),
            },
            confirmed=True,
        )
        require(
            targeted.get(
                "success"
            )
            is True,
            "Fresh targeted edit failed.",
        )
        require(
            "UPDATED_TARGET"
            in (
                documents_directory
                / "created.md"
            ).read_text(
                encoding="utf-8"
            ),
            "Targeted edit did not update the file.",
        )

        (
            documents_directory
            / "duplicates.md"
        ).write_text(
            "repeat repeat",
            encoding="utf-8",
        )
        duplicate_preview = (
            manager.preview_tool_action(
                "replace_text_occurrence",
                {
                    "root": "documents",
                    "path": "duplicates.md",
                    "old_text": "repeat",
                    "new_text": "updated",
                },
            )
        )
        require(
            duplicate_preview.get(
                "success"
            )
            is False,
            "Ambiguous targeted edit was not rejected.",
        )

        directory_result = manager.execute(
            "create_directory",
            {
                "root": "documents",
                "path": "new-folder",
            },
            confirmed=True,
        )
        require(
            directory_result.get(
                "success"
            )
            is True,
            "Confirmed directory creation failed.",
        )

        unsupported = manager.execute(
            "create_text_file",
            {
                "root": "documents",
                "path": "unsafe.exe",
                "content": "no",
            },
            confirmed=True,
        )
        require(
            unsupported.get(
                "success"
            )
            is False,
            "Unsupported file extension was accepted.",
        )

        oversized = manager.execute(
            "create_text_file",
            {
                "root": "documents",
                "path": "oversized.md",
                "content": (
                    "x"
                    * (
                        tools_module.MAX_WRITE_CHARS
                        + 1
                    )
                ),
            },
            confirmed=True,
        )
        require(
            oversized.get(
                "success"
            )
            is False,
            "Oversized write content was accepted.",
        )

    return (
        "Policies, reads, traversal blocking, confirmations, previews, "
        "writes, stale hashes, and limits passed"
    )


def check_audit_log(
    project_root: Path,
) -> str:
    audit_module = import_fresh(
        "audit"
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-audit-",
        ignore_cleanup_errors=True,
    ) as temporary_directory:
        database = (
            Path(
                temporary_directory
            )
            / "tool_audit.db"
        )
        log = audit_module.ToolAuditLog(
            database
        )
        secret = (
            "REGRESSION_SECRET_CONTENT"
        )
        row_id = log.record(
            source="regression",
            request_text=(
                "Create a regression fixture."
            ),
            tool_name=(
                "create_text_file"
            ),
            arguments={
                "root": "documents",
                "path": "fixture.md",
                "content": secret,
                "url": (
                    "https://example.com/path?"
                    "token=super-secret#fragment"
                ),
            },
            policy={
                "access_mode": "write",
                "risk_level": "medium",
                "permission_mode": (
                    "confirmation"
                ),
                "requires_confirmation": (
                    True
                ),
            },
            approved=True,
            result={
                "success": True,
                "tool": (
                    "create_text_file"
                ),
            },
            result_summary=(
                "success: regression fixture"
            ),
        )
        require(
            row_id >= 1,
            "Audit record did not receive an ID.",
        )
        require(
            log.count() == 1,
            "Audit count did not increment.",
        )

        rows = log.list_recent(
            5
        )
        require(
            len(
                rows
            )
            == 1,
            "Audit list did not return the record.",
        )
        arguments_json = str(
            rows[0][
                "arguments_json"
            ]
        )
        require(
            secret
            not in arguments_json,
            "Write content leaked into the audit log.",
        )
        require(
            "sha256"
            in arguments_json,
            "Redacted write metadata omitted its hash.",
        )
        require(
            "super-secret"
            not in arguments_json,
            "URL query value leaked into the audit log.",
        )
        require(
            "<redacted>"
            in arguments_json,
            "URL query was not visibly redacted.",
        )

        reopened = (
            audit_module.ToolAuditLog(
                database
            )
        )
        require(
            reopened.count() == 1,
            "Audit database did not persist after reopening.",
        )

        release_resources(
            reopened,
            log,
        )
        del reopened
        del log
        del rows

    return (
        "Persistence, write redaction, URL redaction, count, and listing passed"
    )


def check_internet_manager(
    project_root: Path,
) -> str:
    internet_module = import_fresh(
        "internet"
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-internet-"
    ) as temporary_directory:
        root = Path(
            temporary_directory
        )
        settings = (
            root
            / "settings.json"
        )
        manager = (
            internet_module.InternetManager(
                settings
            )
        )
        require(
            manager.is_enabled
            is False,
            "Internet manager did not default to disabled.",
        )
        manager.set_enabled(
            True
        )
        require(
            manager.is_enabled
            is True,
            "Internet opt-in did not enable the manager.",
        )
        reopened = (
            internet_module.InternetManager(
                settings
            )
        )
        require(
            reopened.is_enabled
            is True,
            "Internet opt-in did not persist.",
        )
        reopened.set_enabled(
            False
        )
        require(
            internet_module.InternetManager(
                settings
            ).is_enabled
            is False,
            "Internet opt-out did not persist.",
        )

        malformed_path = (
            root
            / "malformed.json"
        )
        malformed_path.write_text(
            "{not-json",
            encoding="utf-8",
        )
        malformed = (
            internet_module.InternetManager(
                malformed_path
            )
        )
        require(
            malformed.is_enabled
            is False,
            "Malformed settings did not fail closed.",
        )
        require(
            malformed.status().get(
                "settings_warning"
            ),
            "Malformed settings did not expose a warning.",
        )

        safe_url = (
            internet_module.InternetManager
            ._validate_result_url_syntax(
                "https://example.com/path?q=1#fragment"
            )
        )
        require(
            safe_url
            == "https://example.com/path?q=1",
            "Safe URL syntax was not normalized correctly.",
        )

        unsafe_urls = [
            "file:///etc/passwd",
            "http://localhost/",
            "http://127.0.0.1/",
            "http://user:pass@example.com/",
            "https://example.com:8443/",
            "http://singlelabel/",
        ]

        for url in unsafe_urls:
            require(
                (
                    internet_module.InternetManager
                    ._validate_result_url_syntax(
                        url
                    )
                )
                is None,
                f"Unsafe URL syntax was accepted: {url}",
            )

        payload = (
            b"Elise compression regression fixture."
            * 100
        )
        gzip_body = gzip.compress(
            payload
        )
        require(
            internet_module.decode_bounded_content_encoding(
                gzip_body,
                "gzip",
            )
            == payload,
            "Gzip decoding failed.",
        )
        deflate_body = zlib.compress(
            payload
        )
        require(
            internet_module.decode_bounded_content_encoding(
                deflate_body,
                "deflate",
            )
            == payload,
            "Deflate decoding failed.",
        )

        try:
            internet_module.decode_bounded_content_encoding(
                b"not-brotli",
                "br",
            )
        except internet_module.InternetError:
            pass
        else:
            raise AssertionError(
                "Unsupported Brotli encoding was accepted."
            )

    return (
        "Default-off, persistence, malformed settings, URL filtering, and "
        "compression passed"
    )



def check_workflow_store(
    project_root: Path,
) -> str:
    workflow_module = import_fresh(
        "workflow"
    )
    WorkflowStore = (
        workflow_module.WorkflowStore
    )
    WorkflowStatus = (
        workflow_module.WorkflowStatus
    )
    StepStatus = (
        workflow_module.StepStatus
    )
    WorkflowTransitionError = (
        workflow_module.WorkflowTransitionError
    )
    WorkflowValidationError = (
        workflow_module.WorkflowValidationError
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-workflow-",
        ignore_cleanup_errors=True,
    ) as temporary_directory:
        database = (
            Path(
                temporary_directory
            )
            / "workflows.db"
        )
        store = WorkflowStore(
            database
        )

        workflow = (
            store.create_read_summarize_write_workflow(
                source_path=(
                    "documents/project_notes.md"
                ),
                destination_path=(
                    "documents/next_steps.md"
                ),
            )
        )
        require(
            workflow.status
            is WorkflowStatus.PENDING,
            "New workflow did not start pending.",
        )
        require(
            len(
                workflow.steps
            )
            == 8,
            "Read-summarize-write template did not contain eight steps.",
        )
        require(
            [
                step.step_number
                for step in workflow.steps
            ]
            == list(
                range(
                    1,
                    9,
                )
            ),
            "Workflow step order is invalid.",
        )
        require(
            workflow.steps[
                4
            ].requires_confirmation
            is True,
            "Confirmation step lost its confirmation flag.",
        )

        try:
            store.create_read_summarize_write_workflow(
                source_path="../secret.txt",
                destination_path=(
                    "documents/out.md"
                ),
            )
        except WorkflowValidationError:
            pass
        else:
            raise AssertionError(
                "Unsafe workflow path was accepted."
            )

        started = store.start_workflow(
            workflow.id
        )
        require(
            started.status
            is WorkflowStatus.RUNNING,
            "Pending workflow did not start.",
        )
        require(
            started.current_step == 1,
            "Started workflow did not select step one.",
        )
        require(
            started.steps[
                0
            ].status
            is StepStatus.RUNNING,
            "First step did not become running.",
        )

        try:
            store.start_workflow(
                workflow.id
            )
        except WorkflowTransitionError:
            pass
        else:
            raise AssertionError(
                "Starting an already active workflow was accepted."
            )

        try:
            store.begin_step(
                workflow.id,
                3,
            )
        except WorkflowTransitionError:
            pass
        else:
            raise AssertionError(
                "Out-of-order step execution was accepted."
            )

        for step_number in (
            1,
            2,
            3,
            4,
        ):
            current = store.get_workflow(
                workflow.id
            )

            if (
                current.steps[
                    step_number
                    - 1
                ].status
                is StepStatus.PENDING
            ):
                store.begin_step(
                    workflow.id,
                    step_number,
                )

            store.complete_step(
                workflow.id,
                step_number,
                result_summary=(
                    f"Regression completed step {step_number}."
                ),
            )

        store.begin_step(
            workflow.id,
            5,
        )
        waiting = (
            store.wait_for_confirmation(
                workflow.id,
                5,
            )
        )
        require(
            waiting.status
            is WorkflowStatus.WAITING_FOR_CONFIRMATION,
            "Workflow did not pause for confirmation.",
        )
        require(
            waiting.steps[
                4
            ].status
            is StepStatus.RUNNING,
            "Confirmation pause changed the step out of running state.",
        )

        reopened = WorkflowStore(
            database
        )
        persisted = reopened.get_workflow(
            workflow.id
        )
        require(
            persisted.status
            is WorkflowStatus.WAITING_FOR_CONFIRMATION,
            "Confirmation pause did not persist after reopening.",
        )
        resumed = reopened.resume_workflow(
            workflow.id
        )
        require(
            resumed.status
            is WorkflowStatus.RUNNING,
            "Confirmation-waiting workflow did not resume.",
        )
        reopened.complete_step(
            workflow.id,
            5,
            result_summary=(
                "User confirmation recorded."
            ),
        )
        reopened.begin_step(
            workflow.id,
            6,
        )
        failed = reopened.fail_step(
            workflow.id,
            6,
            error=(
                "Regression write failure."
            ),
        )
        require(
            failed.status
            is WorkflowStatus.FAILED,
            "Failed step did not fail the workflow.",
        )
        require(
            failed.steps[
                5
            ].status
            is StepStatus.FAILED,
            "Active failed step was not marked failed.",
        )
        require(
            all(
                step.status
                is StepStatus.SKIPPED
                for step in failed.steps[
                    6:
                ]
            ),
            "Dependent steps were not skipped after failure.",
        )

        try:
            reopened.resume_workflow(
                workflow.id
            )
        except WorkflowTransitionError:
            pass
        else:
            raise AssertionError(
                "Failed workflow was allowed to resume."
            )

        cancelled_target = (
            reopened.create_read_summarize_write_workflow(
                source_path=(
                    "documents/another.md"
                ),
                destination_path=(
                    "documents/another-summary.md"
                ),
            )
        )
        cancelled = (
            reopened.cancel_workflow(
                cancelled_target.id
            )
        )
        require(
            cancelled.status
            is WorkflowStatus.CANCELLED,
            "Pending workflow did not cancel.",
        )
        require(
            all(
                step.status
                is StepStatus.SKIPPED
                for step in cancelled.steps
            ),
            "Cancelled workflow left unfinished steps active.",
        )

        completed_target = (
            reopened.create_workflow(
                original_request=(
                    "Complete two deterministic steps."
                ),
                workflow_type=(
                    "regression_complete"
                ),
                steps=[
                    {
                        "action_name": "one",
                        "display_name": "First",
                    },
                    {
                        "action_name": "two",
                        "display_name": "Second",
                    },
                ],
            )
        )
        reopened.start_workflow(
            completed_target.id
        )
        reopened.complete_step(
            completed_target.id,
            1,
        )
        reopened.begin_step(
            completed_target.id,
            2,
        )
        completed = reopened.complete_step(
            completed_target.id,
            2,
        )
        require(
            completed.status
            is WorkflowStatus.COMPLETED,
            "All-completed workflow did not become completed.",
        )
        require(
            completed.current_step
            is None,
            "Completed workflow retained a current step.",
        )

        secret = (
            "REGRESSION_SECRET_WORKFLOW_CONTENT"
        )
        redacted_target = (
            reopened.create_workflow(
                original_request=(
                    "Store a safely redacted argument."
                ),
                workflow_type=(
                    "regression_redaction"
                ),
                steps=[
                    {
                        "action_name": "redact",
                        "display_name": (
                            "Redact sensitive content"
                        ),
                        "arguments": {
                            "content": secret,
                            "safe_path": (
                                "documents/safe.md"
                            ),
                        },
                    }
                ],
            )
        )
        redacted_arguments = (
            redacted_target.steps[
                0
            ].arguments
        )
        require(
            secret
            not in json.dumps(
                redacted_arguments,
                sort_keys=True,
            ),
            "Sensitive workflow content was stored directly.",
        )
        require(
            redacted_arguments[
                "content"
            ][
                "redacted"
            ]
            is True,
            "Sensitive workflow argument lacks a redaction marker.",
        )
        require(
            len(
                redacted_arguments[
                    "content"
                ][
                    "sha256"
                ]
            )
            == 64,
            "Redacted workflow argument lacks a SHA-256 hash.",
        )

        events = reopened.list_events(
            workflow.id
        )
        require(
            events,
            "Workflow event history is empty.",
        )
        require(
            any(
                event.event_type
                == "waiting_for_confirmation"
                for event in events
            ),
            "Confirmation pause was not recorded as an event.",
        )
        require(
            any(
                event.event_type
                == "failed"
                for event in events
            ),
            "Workflow failure was not recorded as an event.",
        )
        require(
            reopened.count()
            >= 4,
            "Workflow count did not persist created records.",
        )
        require(
            reopened.list_recent(
                2
            ),
            "Recent workflow listing returned no records.",
        )

    return (
        "Creation, ordering, legal transitions, confirmation persistence, "
        "failure propagation, cancellation, completion, redaction, and "
        "event history passed"
    )



def check_workflow_execution(
    project_root: Path,
) -> str:
    workflow_module = import_fresh(
        "workflow"
    )
    execution_module = import_fresh(
        "workflow_execution"
    )
    tools_module = import_fresh(
        "tools"
    )
    audit_module = import_fresh(
        "audit"
    )

    WorkflowStore = workflow_module.WorkflowStore
    WorkflowStatus = workflow_module.WorkflowStatus
    StepStatus = workflow_module.StepStatus
    WorkflowExecutor = execution_module.WorkflowExecutor
    ToolManager = tools_module.ToolManager
    ToolAuditLog = audit_module.ToolAuditLog

    class FakeDocumentStore:
        def __init__(self) -> None:
            self.calls = 0

        def reindex(self) -> tuple[int, int]:
            self.calls += 1
            return 3, 5

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-workflow-execution-",
        ignore_cleanup_errors=True,
    ) as temporary_directory:
        temporary_root = Path(
            temporary_directory
        )
        documents = (
            temporary_root
            / "documents"
        )
        data = temporary_root / "data"
        documents.mkdir()
        data.mkdir()
        source = documents / "source.md"
        source.write_text(
            "Next tasks:\n- Build the parser.\n- Add regression tests.\n",
            encoding="utf-8",
        )

        workflow_store = WorkflowStore(
            data / "workflows.db"
        )
        tool_manager = ToolManager(
            project_directory=temporary_root,
            documents_directory=documents,
        )
        audit_log = ToolAuditLog(
            data / "tool_audit.db"
        )
        document_store = FakeDocumentStore()
        output: list[str] = []
        previews: list[dict[str, Any]] = []
        summary_text = (
            "# Next Steps\n\n"
            "- Build the parser.\n"
            "- Add regression tests."
        )

        workflow = (
            workflow_store
            .create_read_summarize_write_workflow(
                source_path="documents/source.md",
                destination_path="documents/summary.md",
            )
        )
        executor = WorkflowExecutor(
            workflow_store=workflow_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=document_store,
            summarize_source=(
                lambda source_path, source_text: summary_text
            ),
            render_write_preview=(
                lambda preview, policy: previews.append(
                    preview
                )
            ),
            request_confirmation=lambda: True,
            output=output.append,
        )
        completed = executor.execute(
            workflow.id
        )
        destination = documents / "summary.md"
        require(
            completed.status
            is WorkflowStatus.COMPLETED,
            "Approved workflow did not complete.",
        )
        require(
            destination.read_text(
                encoding="utf-8"
            )
            == summary_text + "\n",
            "Approved workflow wrote unexpected content.",
        )
        require(
            all(
                step.status
                is StepStatus.COMPLETED
                for step in completed.steps
            ),
            "Approved workflow left incomplete steps.",
        )
        require(
            len(previews) == 1,
            "Approved workflow did not display exactly one preview.",
        )
        require(
            document_store.calls == 1,
            "Approved workflow did not reindex exactly once.",
        )
        require(
            len(
                completed.steps[1].metadata.get(
                    "source_sha256",
                    "",
                )
            )
            == 64,
            "Source hash metadata was not persisted.",
        )
        require(
            len(
                completed.steps[3].metadata.get(
                    "content_sha256",
                    "",
                )
            )
            == 64,
            "Preview hash metadata was not persisted.",
        )
        require(
            audit_log.count() >= 3,
            "Workflow tool executions were not audited.",
        )

        denied = (
            workflow_store
            .create_read_summarize_write_workflow(
                source_path="documents/source.md",
                destination_path="documents/denied.md",
            )
        )
        denied_executor = WorkflowExecutor(
            workflow_store=workflow_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=FakeDocumentStore(),
            summarize_source=(
                lambda source_path, source_text: "# Next Steps\n\n- Denied."
            ),
            render_write_preview=(
                lambda preview, policy: None
            ),
            request_confirmation=lambda: False,
            output=lambda message: None,
        )
        denied_result = denied_executor.execute(
            denied.id
        )
        require(
            denied_result.status
            is WorkflowStatus.CANCELLED,
            "Denied workflow did not cancel.",
        )
        require(
            not (
                documents
                / "denied.md"
            ).exists(),
            "Denied workflow wrote a file.",
        )

        interrupted = (
            workflow_store
            .create_read_summarize_write_workflow(
                source_path="documents/source.md",
                destination_path="documents/resumed.md",
            )
        )

        def interrupt_confirmation() -> bool:
            raise KeyboardInterrupt()

        interrupted_executor = WorkflowExecutor(
            workflow_store=workflow_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=FakeDocumentStore(),
            summarize_source=(
                lambda source_path, source_text: "# Next Steps\n\n- Resume safely."
            ),
            render_write_preview=(
                lambda preview, policy: None
            ),
            request_confirmation=interrupt_confirmation,
            output=lambda message: None,
        )
        paused = interrupted_executor.execute(
            interrupted.id
        )
        require(
            paused.status
            is WorkflowStatus.WAITING_FOR_CONFIRMATION,
            "Interrupted workflow did not remain paused.",
        )

        reopened_store = WorkflowStore(
            data / "workflows.db"
        )
        resumed_executor = WorkflowExecutor(
            workflow_store=reopened_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=FakeDocumentStore(),
            summarize_source=(
                lambda source_path, source_text: "# Next Steps\n\n- Resume safely."
            ),
            render_write_preview=(
                lambda preview, policy: None
            ),
            request_confirmation=lambda: True,
            output=lambda message: None,
        )
        resumed = resumed_executor.execute(
            interrupted.id
        )
        require(
            resumed.status
            is WorkflowStatus.COMPLETED,
            "Interrupted workflow did not resume after reopening.",
        )
        require(
            (
                documents
                / "resumed.md"
            ).exists(),
            "Resumed workflow did not write its approved file.",
        )

        changed = (
            workflow_store
            .create_read_summarize_write_workflow(
                source_path="documents/source.md",
                destination_path="documents/changed.md",
            )
        )
        changed_executor = WorkflowExecutor(
            workflow_store=workflow_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=FakeDocumentStore(),
            summarize_source=(
                lambda source_path, source_text: "# Next Steps\n\n- Stable preview."
            ),
            render_write_preview=(
                lambda preview, policy: None
            ),
            request_confirmation=interrupt_confirmation,
            output=lambda message: None,
        )
        changed_paused = changed_executor.execute(
            changed.id
        )
        require(
            changed_paused.status
            is WorkflowStatus.WAITING_FOR_CONFIRMATION,
            "Source-change fixture did not pause.",
        )
        source.write_text(
            "Next tasks:\n- Source changed after preview.\n",
            encoding="utf-8",
        )
        changed_resume = WorkflowExecutor(
            workflow_store=workflow_store,
            tool_manager=tool_manager,
            audit_log=audit_log,
            document_store=FakeDocumentStore(),
            summarize_source=(
                lambda source_path, source_text: "# Next Steps\n\n- Stable preview."
            ),
            render_write_preview=(
                lambda preview, policy: None
            ),
            request_confirmation=lambda: True,
            output=lambda message: None,
        ).execute(
            changed.id
        )
        require(
            changed_resume.status
            is WorkflowStatus.FAILED,
            "Changed source did not fail closed on resume.",
        )
        require(
            not (
                documents
                / "changed.md"
            ).exists(),
            "Changed-source workflow wrote a file.",
        )

    return (
        "Approved execution, denial, audited writes, metadata-only "
        "persistence, restart resume, and source-change fail-closed passed"
    )


def check_app_pure_functions(
    project_root: Path,
) -> str:
    app_module = import_project_module(
        project_root,
        "app",
    )

    require(
        app_module.is_document_scoped_query(
            "According to notes.md, what is the project token?"
        )
        is True,
        "Document-scoped query detection failed.",
    )
    require(
        app_module.requires_forced_freshness_search(
            "What are the latest major updates to Python?"
        )
        is True,
        "Freshness routing failed for a latest-update question.",
    )
    require(
        app_module.identify_official_update_subject(
            "What are the latest Python updates?"
        )
        == "python",
        "Official subject routing failed for Python.",
    )
    require(
        app_module.parse_stable_version_candidate(
            "Python 3.14.6"
        )[
            "version"
        ]
        == "3.14.6",
        "Stable version parsing failed.",
    )
    require(
        app_module.parse_stable_version_candidate(
            "Python 3.15.0a1"
        )
        is None,
        "Prerelease version was treated as stable.",
    )

    bundle = {
        "selected_version": "3.14.6",
        "display_name": "Python",
        "pages": [
            {
                "citation": 1,
                "title": "Download Python",
                "final_url": (
                    "https://www.python.org/downloads/"
                ),
                "evidence_role": (
                    "official release discovery index"
                ),
                "text": (
                    "Latest Python 3 Release - Python 3.14.6"
                ),
            },
            {
                "citation": 3,
                "title": (
                    "What's New In Python 3.14"
                ),
                "final_url": (
                    "https://docs.python.org/3/"
                    "whatsnew/3.14.html"
                ),
                "evidence_role": (
                    "version-matched official update details"
                ),
                "text": (
                    "Template string literals provide custom "
                    "string processing. Deferred annotations "
                    "delay annotation processing."
                ),
            },
        ],
    }
    statement = (
        app_module
        .build_deterministic_official_version_statement(
            bundle
        )
    )
    require(
        statement.startswith(
            "Python 3.14.6 is the highest stable"
        ),
        "Deterministic stable-version statement changed unexpectedly.",
    )
    feature_result = (
        app_module.validate_official_feature_bullet(
            bullet=(
                "Template string literals provide custom string processing [3]."
            ),
            detail_pages=(
                app_module._official_detail_pages(
                    bundle
                )
            ),
        )
    )
    require(
        feature_result.get(
            "passed"
        )
        is True,
        "Known grounded feature bullet was rejected.",
    )
    invalid_feature = (
        app_module.validate_official_feature_bullet(
            bullet=(
                "Python 3.15 is currently a prerelease [1]."
            ),
            detail_pages=(
                app_module._official_detail_pages(
                    bundle
                )
            ),
        )
    )
    require(
        invalid_feature.get(
            "passed"
        )
        is False,
        "Status/comparison bullet was accepted.",
    )

    return (
        "Document routing, freshness routing, version parsing, deterministic "
        "assembly, and feature validation passed"
    )


def check_live_internet(
    project_root: Path,
) -> str:
    internet_module = import_fresh(
        "internet"
    )

    with tempfile.TemporaryDirectory(
        prefix="elise-regression-live-web-"
    ) as temporary_directory:
        manager = (
            internet_module.InternetManager(
                Path(
                    temporary_directory
                )
                / "settings.json"
            )
        )
        manager.set_enabled(
            True
        )
        result = manager.fetch_web_page(
            "https://www.python.org/downloads/",
            max_chars=5_000,
        )
        require(
            result.get(
                "success"
            )
            is True,
            "Live Python.org fetch failed: "
            + str(
                result.get(
                    "error",
                    "unknown error",
                )
            ),
        )
        require(
            result.get(
                "returned_chars",
                0,
            )
            > 500,
            "Live fetch returned too little readable text.",
        )
        require(
            result.get(
                "links"
            ),
            "Live fetch did not extract any links.",
        )

    return (
        "Live Python.org fetch, bounded text, and link extraction passed"
    )


def check_live_model(
    project_root: Path,
) -> str:
    app_module = import_project_module(
        project_root,
        "app",
    )
    ollama_module = import_fresh(
        "ollama"
    )

    response = ollama_module.chat(
        model=app_module.MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": (
                    "Reply with one short sentence confirming "
                    "that the local model is reachable."
                ),
            }
        ],
        think=False,
        options={
            "temperature": 0.0,
            "num_ctx": 1024,
        },
    )
    content = (
        response.message.content
        or ""
    ).strip()
    require(
        bool(
            content
        ),
        "Ollama returned an empty response.",
    )

    return (
        f"Model {app_module.MODEL_NAME!r} returned {len(content)} characters"
    )


def parse_groups(
    raw_groups: list[str],
    *,
    live_internet: bool,
    live_model: bool,
) -> set[str]:
    if not raw_groups:
        selected = set(
            DEFAULT_GROUPS
        )
    else:
        selected: set[str] = set()

        for raw_group in raw_groups:
            for group in raw_group.split(
                ","
            ):
                cleaned = group.strip().lower()

                if cleaned:
                    selected.add(
                        cleaned
                    )

    if live_internet:
        selected.add(
            "live-internet"
        )

    if live_model:
        selected.add(
            "live-model"
        )

    valid = (
        DEFAULT_GROUPS
        | OPTIONAL_GROUPS
    )
    unknown = sorted(
        selected
        - valid
    )

    if unknown:
        raise ValueError(
            "Unknown regression group(s): "
            + ", ".join(
                unknown
            )
        )

    return selected


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run isolated regression checks against the Elise project."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(
            __file__
        ).resolve().parent,
        help=(
            "Elise project directory. Defaults to the directory containing "
            "this script."
        ),
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help=(
            "Run selected groups only. Repeat the option or use commas. "
            "Groups: structure, memory, documents, tools, audit, internet, "
            "workflow, workflow-execution, app, live-internet, live-model."
        ),
    )
    parser.add_argument(
        "--live-internet",
        action="store_true",
        help=(
            "Also perform a real read-only fetch from Python.org."
        ),
    )
    parser.add_argument(
        "--live-model",
        action="store_true",
        help=(
            "Also send one minimal prompt to the configured Ollama model."
        ),
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help=(
            "Optional path for a machine-readable JSON report."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Show successful details and full failure tracebacks."
        ),
    )

    return parser


def main() -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args()
    project_root = (
        arguments.project_root
        .resolve()
    )

    try:
        selected_groups = parse_groups(
            arguments.group,
            live_internet=(
                arguments.live_internet
            ),
            live_model=(
                arguments.live_model
            ),
        )
    except ValueError as error:
        parser.error(
            str(
                error
            )
        )

    sys.path.insert(
        0,
        str(
            project_root
        ),
    )
    runner = RegressionRunner(
        project_root=project_root,
        selected_groups=(
            selected_groups
        ),
        verbose=arguments.verbose,
    )

    print(
        "="
        * 70
    )
    print(
        f"Elise Regression Suite {SUITE_VERSION}"
    )
    print(
        f"Project: {project_root}"
    )
    print(
        "Groups: "
        + ", ".join(
            sorted(
                selected_groups
            )
        )
    )
    print(
        "Isolation: temporary databases, documents, settings, and tool roots"
    )
    print(
        "Live internet: "
        + (
            "enabled"
            if "live-internet"
            in selected_groups
            else "disabled"
        )
    )
    print(
        "Live model: "
        + (
            "enabled"
            if "live-model"
            in selected_groups
            else "disabled"
        )
    )
    print(
        "="
        * 70
    )

    runner.run(
        "structure",
        "required project files",
        lambda: check_required_files(
            project_root
        ),
    )
    runner.run(
        "structure",
        "Python compilation",
        lambda: check_compile_project(
            project_root
        ),
    )
    runner.run(
        "structure",
        "Git worktree status",
        lambda: check_git_worktree(
            project_root
        ),
    )
    runner.run(
        "memory",
        "structured memory lifecycle",
        lambda: check_memory_store(
            project_root
        ),
    )
    runner.run(
        "documents",
        "local document indexing and search",
        lambda: check_document_store(
            project_root
        ),
    )
    runner.run(
        "tools",
        "filesystem policies and write safeguards",
        lambda: check_tool_manager(
            project_root
        ),
    )
    runner.run(
        "audit",
        "persistent audit redaction",
        lambda: check_audit_log(
            project_root
        ),
    )
    runner.run(
        "internet",
        "offline-safe settings, URL, and compression",
        lambda: check_internet_manager(
            project_root
        ),
    )
    runner.run(
        "workflow",
        "persistent workflow state machine",
        lambda: check_workflow_store(
            project_root
        ),
    )
    runner.run(
        "workflow-execution",
        "safe read-summarize-confirm-write execution",
        lambda: check_workflow_execution(
            project_root
        ),
    )
    runner.run(
        "app",
        "pure routing and grounding functions",
        lambda: check_app_pure_functions(
            project_root
        ),
    )
    runner.run(
        "live-internet",
        "real Python.org fetch",
        lambda: check_live_internet(
            project_root
        ),
    )
    runner.run(
        "live-model",
        "local Ollama response",
        lambda: check_live_model(
            project_root
        ),
    )

    report = runner.summary()

    if arguments.json_report is not None:
        report_path = (
            arguments.json_report
        )

        if not report_path.is_absolute():
            report_path = (
                project_root
                / report_path
            )

        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        report_path.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"\nJSON report: {report_path}"
        )

    counts = report[
        "counts"
    ]
    print(
        "\n"
        + "="
        * 70
    )
    print(
        "Summary: "
        f"{counts['PASS']} passed, "
        f"{counts['FAIL']} failed, "
        f"{counts['SKIP']} skipped"
    )
    print(
        "="
        * 70
    )

    if counts[
        "FAIL"
    ]:
        print(
            "Regression suite FAILED."
        )
        return 1

    print(
        "Regression suite PASSED."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
