from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePath
import sqlite3
from typing import Any, Iterable, Sequence


MAX_REQUEST_CHARS = 4_000
MAX_ACTION_NAME_CHARS = 100
MAX_DISPLAY_NAME_CHARS = 200
MAX_RESULT_SUMMARY_CHARS = 1_000
MAX_ERROR_CHARS = 1_000
MAX_EVENT_SUMMARY_CHARS = 1_000
MAX_ARGUMENTS_JSON_CHARS = 8_000

SENSITIVE_ARGUMENT_KEYS = {
    "content",
    "file_content",
    "new_text",
    "old_text",
    "prompt",
    "raw_content",
    "response",
    "source_text",
    "summary",
    "text",
}


class WorkflowError(Exception):
    """Base class for workflow errors."""


class WorkflowNotFoundError(WorkflowError):
    """Raised when a requested workflow or step does not exist."""


class WorkflowTransitionError(WorkflowError):
    """Raised when a state transition is not legal."""


class WorkflowValidationError(WorkflowError):
    """Raised when workflow input is malformed or unsafe."""


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = (
        "waiting_for_confirmation"
    )
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_WORKFLOW_STATUSES = {
    WorkflowStatus.COMPLETED,
    WorkflowStatus.FAILED,
    WorkflowStatus.CANCELLED,
}

TERMINAL_STEP_STATUSES = {
    StepStatus.COMPLETED,
    StepStatus.FAILED,
    StepStatus.SKIPPED,
}


@dataclass(frozen=True)
class WorkflowStep:
    id: int
    workflow_id: int
    step_number: int
    action_name: str
    display_name: str
    arguments: dict[str, Any]
    status: StepStatus
    requires_confirmation: bool
    result_summary: str | None
    error: str | None
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class WorkflowRun:
    id: int
    workflow_type: str
    original_request: str
    status: WorkflowStatus
    current_step: int | None
    created_at: str
    updated_at: str
    completed_at: str | None
    error: str | None
    steps: tuple[WorkflowStep, ...]


@dataclass(frozen=True)
class WorkflowEvent:
    id: int
    workflow_id: int
    created_at: str
    event_type: str
    step_number: int | None
    summary: str


