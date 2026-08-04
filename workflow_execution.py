from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import PurePosixPath
from typing import Any, Callable, Sequence

from audit import ToolAuditLog
from tools import ToolManager
from workflow import (
    StepStatus,
    TERMINAL_WORKFLOW_STATUSES,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStore,
)


MAX_WORKFLOW_SOURCE_CHARS = 20_000
MAX_WORKFLOW_COMBINED_SOURCE_CHARS = 30_000
MAX_WORKFLOW_OUTPUT_CHARS = 8_000

SUPPORTED_WORKFLOW_OPERATIONS = {
    "document_summary",
    "action_items",
    "document_comparison",
}


class WorkflowExecutionError(Exception):
    """Raised when a workflow cannot safely continue."""


@dataclass(frozen=True)
class ResolvedWorkflowTemplate:
    operation: str
    source_paths: tuple[str, ...]
    destination_path: str
    locate_action: str
    read_action: str
    generate_action: str


@dataclass
class WorkflowExecutionContext:
    operation: str = ""
    source_paths: tuple[str, ...] = ()
    destination_path: str = ""
    source_texts: dict[str, str] = field(
        default_factory=dict
    )
    source_hashes: dict[str, str] = field(
        default_factory=dict
    )
    generated_text: str = ""
    generated_sha256: str = ""
    write_tool: str = ""
    preview: dict[str, Any] | None = None


class WorkflowExecutor:
    """
    Execute allowlisted workflow templates through existing safety layers.

    Full source and generated text remain in memory only. Persistent workflow
    metadata stores paths, lengths, hashes, and bounded result summaries.
    """

    def __init__(
        self,
        *,
        workflow_store: WorkflowStore,
        tool_manager: ToolManager,
        audit_log: ToolAuditLog,
        document_store: Any,
        generate_text: (
            Callable[
                [
                    str,
                    Sequence[tuple[str, str]],
                ],
                str,
            ]
            | None
        ) = None,
        summarize_source: (
            Callable[[str, str], str]
            | None
        ) = None,
        render_write_preview: Callable[[dict[str, Any], dict[str, Any]], None],
        request_confirmation: Callable[[], bool],
        output: Callable[[str], None] = print,
    ) -> None:
        if generate_text is None:
            if summarize_source is None:
                raise ValueError(
                    "WorkflowExecutor requires generate_text or summarize_source."
                )

            def legacy_generate_text(
                operation: str,
                sources: Sequence[
                    tuple[str, str]
                ],
            ) -> str:
                if len(sources) != 1:
                    raise WorkflowExecutionError(
                        "The legacy summarizer supports exactly one source."
                    )
                return summarize_source(
                    sources[0][0],
                    sources[0][1],
                )

            generate_text = legacy_generate_text

        self.workflow_store = workflow_store
        self.tool_manager = tool_manager
        self.audit_log = audit_log
        self.document_store = document_store
        self.generate_text = generate_text
        self.render_write_preview = render_write_preview
        self.request_confirmation = request_confirmation
        self.output = output

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _documents_path(value: Any) -> str:
        cleaned = (
            str(value)
            .strip()
            .replace("\\", "/")
            .strip("/")
        )
        parts = [
            part
            for part in cleaned.split("/")
            if part
        ]

        if (
            parts
            and parts[0].lower()
            == "documents"
        ):
            parts = parts[1:]

        if (
            not parts
            or any(
                part in {
                    ".",
                    "..",
                }
                for part in parts
            )
        ):
            raise WorkflowExecutionError(
                "Workflow paths must point to a file inside documents/."
            )

        return PurePosixPath(
            *parts
        ).as_posix()

    @staticmethod
    def _step(
        workflow: WorkflowRun,
        action_name: str,
    ):
        for step in workflow.steps:
            if (
                step.action_name
                == action_name
            ):
                return step

        raise WorkflowExecutionError(
            f"Workflow template is missing action {action_name!r}."
        )

    @staticmethod
    def _step_any(
        workflow: WorkflowRun,
        action_names: Sequence[str],
    ):
        for action_name in action_names:
            for step in workflow.steps:
                if (
                    step.action_name
                    == action_name
                ):
                    return step

        raise WorkflowExecutionError(
            "Workflow template is missing one of the required actions: "
            + ", ".join(
                repr(name)
                for name in action_names
            )
        )

    def _resolve_template(
        self,
        workflow: WorkflowRun,
    ) -> ResolvedWorkflowTemplate:
        if (
            workflow.workflow_type
            == "read_summarize_write"
        ):
            locate_step = self._step(
                workflow,
                "locate_source",
            )
            source_paths = (
                self._documents_path(
                    locate_step.arguments.get(
                        "source_path"
                    )
                ),
            )
            destination_step = self._step(
                workflow,
                "preview_destination",
            )
            destination = self._documents_path(
                destination_step.arguments.get(
                    "destination_path"
                )
            )
            return ResolvedWorkflowTemplate(
                operation="action_items",
                source_paths=source_paths,
                destination_path=destination,
                locate_action="locate_source",
                read_action="read_source",
                generate_action="summarize_source",
            )

        operation_by_type = {
            "document_summary": (
                "document_summary"
            ),
            "document_action_items": (
                "action_items"
            ),
            "compare_documents": (
                "document_comparison"
            ),
        }
        operation = operation_by_type.get(
            workflow.workflow_type
        )

        if operation is None:
            raise WorkflowExecutionError(
                "Unsupported workflow type: "
                + workflow.workflow_type
            )

        locate_step = self._step(
            workflow,
            "locate_sources",
        )
        raw_sources = locate_step.arguments.get(
            "source_paths",
            [],
        )

        if not isinstance(
            raw_sources,
            list,
        ):
            raise WorkflowExecutionError(
                "Workflow source paths are malformed."
            )

        source_paths = tuple(
            self._documents_path(
                path
            )
            for path in raw_sources
        )

        expected_count = (
            2
            if operation
            == "document_comparison"
            else 1
        )

        if (
            len(source_paths)
            != expected_count
        ):
            raise WorkflowExecutionError(
                f"{workflow.workflow_type} requires "
                f"exactly {expected_count} source file(s)."
            )

        if (
            len(set(source_paths))
            != len(source_paths)
        ):
            raise WorkflowExecutionError(
                "Workflow source files must be distinct."
            )

        destination_step = self._step(
            workflow,
            "preview_destination",
        )
        destination = self._documents_path(
            destination_step.arguments.get(
                "destination_path"
            )
        )

        if destination in source_paths:
            raise WorkflowExecutionError(
                "The destination cannot overwrite a source file."
            )

        return ResolvedWorkflowTemplate(
            operation=operation,
            source_paths=source_paths,
            destination_path=destination,
            locate_action="locate_sources",
            read_action="read_sources",
            generate_action="generate_output",
        )

    @staticmethod
    def _summary_for_tool(
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        if not result.get(
            "success"
        ):
            return (
                "failed: "
                + str(
                    result.get(
                        "error",
                        "unknown tool error",
                    )
                )
            )

        if tool_name == "list_files":
            return (
                "success: "
                f"{len(result.get('entries', []))} entries"
            )

        if tool_name == "read_text_file":
            return (
                f"success: {result.get('root')}:"
                f"{result.get('path')} "
                f"({result.get('size_bytes')} bytes)"
            )

        if tool_name in {
            "create_text_file",
            "replace_text_file",
        }:
            return (
                f"success: {tool_name} "
                f"{result.get('root')}:"
                f"{result.get('path')} "
                f"({result.get('bytes_written')} bytes)"
            )

        return "success"

    def _audit_tool(
        self,
        *,
        workflow: WorkflowRun,
        tool_name: str,
        arguments: dict[str, Any],
        approved: bool,
        result: dict[str, Any],
    ) -> None:
        policy = (
            self.tool_manager
            .get_tool_policy(
                tool_name
            )
        )
        self.audit_log.record(
            source="workflow_executor",
            request_text=(
                workflow.original_request
            ),
            tool_name=tool_name,
            arguments=arguments,
            policy=policy,
            approved=approved,
            result=result,
            result_summary=(
                self._summary_for_tool(
                    tool_name,
                    result,
                )
            ),
        )

    def _execute_tool(
        self,
        *,
        workflow: WorkflowRun,
        tool_name: str,
        arguments: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        policy = (
            self.tool_manager
            .get_tool_policy(
                tool_name
            )
        )
        requires_confirmation = bool(
            policy.get(
                "requires_confirmation",
                True,
            )
        )
        permission_mode = str(
            policy.get(
                "permission_mode",
                "blocked",
            )
        )
        approved = (
            confirmed
            and requires_confirmation
            and permission_mode
            == "confirmation"
        ) or (
            not requires_confirmation
            and permission_mode
            == "automatic"
        )

        if not approved:
            result = {
                "success": False,
                "tool": tool_name,
                "error": (
                    "Workflow tool execution "
                    "was not approved by policy."
                ),
            }
        else:
            try:
                result = (
                    self.tool_manager.execute(
                        tool_name=tool_name,
                        arguments=arguments,
                        confirmed=confirmed,
                    )
                )
            except Exception as error:
                result = {
                    "success": False,
                    "tool": tool_name,
                    "error": str(
                        error
                    ),
                }

        self._audit_tool(
            workflow=workflow,
            tool_name=tool_name,
            arguments=arguments,
            approved=approved,
            result=result,
        )
        return result

    def _audit_denied_write(
        self,
        *,
        workflow: WorkflowRun,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        result = {
            "success": False,
            "tool": tool_name,
            "error": (
                "User denied workflow "
                "write confirmation."
            ),
        }
        self._audit_tool(
            workflow=workflow,
            tool_name=tool_name,
            arguments=arguments,
            approved=False,
            result=result,
        )

    def _locate_sources(
        self,
        workflow: WorkflowRun,
        template: ResolvedWorkflowTemplate,
        context: WorkflowExecutionContext,
    ) -> None:
        context.operation = (
            template.operation
        )
        context.source_paths = (
            template.source_paths
        )
        context.destination_path = (
            template.destination_path
        )

        for source_path in (
            template.source_paths
        ):
            parent = (
                PurePosixPath(
                    source_path
                )
                .parent
                .as_posix()
            )

            if parent == ".":
                parent = "."

            result = self._execute_tool(
                workflow=workflow,
                tool_name="list_files",
                arguments={
                    "root": "documents",
                    "path": parent,
                    "recursive": False,
                },
            )

            if not result.get(
                "success"
            ):
                raise WorkflowExecutionError(
                    str(
                        result.get(
                            "error"
                        )
                    )
                )

            expected = source_path.replace(
                "\\",
                "/",
            )
            found = any(
                str(
                    entry.get(
                        "path",
                        "",
                    )
                )
                .replace(
                    "\\",
                    "/",
                )
                == expected
                and entry.get(
                    "type"
                )
                == "file"
                for entry in result.get(
                    "entries",
                    [],
                )
            )

            if not found:
                raise WorkflowExecutionError(
                    "Source file "
                    f"documents/{source_path} "
                    "was not found."
                )

    @staticmethod
    def _expected_source_hashes(
        read_step: Any,
        source_paths: Sequence[str],
    ) -> dict[str, str]:
        raw_hashes = read_step.metadata.get(
            "source_hashes"
        )

        if isinstance(
            raw_hashes,
            dict,
        ):
            return {
                str(
                    key
                ): str(
                    value
                )
                for (
                    key,
                    value,
                ) in raw_hashes.items()
            }

        legacy_hash = str(
            read_step.metadata.get(
                "source_sha256",
                "",
            )
        )

        if (
            legacy_hash
            and len(
                source_paths
            )
            == 1
        ):
            return {
                source_paths[0]: (
                    legacy_hash
                ),
            }

        return {}

    def _read_sources(
        self,
        workflow: WorkflowRun,
        template: ResolvedWorkflowTemplate,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_hashes: bool,
    ) -> list[dict[str, Any]]:
        if not context.source_paths:
            context.operation = (
                template.operation
            )
            context.source_paths = (
                template.source_paths
            )
            context.destination_path = (
                template.destination_path
            )

        read_step = self._step(
            workflow,
            template.read_action,
        )
        expected_hashes = (
            self._expected_source_hashes(
                read_step,
                context.source_paths,
            )
            if verify_persisted_hashes
            else {}
        )
        total_chars = 0
        results: list[
            dict[str, Any]
        ] = []

        for source_path in (
            context.source_paths
        ):
            result = self._execute_tool(
                workflow=workflow,
                tool_name="read_text_file",
                arguments={
                    "root": "documents",
                    "path": source_path,
                    "max_chars": (
                        MAX_WORKFLOW_SOURCE_CHARS
                    ),
                },
            )

            if not result.get(
                "success"
            ):
                raise WorkflowExecutionError(
                    str(
                        result.get(
                            "error"
                        )
                    )
                )

            if result.get(
                "truncated"
            ):
                raise WorkflowExecutionError(
                    "A source exceeds the "
                    "20,000-character workflow limit. "
                    "Chunked workflow processing "
                    "is not enabled yet."
                )

            content = str(
                result.get(
                    "content",
                    "",
                )
            )
            total_chars += len(
                content
            )

            if (
                total_chars
                > MAX_WORKFLOW_COMBINED_SOURCE_CHARS
            ):
                raise WorkflowExecutionError(
                    "Combined workflow sources exceed "
                    "the 30,000-character limit."
                )

            source_hash = (
                self._sha256(
                    content
                )
            )
            expected_hash = (
                expected_hashes.get(
                    source_path,
                    "",
                )
            )

            if (
                expected_hash
                and source_hash
                != expected_hash
            ):
                raise WorkflowExecutionError(
                    "A source file changed after "
                    "the workflow read it. "
                    "Create a new workflow so the "
                    "preview matches current content."
                )

            context.source_texts[
                source_path
            ] = content
            context.source_hashes[
                source_path
            ] = source_hash
            results.append(
                result
            )

        return results

    @staticmethod
    def _required_heading(
        workflow_type: str,
        operation: str,
    ) -> str:
        if (
            workflow_type
            == "read_summarize_write"
        ):
            return "# Next Steps"

        return {
            "document_summary": (
                "# Document Summary"
            ),
            "action_items": (
                "# Action Items"
            ),
            "document_comparison": (
                "# Document Comparison"
            ),
        }[
            operation
        ]

    def _generate_output(
        self,
        workflow: WorkflowRun,
        template: ResolvedWorkflowTemplate,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_hash: bool,
    ) -> str:
        if (
            len(
                context.source_texts
            )
            != len(
                template.source_paths
            )
        ):
            self._read_sources(
                workflow,
                template,
                context,
                verify_persisted_hashes=True,
            )

        sources = [
            (
                f"documents/{path}",
                context.source_texts[
                    path
                ],
            )
            for path in (
                template.source_paths
            )
        ]

        try:
            generated = (
                self.generate_text(
                    template.operation,
                    sources,
                )
            )
        except Exception as error:
            raise WorkflowExecutionError(
                "Local workflow text "
                f"generation failed: {error}"
            ) from error

        generated = str(
            generated
        ).strip()

        if not generated:
            raise WorkflowExecutionError(
                "The local model returned "
                "empty workflow output."
            )

        required_heading = (
            self._required_heading(
                workflow.workflow_type,
                template.operation,
            )
        )

        if not generated.startswith(
            required_heading
        ):
            generated = (
                required_heading
                + "\n\n"
                + generated
            )

        if (
            len(
                generated
            )
            > MAX_WORKFLOW_OUTPUT_CHARS
        ):
            raise WorkflowExecutionError(
                "The generated workflow output "
                "exceeded the 8,000-character "
                "write limit."
            )

        generated += "\n"
        generated_hash = (
            self._sha256(
                generated
            )
        )

        if verify_persisted_hash:
            generate_step = self._step(
                workflow,
                template.generate_action,
            )
            expected_hash = str(
                generate_step.metadata.get(
                    "generated_sha256",
                    generate_step.metadata.get(
                        "summary_sha256",
                        "",
                    ),
                )
            )

            if (
                expected_hash
                and generated_hash
                != expected_hash
            ):
                raise WorkflowExecutionError(
                    "The regenerated workflow output "
                    "did not match the prior preview. "
                    "Create a new workflow before writing."
                )

        context.generated_text = (
            generated
        )
        context.generated_sha256 = (
            generated_hash
        )
        return generated

    def _build_preview(
        self,
        workflow: WorkflowRun,
        template: ResolvedWorkflowTemplate,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_preview: bool,
    ) -> dict[str, Any]:
        if not context.generated_text:
            self._generate_output(
                workflow,
                template,
                context,
                verify_persisted_hash=True,
            )

        context.destination_path = (
            template.destination_path
        )
        arguments = {
            "root": "documents",
            "path": (
                context.destination_path
            ),
            "content": (
                context.generated_text
            ),
        }

        create_preview = (
            self.tool_manager
            .preview_tool_action(
                tool_name=(
                    "create_text_file"
                ),
                arguments=arguments,
            )
        )

        if create_preview.get(
            "success"
        ):
            tool_name = (
                "create_text_file"
            )
            preview = create_preview
        else:
            replace_preview = (
                self.tool_manager
                .preview_tool_action(
                    tool_name=(
                        "replace_text_file"
                    ),
                    arguments=arguments,
                )
            )

            if not replace_preview.get(
                "success"
            ):
                raise WorkflowExecutionError(
                    "Destination preview failed: "
                    + str(
                        replace_preview.get(
                            "error",
                            create_preview.get(
                                "error"
                            ),
                        )
                    )
                )

            tool_name = (
                "replace_text_file"
            )
            preview = replace_preview

        if verify_persisted_preview:
            preview_step = self._step(
                workflow,
                "preview_destination",
            )
            expected_tool = str(
                preview_step.metadata.get(
                    "write_tool",
                    "",
                )
            )
            expected_hash = str(
                preview_step.metadata.get(
                    "content_sha256",
                    "",
                )
            )
            expected_size = (
                preview_step.metadata.get(
                    "current_size_bytes"
                )
            )

            if (
                expected_tool
                and expected_tool
                != tool_name
            ):
                raise WorkflowExecutionError(
                    "The destination existence changed "
                    "after preview. Create a new "
                    "workflow before writing."
                )

            if (
                expected_size
                is not None
                and int(
                    expected_size
                )
                != int(
                    preview.get(
                        "current_size_bytes",
                        0,
                    )
                )
            ):
                raise WorkflowExecutionError(
                    "The destination file size changed "
                    "after preview. Create a new "
                    "workflow before writing."
                )

            if (
                expected_hash
                and expected_hash
                != context.generated_sha256
            ):
                raise WorkflowExecutionError(
                    "The pending write content no "
                    "longer matches its preview."
                )

        context.write_tool = tool_name
        context.preview = preview
        return preview

    def _fail_current_step(
        self,
        workflow_id: int,
        error: Exception,
    ) -> WorkflowRun:
        workflow = (
            self.workflow_store
            .get_workflow(
                workflow_id
            )
        )

        if (
            workflow.status
            in TERMINAL_WORKFLOW_STATUSES
        ):
            return workflow

        if (
            workflow.current_step
            is None
        ):
            return workflow

        step = workflow.steps[
            workflow.current_step - 1
        ]

        if (
            step.status
            is StepStatus.PENDING
        ):
            workflow = (
                self.workflow_store
                .begin_step(
                    workflow_id,
                    step.step_number,
                )
            )

        return (
            self.workflow_store
            .fail_step(
                workflow_id,
                int(
                    workflow.current_step
                    or step.step_number
                ),
                error=str(
                    error
                ),
            )
        )

    @staticmethod
    def _source_result_summary(
        context: WorkflowExecutionContext,
    ) -> str:
        if (
            len(
                context.source_paths
            )
            == 1
        ):
            return (
                "Located documents/"
                + context.source_paths[0]
                + "."
            )

        return (
            "Located "
            f"{len(context.source_paths)} "
            "source files: "
            + ", ".join(
                "documents/" + path
                for path in (
                    context.source_paths
                )
            )
            + "."
        )

    @staticmethod
    def _read_result_summary(
        results: Sequence[
            dict[str, Any]
        ],
    ) -> str:
        total_chars = sum(
            int(
                result.get(
                    "returned_chars",
                    0,
                )
                or 0
            )
            for result in results
        )
        return (
            f"Read {len(results)} source file(s), "
            f"{total_chars} characters total, "
            "without truncation."
        )

    def execute(
        self,
        workflow_id: int,
    ) -> WorkflowRun:
        workflow_id = int(
            workflow_id
        )
        context = (
            WorkflowExecutionContext()
        )
        workflow = (
            self.workflow_store
            .get_workflow(
                workflow_id
            )
        )

        if (
            workflow.status
            in TERMINAL_WORKFLOW_STATUSES
        ):
            self.output(
                f"Elise: Workflow #{workflow_id} "
                f"is already {workflow.status.value}."
            )
            return workflow

        try:
            template = (
                self._resolve_template(
                    workflow
                )
            )
            context.operation = (
                template.operation
            )
            context.source_paths = (
                template.source_paths
            )
            context.destination_path = (
                template.destination_path
            )

            if (
                workflow.status
                is WorkflowStatus.PENDING
            ):
                workflow = (
                    self.workflow_store
                    .start_workflow(
                        workflow_id
                    )
                )
                self.output(
                    f"Elise: Workflow "
                    f"#{workflow_id} started."
                )

            while True:
                workflow = (
                    self.workflow_store
                    .get_workflow(
                        workflow_id
                    )
                )

                if (
                    workflow.status
                    in TERMINAL_WORKFLOW_STATUSES
                ):
                    return workflow

                if (
                    workflow.status
                    is WorkflowStatus.WAITING_FOR_CONFIRMATION
                ):
                    self._read_sources(
                        workflow,
                        template,
                        context,
                        verify_persisted_hashes=True,
                    )
                    self._generate_output(
                        workflow,
                        template,
                        context,
                        verify_persisted_hash=True,
                    )
                    preview = (
                        self._build_preview(
                            workflow,
                            template,
                            context,
                            verify_persisted_preview=True,
                        )
                    )
                else:
                    if (
                        workflow.current_step
                        is None
                    ):
                        raise WorkflowExecutionError(
                            "Active workflow has "
                            "no current step."
                        )

                    step = workflow.steps[
                        workflow.current_step
                        - 1
                    ]

                    if (
                        step.status
                        is StepStatus.PENDING
                    ):
                        workflow = (
                            self.workflow_store
                            .begin_step(
                                workflow_id,
                                step.step_number,
                            )
                        )
                        step = workflow.steps[
                            step.step_number
                            - 1
                        ]

                    total_steps = len(
                        workflow.steps
                    )

                    if (
                        step.action_name
                        == template.locate_action
                    ):
                        self._locate_sources(
                            workflow,
                            template,
                            context,
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                self._source_result_summary(
                                    context
                                )
                            ),
                            metadata={
                                "root": "documents",
                                "source_paths": list(
                                    context.source_paths
                                ),
                            },
                        )
                        self.output(
                            "Elise: Step "
                            f"{step.step_number}/"
                            f"{total_steps} located "
                            "the source file(s)."
                        )
                        continue

                    if (
                        step.action_name
                        == template.read_action
                    ):
                        results = (
                            self._read_sources(
                                workflow,
                                template,
                                context,
                                verify_persisted_hashes=False,
                            )
                        )
                        metadata = {
                            "source_hashes": dict(
                                context.source_hashes
                            ),
                            "source_count": len(
                                context.source_paths
                            ),
                            "total_returned_chars": sum(
                                int(
                                    result.get(
                                        "returned_chars",
                                        0,
                                    )
                                    or 0
                                )
                                for result in results
                            ),
                            "truncated": False,
                        }

                        if (
                            len(
                                context.source_paths
                            )
                            == 1
                        ):
                            only_path = (
                                context.source_paths[
                                    0
                                ]
                            )
                            metadata[
                                "source_sha256"
                            ] = (
                                context.source_hashes[
                                    only_path
                                ]
                            )

                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                self._read_result_summary(
                                    results
                                )
                            ),
                            metadata=metadata,
                        )
                        self.output(
                            "Elise: Step "
                            f"{step.step_number}/"
                            f"{total_steps} read "
                            "the source file(s)."
                        )
                        continue

                    if (
                        step.action_name
                        == template.generate_action
                    ):
                        self._generate_output(
                            workflow,
                            template,
                            context,
                            verify_persisted_hash=False,
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                "Generated "
                                f"{len(context.generated_text)} "
                                "characters of "
                                f"{template.operation} output."
                            ),
                            metadata={
                                "operation": (
                                    template.operation
                                ),
                                "source_hashes": dict(
                                    context.source_hashes
                                ),
                                "generated_sha256": (
                                    context.generated_sha256
                                ),
                                "summary_sha256": (
                                    context.generated_sha256
                                ),
                                "generated_chars": len(
                                    context.generated_text
                                ),
                            },
                        )
                        self.output(
                            "Elise: Step "
                            f"{step.step_number}/"
                            f"{total_steps} generated "
                            "the grounded output."
                        )
                        continue

                    if (
                        step.action_name
                        == "preview_destination"
                    ):
                        preview = (
                            self._build_preview(
                                workflow,
                                template,
                                context,
                                verify_persisted_preview=False,
                            )
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                "Prepared "
                                f"{context.write_tool} "
                                "preview for documents/"
                                f"{context.destination_path}."
                            ),
                            metadata={
                                "write_tool": (
                                    context.write_tool
                                ),
                                "destination_path": (
                                    context.destination_path
                                ),
                                "content_sha256": (
                                    context.generated_sha256
                                ),
                                "content_chars": len(
                                    context.generated_text
                                ),
                                "target_existed": bool(
                                    preview.get(
                                        "target_exists"
                                    )
                                ),
                                "current_size_bytes": int(
                                    preview.get(
                                        "current_size_bytes",
                                        0,
                                    )
                                ),
                            },
                        )
                        self.output(
                            "Elise: Step "
                            f"{step.step_number}/"
                            f"{total_steps} prepared "
                            "the write preview."
                        )
                        continue

                    if (
                        step.action_name
                        == "confirm_write"
                    ):
                        preview = (
                            self._build_preview(
                                workflow,
                                template,
                                context,
                                verify_persisted_preview=True,
                            )
                        )
                        workflow = (
                            self.workflow_store
                            .wait_for_confirmation(
                                workflow_id,
                                step.step_number,
                                summary=(
                                    "Waiting for explicit "
                                    "workflow write confirmation."
                                ),
                            )
                        )

                policy = (
                    self.tool_manager
                    .get_tool_policy(
                        context.write_tool
                    )
                )
                self.render_write_preview(
                    preview,
                    policy,
                )
                self.output(
                    f"Elise: Workflow "
                    f"#{workflow_id} is waiting "
                    "for write confirmation."
                )

                try:
                    approved = (
                        self.request_confirmation()
                    )
                except (
                    KeyboardInterrupt,
                    EOFError,
                ):
                    self.output(
                        "Elise: Confirmation was "
                        "interrupted. The workflow "
                        "remains paused and can be "
                        "resumed later."
                    )
                    return (
                        self.workflow_store
                        .get_workflow(
                            workflow_id
                        )
                    )

                arguments = {
                    "root": "documents",
                    "path": (
                        context.destination_path
                    ),
                    "content": (
                        context.generated_text
                    ),
                }

                if not approved:
                    self._audit_denied_write(
                        workflow=workflow,
                        tool_name=(
                            context.write_tool
                        ),
                        arguments=arguments,
                    )
                    cancelled = (
                        self.workflow_store
                        .cancel_workflow(
                            workflow_id,
                            reason=(
                                "Workflow write was "
                                "denied by the user."
                            ),
                        )
                    )
                    self.output(
                        f"Elise: Workflow "
                        f"#{workflow_id} cancelled; "
                        "no file was written."
                    )
                    return cancelled

                confirmation_step = (
                    self._step(
                        workflow,
                        "confirm_write",
                    )
                )
                self.workflow_store.resume_workflow(
                    workflow_id
                )
                self.workflow_store.complete_step(
                    workflow_id,
                    confirmation_step.step_number,
                    result_summary=(
                        "User approved the exact "
                        "displayed write preview."
                    ),
                    metadata={
                        "approved": True,
                        "write_tool": (
                            context.write_tool
                        ),
                        "content_sha256": (
                            context.generated_sha256
                        ),
                    },
                )

                write_step = self._step(
                    self.workflow_store.get_workflow(
                        workflow_id
                    ),
                    "write_destination",
                )
                self.workflow_store.begin_step(
                    workflow_id,
                    write_step.step_number,
                )
                workflow = (
                    self.workflow_store
                    .get_workflow(
                        workflow_id
                    )
                )
                result = self._execute_tool(
                    workflow=workflow,
                    tool_name=(
                        context.write_tool
                    ),
                    arguments=arguments,
                    confirmed=True,
                )

                if not result.get(
                    "success"
                ):
                    raise WorkflowExecutionError(
                        "Confirmed write failed: "
                        + str(
                            result.get(
                                "error"
                            )
                        )
                    )

                self.workflow_store.complete_step(
                    workflow_id,
                    write_step.step_number,
                    result_summary=(
                        f"Wrote "
                        f"{result.get('bytes_written')} "
                        "bytes to documents/"
                        f"{context.destination_path}."
                    ),
                    metadata={
                        "write_tool": (
                            context.write_tool
                        ),
                        "destination_path": (
                            context.destination_path
                        ),
                        "content_sha256": (
                            result.get(
                                "content_sha256"
                            )
                        ),
                        "bytes_written": (
                            result.get(
                                "bytes_written"
                            )
                        ),
                        "chars_written": (
                            result.get(
                                "chars_written"
                            )
                        ),
                    },
                )
                self.output(
                    "Elise: Step "
                    f"{write_step.step_number}/"
                    f"{len(workflow.steps)} wrote "
                    "the confirmed output."
                )

                reindex_workflow = (
                    self.workflow_store
                    .get_workflow(
                        workflow_id
                    )
                )
                reindex_step = self._step(
                    reindex_workflow,
                    "reindex_documents",
                )
                self.workflow_store.begin_step(
                    workflow_id,
                    reindex_step.step_number,
                )

                try:
                    (
                        file_count,
                        chunk_count,
                    ) = (
                        self.document_store
                        .reindex()
                    )
                except Exception as error:
                    raise WorkflowExecutionError(
                        "The file was written, but "
                        "document reindexing failed: "
                        f"{error}"
                    ) from error

                self.workflow_store.complete_step(
                    workflow_id,
                    reindex_step.step_number,
                    result_summary=(
                        f"Reindexed {file_count} files "
                        f"and {chunk_count} sections."
                    ),
                    metadata={
                        "file_count": int(
                            file_count
                        ),
                        "chunk_count": int(
                            chunk_count
                        ),
                    },
                )
                self.output(
                    "Elise: Step "
                    f"{reindex_step.step_number}/"
                    f"{len(workflow.steps)} refreshed "
                    "document search."
                )

                report_workflow = (
                    self.workflow_store
                    .get_workflow(
                        workflow_id
                    )
                )
                report_step = self._step(
                    report_workflow,
                    "report_completion",
                )
                self.workflow_store.begin_step(
                    workflow_id,
                    report_step.step_number,
                )
                completed = (
                    self.workflow_store
                    .complete_step(
                        workflow_id,
                        report_step.step_number,
                        result_summary=(
                            "Completed "
                            f"{template.operation} "
                            "workflow and saved "
                            "documents/"
                            f"{context.destination_path}."
                        ),
                        metadata={
                            "operation": (
                                template.operation
                            ),
                            "destination_path": (
                                context.destination_path
                            ),
                            "content_sha256": (
                                context.generated_sha256
                            ),
                        },
                    )
                )
                self.output(
                    f"Elise: Workflow "
                    f"#{workflow_id} completed "
                    "successfully."
                )
                return completed

        except WorkflowExecutionError as error:
            failed = (
                self._fail_current_step(
                    workflow_id,
                    error,
                )
            )
            self.output(
                f"Elise: Workflow "
                f"#{workflow_id} failed: {error}"
            )
            return failed
        except Exception as error:
            wrapped = WorkflowExecutionError(
                "Unexpected workflow "
                f"execution error: {error}"
            )
            failed = (
                self._fail_current_step(
                    workflow_id,
                    wrapped,
                )
            )
            self.output(
                f"Elise: Workflow "
                f"#{workflow_id} failed: {wrapped}"
            )
            return failed