class WorkflowStore:
    """
    Persistent workflow state with fail-closed transition validation.

    Connections are opened per operation so Windows can release database
    handles immediately after each transaction.
    """

    def __init__(
        self,
        database_path: str | Path,
    ) -> None:
        self.database_path = Path(
            database_path
        )
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    @staticmethod
    def _now() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )

    @staticmethod
    def _truncate(
        value: str,
        maximum: int,
    ) -> str:
        value = str(
            value
        )

        if len(
            value
        ) <= maximum:
            return value

        return (
            value[
                : maximum - 3
            ]
            + "..."
        )

    def _connect(
        self,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path
        )
        connection.row_factory = (
            sqlite3.Row
        )
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        return connection

    def _initialize(
        self,
    ) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_type TEXT NOT NULL,
                    original_request TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER NOT NULL,
                    step_number INTEGER NOT NULL,
                    action_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    result_summary TEXT,
                    error TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_id)
                        REFERENCES workflows(id)
                        ON DELETE CASCADE,
                    UNIQUE (workflow_id, step_number)
                );

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step_number INTEGER,
                    summary TEXT NOT NULL,
                    FOREIGN KEY (workflow_id)
                        REFERENCES workflows(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS
                    idx_workflows_updated_at
                    ON workflows(updated_at DESC);

                CREATE INDEX IF NOT EXISTS
                    idx_workflow_steps_workflow
                    ON workflow_steps(
                        workflow_id,
                        step_number
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_workflow_events_workflow
                    ON workflow_events(
                        workflow_id,
                        id
                    );
                """
            )

    @classmethod
    def _redact_value(
        cls,
        key: str,
        value: Any,
    ) -> Any:
        normalized_key = key.strip().lower()

        if (
            normalized_key
            in SENSITIVE_ARGUMENT_KEYS
        ):
            serialized = (
                value
                if isinstance(
                    value,
                    str,
                )
                else json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
            encoded = serialized.encode(
                "utf-8"
            )

            return {
                "redacted": True,
                "characters": len(
                    serialized
                ),
                "sha256": hashlib.sha256(
                    encoded
                ).hexdigest(),
            }

        if isinstance(
            value,
            dict,
        ):
            return {
                str(
                    child_key
                ): cls._redact_value(
                    str(
                        child_key
                    ),
                    child_value,
                )
                for (
                    child_key,
                    child_value,
                ) in value.items()
            }

        if isinstance(
            value,
            (
                list,
                tuple,
            ),
        ):
            return [
                cls._redact_value(
                    key,
                    child_value,
                )
                for child_value in value
            ]

        if isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            )
        ) or value is None:
            return value

        return str(
            value
        )

    @classmethod
    def sanitize_arguments(
        cls,
        arguments: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if arguments is None:
            return {}

        if not isinstance(
            arguments,
            dict,
        ):
            raise WorkflowValidationError(
                "Step arguments must be a dictionary."
            )

        sanitized = {
            str(
                key
            ): cls._redact_value(
                str(
                    key
                ),
                value,
            )
            for (
                key,
                value,
            ) in arguments.items()
        }
        serialized = json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        if len(
            serialized
        ) > MAX_ARGUMENTS_JSON_CHARS:
            raise WorkflowValidationError(
                "Sanitized step arguments are too large."
            )

        return sanitized

    @staticmethod
    def _validate_relative_path(
        value: str,
        *,
        label: str,
    ) -> str:
        cleaned = value.strip().replace(
            "\\",
            "/",
        )

        if not cleaned:
            raise WorkflowValidationError(
                f"{label} cannot be empty."
            )

        path = PurePath(
            cleaned
        )

        if path.is_absolute():
            raise WorkflowValidationError(
                f"{label} must be relative."
            )

        if any(
            part
            in {
                "",
                ".",
                "..",
            }
            for part in path.parts
        ):
            raise WorkflowValidationError(
                f"{label} contains an unsafe path component."
            )

        return path.as_posix()

    @staticmethod
    def _validate_step_specs(
        steps: Sequence[
            dict[str, Any]
        ],
    ) -> None:
        if not steps:
            raise WorkflowValidationError(
                "A workflow must contain at least one step."
            )

        for index, step in enumerate(
            steps,
            start=1,
        ):
            if not isinstance(
                step,
                dict,
            ):
                raise WorkflowValidationError(
                    f"Step {index} must be a dictionary."
                )

            action_name = str(
                step.get(
                    "action_name",
                    "",
                )
            ).strip()
            display_name = str(
                step.get(
                    "display_name",
                    "",
                )
            ).strip()

            if not action_name:
                raise WorkflowValidationError(
                    f"Step {index} requires an action name."
                )

            if not display_name:
                raise WorkflowValidationError(
                    f"Step {index} requires a display name."
                )

            if len(
                action_name
            ) > MAX_ACTION_NAME_CHARS:
                raise WorkflowValidationError(
                    f"Step {index} action name is too long."
                )

            if len(
                display_name
            ) > MAX_DISPLAY_NAME_CHARS:
                raise WorkflowValidationError(
                    f"Step {index} display name is too long."
                )

            WorkflowStore.sanitize_arguments(
                step.get(
                    "arguments",
                    {},
                )
            )

    @staticmethod
    def _step_from_row(
        row: sqlite3.Row,
    ) -> WorkflowStep:
        try:
            arguments = json.loads(
                row[
                    "arguments_json"
                ]
            )
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            arguments = {
                "error": (
                    "Stored arguments could not be decoded."
                )
            }

        return WorkflowStep(
            id=int(
                row[
                    "id"
                ]
            ),
            workflow_id=int(
                row[
                    "workflow_id"
                ]
            ),
            step_number=int(
                row[
                    "step_number"
                ]
            ),
            action_name=str(
                row[
                    "action_name"
                ]
            ),
            display_name=str(
                row[
                    "display_name"
                ]
            ),
            arguments=arguments,
            status=StepStatus(
                row[
                    "status"
                ]
            ),
            requires_confirmation=bool(
                row[
                    "requires_confirmation"
                ]
            ),
            result_summary=(
                None
                if row[
                    "result_summary"
                ]
                is None
                else str(
                    row[
                        "result_summary"
                    ]
                )
            ),
            error=(
                None
                if row[
                    "error"
                ]
                is None
                else str(
                    row[
                        "error"
                    ]
                )
            ),
            started_at=(
                None
                if row[
                    "started_at"
                ]
                is None
                else str(
                    row[
                        "started_at"
                    ]
                )
            ),
            completed_at=(
                None
                if row[
                    "completed_at"
                ]
                is None
                else str(
                    row[
                        "completed_at"
                    ]
                )
            ),
            updated_at=str(
                row[
                    "updated_at"
                ]
            ),
        )

    def _workflow_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> WorkflowRun:
        step_rows = connection.execute(
            """
            SELECT *
            FROM workflow_steps
            WHERE workflow_id = ?
            ORDER BY step_number
            """,
            (
                int(
                    row[
                        "id"
                    ]
                ),
            ),
        ).fetchall()
        steps = tuple(
            self._step_from_row(
                step_row
            )
            for step_row in step_rows
        )

        return WorkflowRun(
            id=int(
                row[
                    "id"
                ]
            ),
            workflow_type=str(
                row[
                    "workflow_type"
                ]
            ),
            original_request=str(
                row[
                    "original_request"
                ]
            ),
            status=WorkflowStatus(
                row[
                    "status"
                ]
            ),
            current_step=(
                None
                if row[
                    "current_step"
                ]
                is None
                else int(
                    row[
                        "current_step"
                    ]
                )
            ),
            created_at=str(
                row[
                    "created_at"
                ]
            ),
            updated_at=str(
                row[
                    "updated_at"
                ]
            ),
            completed_at=(
                None
                if row[
                    "completed_at"
                ]
                is None
                else str(
                    row[
                        "completed_at"
                    ]
                )
            ),
            error=(
                None
                if row[
                    "error"
                ]
                is None
                else str(
                    row[
                        "error"
                    ]
                )
            ),
            steps=steps,
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        workflow_id: int,
        event_type: str,
        summary: str,
        step_number: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_events (
                workflow_id,
                created_at,
                event_type,
                step_number,
                summary
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                WorkflowStore._now(),
                event_type.strip(),
                step_number,
                WorkflowStore._truncate(
                    summary.strip(),
                    MAX_EVENT_SUMMARY_CHARS,
                ),
            ),
        )

    @staticmethod
    def _get_workflow_row(
        connection: sqlite3.Connection,
        workflow_id: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM workflows
            WHERE id = ?
            """,
            (
                workflow_id,
            ),
        ).fetchone()

        if row is None:
            raise WorkflowNotFoundError(
                f"Workflow {workflow_id} does not exist."
            )

        return row

    @staticmethod
    def _get_step_row(
        connection: sqlite3.Connection,
        workflow_id: int,
        step_number: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM workflow_steps
            WHERE workflow_id = ?
              AND step_number = ?
            """,
            (
                workflow_id,
                step_number,
            ),
        ).fetchone()

        if row is None:
            raise WorkflowNotFoundError(
                f"Workflow {workflow_id} has no step {step_number}."
            )

        return row

    def count(
        self,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM workflows
                """
            ).fetchone()

        return int(
            row[
                "count"
            ]
        )

    def create_workflow(
        self,
        *,
        original_request: str,
        workflow_type: str,
        steps: Sequence[
            dict[str, Any]
        ],
    ) -> WorkflowRun:
        request = original_request.strip()
        workflow_type = (
            workflow_type.strip()
        )

        if not request:
            raise WorkflowValidationError(
                "The original request cannot be empty."
            )

        if len(
            request
        ) > MAX_REQUEST_CHARS:
            raise WorkflowValidationError(
                "The original request is too long."
            )

        if not workflow_type:
            raise WorkflowValidationError(
                "The workflow type cannot be empty."
            )

        self._validate_step_specs(
            steps
        )
        now = self._now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO workflows (
                    workflow_type,
                    original_request,
                    status,
                    current_step,
                    created_at,
                    updated_at,
                    completed_at,
                    error
                )
                VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL)
                """,
                (
                    workflow_type,
                    request,
                    WorkflowStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            workflow_id = int(
                cursor.lastrowid
            )

            for step_number, step in enumerate(
                steps,
                start=1,
            ):
                sanitized_arguments = (
                    self.sanitize_arguments(
                        step.get(
                            "arguments",
                            {},
                        )
                    )
                )
                connection.execute(
                    """
                    INSERT INTO workflow_steps (
                        workflow_id,
                        step_number,
                        action_name,
                        display_name,
                        arguments_json,
                        status,
                        requires_confirmation,
                        result_summary,
                        error,
                        started_at,
                        completed_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
                    """,
                    (
                        workflow_id,
                        step_number,
                        str(
                            step[
                                "action_name"
                            ]
                        ).strip(),
                        str(
                            step[
                                "display_name"
                            ]
                        ).strip(),
                        json.dumps(
                            sanitized_arguments,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        StepStatus.PENDING.value,
                        int(
                            bool(
                                step.get(
                                    "requires_confirmation",
                                    False,
                                )
                            )
                        ),
                        now,
                    ),
                )

            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="created",
                summary=(
                    f"Created {workflow_type} workflow "
                    f"with {len(steps)} steps."
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def create_read_summarize_write_workflow(
        self,
        *,
        source_path: str,
        destination_path: str,
        original_request: str | None = None,
    ) -> WorkflowRun:
        source = self._validate_relative_path(
            source_path,
            label="Source path",
        )
        destination = self._validate_relative_path(
            destination_path,
            label="Destination path",
        )

        if source == destination:
            raise WorkflowValidationError(
                "Source and destination paths must differ."
            )

        request = (
            original_request.strip()
            if original_request
            else (
                f"Read {source}, summarize its next tasks, "
                f"and save the summary to {destination}."
            )
        )

        steps = [
            {
                "action_name": "locate_source",
                "display_name": (
                    "Locate the source file"
                ),
                "arguments": {
                    "source_path": source,
                },
            },
            {
                "action_name": "read_source",
                "display_name": (
                    "Read the source file"
                ),
                "arguments": {
                    "source_path": source,
                },
            },
            {
                "action_name": "summarize_source",
                "display_name": (
                    "Generate a source-grounded summary"
                ),
                "arguments": {
                    "source_path": source,
                },
            },
            {
                "action_name": "preview_destination",
                "display_name": (
                    "Preview the destination file"
                ),
                "arguments": {
                    "destination_path": destination,
                },
            },
            {
                "action_name": "confirm_write",
                "display_name": (
                    "Wait for write confirmation"
                ),
                "arguments": {
                    "destination_path": destination,
                },
                "requires_confirmation": True,
            },
            {
                "action_name": "write_destination",
                "display_name": (
                    "Write the confirmed summary"
                ),
                "arguments": {
                    "destination_path": destination,
                },
            },
            {
                "action_name": "reindex_documents",
                "display_name": (
                    "Reindex local documents"
                ),
                "arguments": {
                    "destination_path": destination,
                },
            },
            {
                "action_name": "report_completion",
                "display_name": (
                    "Report workflow completion"
                ),
                "arguments": {},
            },
        ]

        return self.create_workflow(
            original_request=request,
            workflow_type=(
                "read_summarize_write"
            ),
            steps=steps,
        )

    def get_workflow(
        self,
        workflow_id: int,
    ) -> WorkflowRun:
        with self._connect() as connection:
            row = self._get_workflow_row(
                connection,
                int(
                    workflow_id
                ),
            )

            return self._workflow_from_row(
                connection,
                row,
            )

    def list_recent(
        self,
        limit: int = 20,
    ) -> list[WorkflowRun]:
        safe_limit = max(
            1,
            min(
                int(
                    limit
                ),
                100,
            ),
        )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM workflows
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (
                    safe_limit,
                ),
            ).fetchall()

            return [
                self._workflow_from_row(
                    connection,
                    row,
                )
                for row in rows
            ]

    def list_events(
        self,
        workflow_id: int,
        limit: int = 100,
    ) -> list[WorkflowEvent]:
        safe_limit = max(
            1,
            min(
                int(
                    limit
                ),
                500,
            ),
        )

        with self._connect() as connection:
            self._get_workflow_row(
                connection,
                int(
                    workflow_id
                ),
            )
            rows = connection.execute(
                """
                SELECT *
                FROM workflow_events
                WHERE workflow_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    int(
                        workflow_id
                    ),
                    safe_limit,
                ),
            ).fetchall()

        return [
            WorkflowEvent(
                id=int(
                    row[
                        "id"
                    ]
                ),
                workflow_id=int(
                    row[
                        "workflow_id"
                    ]
                ),
                created_at=str(
                    row[
                        "created_at"
                    ]
                ),
                event_type=str(
                    row[
                        "event_type"
                    ]
                ),
                step_number=(
                    None
                    if row[
                        "step_number"
                    ]
                    is None
                    else int(
                        row[
                            "step_number"
                        ]
                    )
                ),
                summary=str(
                    row[
                        "summary"
                    ]
                ),
            )
            for row in rows
        ]

    def start_workflow(
        self,
        workflow_id: int,
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        now = self._now()

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )
            status = WorkflowStatus(
                workflow[
                    "status"
                ]
            )

            if status is not WorkflowStatus.PENDING:
                raise WorkflowTransitionError(
                    "Only a pending workflow can be started."
                )

            first_step = connection.execute(
                """
                SELECT *
                FROM workflow_steps
                WHERE workflow_id = ?
                  AND status = ?
                ORDER BY step_number
                LIMIT 1
                """,
                (
                    workflow_id,
                    StepStatus.PENDING.value,
                ),
            ).fetchone()

            if first_step is None:
                raise WorkflowTransitionError(
                    "The workflow has no pending step to start."
                )

            first_number = int(
                first_step[
                    "step_number"
                ]
            )
            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND step_number = ?
                """,
                (
                    StepStatus.RUNNING.value,
                    now,
                    now,
                    workflow_id,
                    first_number,
                ),
            )
            connection.execute(
                """
                UPDATE workflows
                SET status = ?,
                    current_step = ?,
                    updated_at = ?,
                    error = NULL
                WHERE id = ?
                """,
                (
                    WorkflowStatus.RUNNING.value,
                    first_number,
                    now,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="started",
                step_number=first_number,
                summary=(
                    f"Started workflow at step {first_number}."
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def begin_step(
        self,
        workflow_id: int,
        step_number: int,
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        step_number = int(
            step_number
        )
        now = self._now()

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )

            if WorkflowStatus(
                workflow[
                    "status"
                ]
            ) is not WorkflowStatus.RUNNING:
                raise WorkflowTransitionError(
                    "A step can begin only while the workflow is running."
                )

            running = connection.execute(
                """
                SELECT step_number
                FROM workflow_steps
                WHERE workflow_id = ?
                  AND status = ?
                """,
                (
                    workflow_id,
                    StepStatus.RUNNING.value,
                ),
            ).fetchone()

            if running is not None:
                raise WorkflowTransitionError(
                    f"Step {int(running['step_number'])} is already running."
                )

            step = self._get_step_row(
                connection,
                workflow_id,
                step_number,
            )

            if StepStatus(
                step[
                    "status"
                ]
            ) is not StepStatus.PENDING:
                raise WorkflowTransitionError(
                    "Only a pending step can begin."
                )

            incomplete_previous = (
                connection.execute(
                    """
                    SELECT step_number
                    FROM workflow_steps
                    WHERE workflow_id = ?
                      AND step_number < ?
                      AND status NOT IN (?, ?)
                    ORDER BY step_number
                    LIMIT 1
                    """,
                    (
                        workflow_id,
                        step_number,
                        StepStatus.COMPLETED.value,
                        StepStatus.SKIPPED.value,
                    ),
                ).fetchone()
            )

            if incomplete_previous is not None:
                raise WorkflowTransitionError(
                    "Earlier dependent steps are not complete."
                )

            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND step_number = ?
                """,
                (
                    StepStatus.RUNNING.value,
                    now,
                    now,
                    workflow_id,
                    step_number,
                ),
            )
            connection.execute(
                """
                UPDATE workflows
                SET current_step = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    step_number,
                    now,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="step_started",
                step_number=step_number,
                summary=(
                    f"Started step {step_number}."
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def complete_step(
        self,
        workflow_id: int,
        step_number: int,
        *,
        result_summary: str = "",
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        step_number = int(
            step_number
        )
        now = self._now()
        summary = self._truncate(
            result_summary.strip(),
            MAX_RESULT_SUMMARY_CHARS,
        )

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )
            workflow_status = WorkflowStatus(
                workflow[
                    "status"
                ]
            )

            if workflow_status not in {
                WorkflowStatus.RUNNING,
                WorkflowStatus.WAITING_FOR_CONFIRMATION,
            }:
                raise WorkflowTransitionError(
                    "A step can complete only in an active workflow."
                )

            step = self._get_step_row(
                connection,
                workflow_id,
                step_number,
            )

            if StepStatus(
                step[
                    "status"
                ]
            ) is not StepStatus.RUNNING:
                raise WorkflowTransitionError(
                    "Only a running step can be completed."
                )

            if (
                workflow_status
                is WorkflowStatus.WAITING_FOR_CONFIRMATION
                and not bool(
                    step[
                        "requires_confirmation"
                    ]
                )
            ):
                raise WorkflowTransitionError(
                    "The waiting step is not confirmation-gated."
                )

            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    result_summary = ?,
                    error = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND step_number = ?
                """,
                (
                    StepStatus.COMPLETED.value,
                    summary
                    or None,
                    now,
                    now,
                    workflow_id,
                    step_number,
                ),
            )
            next_step = connection.execute(
                """
                SELECT step_number
                FROM workflow_steps
                WHERE workflow_id = ?
                  AND status = ?
                ORDER BY step_number
                LIMIT 1
                """,
                (
                    workflow_id,
                    StepStatus.PENDING.value,
                ),
            ).fetchone()

            if next_step is None:
                connection.execute(
                    """
                    UPDATE workflows
                    SET status = ?,
                        current_step = NULL,
                        updated_at = ?,
                        completed_at = ?,
                        error = NULL
                    WHERE id = ?
                    """,
                    (
                        WorkflowStatus.COMPLETED.value,
                        now,
                        now,
                        workflow_id,
                    ),
                )
                event_type = (
                    "completed"
                )
                event_summary = (
                    "Completed all workflow steps."
                )
            else:
                next_number = int(
                    next_step[
                        "step_number"
                    ]
                )
                connection.execute(
                    """
                    UPDATE workflows
                    SET status = ?,
                        current_step = ?,
                        updated_at = ?,
                        error = NULL
                    WHERE id = ?
                    """,
                    (
                        WorkflowStatus.RUNNING.value,
                        next_number,
                        now,
                        workflow_id,
                    ),
                )
                event_type = (
                    "step_completed"
                )
                event_summary = (
                    f"Completed step {step_number}; "
                    f"next step is {next_number}."
                )

            self._event(
                connection,
                workflow_id=workflow_id,
                event_type=event_type,
                step_number=step_number,
                summary=event_summary,
            )

        return self.get_workflow(
            workflow_id
        )

    def wait_for_confirmation(
        self,
        workflow_id: int,
        step_number: int,
        *,
        summary: str = (
            "Waiting for explicit user confirmation."
        ),
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        step_number = int(
            step_number
        )
        now = self._now()

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )

            if WorkflowStatus(
                workflow[
                    "status"
                ]
            ) is not WorkflowStatus.RUNNING:
                raise WorkflowTransitionError(
                    "Only a running workflow can wait for confirmation."
                )

            step = self._get_step_row(
                connection,
                workflow_id,
                step_number,
            )

            if StepStatus(
                step[
                    "status"
                ]
            ) is not StepStatus.RUNNING:
                raise WorkflowTransitionError(
                    "Only a running step can wait for confirmation."
                )

            if not bool(
                step[
                    "requires_confirmation"
                ]
            ):
                raise WorkflowTransitionError(
                    "This step does not require confirmation."
                )

            connection.execute(
                """
                UPDATE workflows
                SET status = ?,
                    current_step = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    WorkflowStatus.WAITING_FOR_CONFIRMATION.value,
                    step_number,
                    now,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type=(
                    "waiting_for_confirmation"
                ),
                step_number=step_number,
                summary=self._truncate(
                    summary,
                    MAX_EVENT_SUMMARY_CHARS,
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def resume_workflow(
        self,
        workflow_id: int,
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )

        workflow = self.get_workflow(
            workflow_id
        )

        if (
            workflow.status
            is WorkflowStatus.PENDING
        ):
            return self.start_workflow(
                workflow_id
            )

        if (
            workflow.status
            is not WorkflowStatus.WAITING_FOR_CONFIRMATION
        ):
            raise WorkflowTransitionError(
                "Only a pending or confirmation-waiting workflow can resume."
            )

        now = self._now()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workflows
                SET status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    WorkflowStatus.RUNNING.value,
                    now,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="resumed",
                step_number=(
                    workflow.current_step
                ),
                summary=(
                    "Resumed workflow after confirmation."
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def fail_step(
        self,
        workflow_id: int,
        step_number: int,
        *,
        error: str,
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        step_number = int(
            step_number
        )
        error_text = self._truncate(
            error.strip()
            or "Unknown workflow failure.",
            MAX_ERROR_CHARS,
        )
        now = self._now()

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )
            status = WorkflowStatus(
                workflow[
                    "status"
                ]
            )

            if status in TERMINAL_WORKFLOW_STATUSES:
                raise WorkflowTransitionError(
                    "A terminal workflow cannot fail again."
                )

            step = self._get_step_row(
                connection,
                workflow_id,
                step_number,
            )
            step_status = StepStatus(
                step[
                    "status"
                ]
            )

            if step_status not in {
                StepStatus.PENDING,
                StepStatus.RUNNING,
            }:
                raise WorkflowTransitionError(
                    "Only a pending or running step can fail."
                )

            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    error = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND step_number = ?
                """,
                (
                    StepStatus.FAILED.value,
                    error_text,
                    now,
                    now,
                    workflow_id,
                    step_number,
                ),
            )
            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    result_summary = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND status = ?
                """,
                (
                    StepStatus.SKIPPED.value,
                    (
                        "Skipped because an earlier "
                        "dependent step failed."
                    ),
                    now,
                    now,
                    workflow_id,
                    StepStatus.PENDING.value,
                ),
            )
            connection.execute(
                """
                UPDATE workflows
                SET status = ?,
                    current_step = ?,
                    updated_at = ?,
                    completed_at = ?,
                    error = ?
                WHERE id = ?
                """,
                (
                    WorkflowStatus.FAILED.value,
                    step_number,
                    now,
                    now,
                    error_text,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="failed",
                step_number=step_number,
                summary=(
                    f"Workflow failed at step {step_number}: "
                    + error_text
                ),
            )

        return self.get_workflow(
            workflow_id
        )

    def cancel_workflow(
        self,
        workflow_id: int,
        *,
        reason: str = "Cancelled by the user.",
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        reason_text = self._truncate(
            reason.strip()
            or "Cancelled by the user.",
            MAX_RESULT_SUMMARY_CHARS,
        )
        now = self._now()

        with self._connect() as connection:
            workflow = self._get_workflow_row(
                connection,
                workflow_id,
            )
            status = WorkflowStatus(
                workflow[
                    "status"
                ]
            )

            if status in TERMINAL_WORKFLOW_STATUSES:
                raise WorkflowTransitionError(
                    "A completed, failed, or cancelled workflow cannot be cancelled."
                )

            connection.execute(
                """
                UPDATE workflow_steps
                SET status = ?,
                    result_summary = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE workflow_id = ?
                  AND status IN (?, ?)
                """,
                (
                    StepStatus.SKIPPED.value,
                    reason_text,
                    now,
                    now,
                    workflow_id,
                    StepStatus.PENDING.value,
                    StepStatus.RUNNING.value,
                ),
            )
            connection.execute(
                """
                UPDATE workflows
                SET status = ?,
                    current_step = NULL,
                    updated_at = ?,
                    completed_at = ?,
                    error = NULL
                WHERE id = ?
                """,
                (
                    WorkflowStatus.CANCELLED.value,
                    now,
                    now,
                    workflow_id,
                ),
            )
            self._event(
                connection,
                workflow_id=workflow_id,
                event_type="cancelled",
                summary=reason_text,
            )

        return self.get_workflow(
            workflow_id
        )