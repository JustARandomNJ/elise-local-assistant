from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
from typing import Any, Callable

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
MAX_WORKFLOW_SUMMARY_CHARS = 8_000


class WorkflowExecutionError(Exception):
    """Raised when a workflow cannot safely continue."""


@dataclass
class WorkflowExecutionContext:
    source_path: str = ""
    destination_path: str = ""
    source_text: str = ""
    source_sha256: str = ""
    summary_text: str = ""
    summary_sha256: str = ""
    write_tool: str = ""
    preview: dict[str, Any] | None = None


class WorkflowExecutor:
    """
    Execute one allowlisted workflow template through existing safety layers.

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
        summarize_source: Callable[[str, str], str],
        render_write_preview: Callable[[dict[str, Any], dict[str, Any]], None],
        request_confirmation: Callable[[], bool],
        output: Callable[[str], None] = print,
    ) -> None:
        self.workflow_store = workflow_store
        self.tool_manager = tool_manager
        self.audit_log = audit_log
        self.document_store = document_store
        self.summarize_source = summarize_source
        self.render_write_preview = render_write_preview
        self.request_confirmation = request_confirmation
        self.output = output

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _documents_path(value: Any) -> str:
        cleaned = str(value).strip().replace("\\", "/").strip("/")
        parts = [part for part in cleaned.split("/") if part]

        if parts and parts[0].lower() == "documents":
            parts = parts[1:]

        if not parts or any(part in {".", ".."} for part in parts):
            raise WorkflowExecutionError(
                "Workflow paths must point to a file inside documents/."
            )

        return PurePosixPath(*parts).as_posix()

    @staticmethod
    def _step(workflow: WorkflowRun, action_name: str):
        for step in workflow.steps:
            if step.action_name == action_name:
                return step
        raise WorkflowExecutionError(
            f"Workflow template is missing action {action_name!r}."
        )

    @staticmethod
    def _summary_for_tool(tool_name: str, result: dict[str, Any]) -> str:
        if not result.get("success"):
            return "failed: " + str(result.get("error", "unknown tool error"))
        if tool_name == "list_files":
            return f"success: {len(result.get('entries', []))} entries"
        if tool_name == "read_text_file":
            return (
                f"success: {result.get('root')}:{result.get('path')} "
                f"({result.get('size_bytes')} bytes)"
            )
        if tool_name in {"create_text_file", "replace_text_file"}:
            return (
                f"success: {tool_name} {result.get('root')}:"
                f"{result.get('path')} ({result.get('bytes_written')} bytes)"
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
        policy = self.tool_manager.get_tool_policy(tool_name)
        self.audit_log.record(
            source="workflow_executor",
            request_text=workflow.original_request,
            tool_name=tool_name,
            arguments=arguments,
            policy=policy,
            approved=approved,
            result=result,
            result_summary=self._summary_for_tool(tool_name, result),
        )

    def _execute_tool(
        self,
        *,
        workflow: WorkflowRun,
        tool_name: str,
        arguments: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        policy = self.tool_manager.get_tool_policy(tool_name)
        requires_confirmation = bool(policy.get("requires_confirmation", True))
        permission_mode = str(policy.get("permission_mode", "blocked"))
        approved = (
            confirmed and requires_confirmation and permission_mode == "confirmation"
        ) or (
            not requires_confirmation and permission_mode == "automatic"
        )

        if not approved:
            result = {
                "success": False,
                "tool": tool_name,
                "error": "Workflow tool execution was not approved by policy.",
            }
        else:
            try:
                result = self.tool_manager.execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    confirmed=confirmed,
                )
            except Exception as error:
                result = {
                    "success": False,
                    "tool": tool_name,
                    "error": str(error),
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
            "error": "User denied workflow write confirmation.",
        }
        self._audit_tool(
            workflow=workflow,
            tool_name=tool_name,
            arguments=arguments,
            approved=False,
            result=result,
        )

    def _locate_source(
        self,
        workflow: WorkflowRun,
        context: WorkflowExecutionContext,
    ) -> dict[str, Any]:
        source_step = self._step(workflow, "locate_source")
        source_path = self._documents_path(source_step.arguments.get("source_path"))
        context.source_path = source_path
        parent = PurePosixPath(source_path).parent.as_posix()
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
        if not result.get("success"):
            raise WorkflowExecutionError(str(result.get("error")))

        expected = source_path.replace("\\", "/")
        found = any(
            str(entry.get("path", "")).replace("\\", "/") == expected
            and entry.get("type") == "file"
            for entry in result.get("entries", [])
        )
        if not found:
            raise WorkflowExecutionError(
                f"Source file documents/{source_path} was not found."
            )
        return result

    def _read_source(
        self,
        workflow: WorkflowRun,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_hash: bool,
    ) -> dict[str, Any]:
        if not context.source_path:
            source_step = self._step(workflow, "read_source")
            context.source_path = self._documents_path(
                source_step.arguments.get("source_path")
            )
        result = self._execute_tool(
            workflow=workflow,
            tool_name="read_text_file",
            arguments={
                "root": "documents",
                "path": context.source_path,
                "max_chars": MAX_WORKFLOW_SOURCE_CHARS,
            },
        )
        if not result.get("success"):
            raise WorkflowExecutionError(str(result.get("error")))
        if result.get("truncated"):
            raise WorkflowExecutionError(
                "The source exceeds the 20,000-character workflow limit. "
                "Chunked summarization is not enabled yet."
            )

        content = str(result.get("content", ""))
        source_hash = self._sha256(content)
        if verify_persisted_hash:
            read_step = self._step(workflow, "read_source")
            expected_hash = str(read_step.metadata.get("source_sha256", ""))
            if expected_hash and source_hash != expected_hash:
                raise WorkflowExecutionError(
                    "The source file changed after the workflow read it. "
                    "Create a new workflow so the preview matches current content."
                )

        context.source_text = content
        context.source_sha256 = source_hash
        return result

    def _generate_summary(
        self,
        workflow: WorkflowRun,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_hash: bool,
    ) -> str:
        if not context.source_text:
            self._read_source(
                workflow,
                context,
                verify_persisted_hash=True,
            )
        try:
            summary = self.summarize_source(
                f"documents/{context.source_path}",
                context.source_text,
            )
        except Exception as error:
            raise WorkflowExecutionError(
                f"Local summary generation failed: {error}"
            ) from error

        summary = str(summary).strip()
        if not summary:
            raise WorkflowExecutionError("The local model returned an empty summary.")
        if len(summary) > MAX_WORKFLOW_SUMMARY_CHARS:
            raise WorkflowExecutionError(
                "The generated summary exceeded the 8,000-character write limit."
            )
        summary += "\n"
        summary_hash = self._sha256(summary)

        if verify_persisted_hash:
            summary_step = self._step(workflow, "summarize_source")
            expected_hash = str(summary_step.metadata.get("summary_sha256", ""))
            if expected_hash and summary_hash != expected_hash:
                raise WorkflowExecutionError(
                    "The regenerated summary did not match the prior workflow "
                    "summary. Create a new workflow before writing."
                )

        context.summary_text = summary
        context.summary_sha256 = summary_hash
        return summary

    def _build_preview(
        self,
        workflow: WorkflowRun,
        context: WorkflowExecutionContext,
        *,
        verify_persisted_preview: bool,
    ) -> dict[str, Any]:
        if not context.summary_text:
            self._generate_summary(
                workflow,
                context,
                verify_persisted_hash=True,
            )
        destination_step = self._step(workflow, "preview_destination")
        destination_path = self._documents_path(
            destination_step.arguments.get("destination_path")
        )
        context.destination_path = destination_path
        arguments = {
            "root": "documents",
            "path": destination_path,
            "content": context.summary_text,
        }

        create_preview = self.tool_manager.preview_tool_action(
            tool_name="create_text_file",
            arguments=arguments,
        )
        if create_preview.get("success"):
            tool_name = "create_text_file"
            preview = create_preview
        else:
            replace_preview = self.tool_manager.preview_tool_action(
                tool_name="replace_text_file",
                arguments=arguments,
            )
            if not replace_preview.get("success"):
                raise WorkflowExecutionError(
                    "Destination preview failed: "
                    + str(replace_preview.get("error", create_preview.get("error")))
                )
            tool_name = "replace_text_file"
            preview = replace_preview

        if verify_persisted_preview:
            preview_step = self._step(workflow, "preview_destination")
            expected_tool = str(preview_step.metadata.get("write_tool", ""))
            expected_hash = str(preview_step.metadata.get("content_sha256", ""))
            expected_size = preview_step.metadata.get("current_size_bytes")
            if expected_tool and expected_tool != tool_name:
                raise WorkflowExecutionError(
                    "The destination existence changed after preview. "
                    "Create a new workflow before writing."
                )
            if (
                expected_size is not None
                and int(expected_size) != int(preview.get("current_size_bytes", 0))
            ):
                raise WorkflowExecutionError(
                    "The destination file size changed after preview. "
                    "Create a new workflow before writing."
                )
            if expected_hash and expected_hash != context.summary_sha256:
                raise WorkflowExecutionError(
                    "The pending write content no longer matches its preview."
                )

        context.write_tool = tool_name
        context.preview = preview
        return preview

    def _fail_current_step(self, workflow_id: int, error: Exception) -> WorkflowRun:
        workflow = self.workflow_store.get_workflow(workflow_id)
        if workflow.status in TERMINAL_WORKFLOW_STATUSES:
            return workflow
        if workflow.current_step is None:
            return workflow
        step = workflow.steps[workflow.current_step - 1]
        if step.status is StepStatus.PENDING:
            workflow = self.workflow_store.begin_step(workflow_id, step.step_number)
        return self.workflow_store.fail_step(
            workflow_id,
            int(workflow.current_step or step.step_number),
            error=str(error),
        )

    def execute(self, workflow_id: int) -> WorkflowRun:
        workflow_id = int(workflow_id)
        context = WorkflowExecutionContext()
        workflow = self.workflow_store.get_workflow(workflow_id)

        if workflow.status in TERMINAL_WORKFLOW_STATUSES:
            self.output(
                f"Elise: Workflow #{workflow_id} is already {workflow.status.value}."
            )
            return workflow
        if workflow.workflow_type != "read_summarize_write":
            error = WorkflowExecutionError(
                f"Unsupported workflow type: {workflow.workflow_type}"
            )
            failed = self._fail_current_step(workflow_id, error)
            self.output(f"Elise: Workflow #{workflow_id} failed: {error}")
            return failed

        try:
            if workflow.status is WorkflowStatus.PENDING:
                workflow = self.workflow_store.start_workflow(workflow_id)
                self.output(f"Elise: Workflow #{workflow_id} started.")

            while True:
                workflow = self.workflow_store.get_workflow(workflow_id)
                if workflow.status in TERMINAL_WORKFLOW_STATUSES:
                    return workflow

                if workflow.status is WorkflowStatus.WAITING_FOR_CONFIRMATION:
                    self._read_source(
                        workflow,
                        context,
                        verify_persisted_hash=True,
                    )
                    self._generate_summary(
                        workflow,
                        context,
                        verify_persisted_hash=True,
                    )
                    preview = self._build_preview(
                        workflow,
                        context,
                        verify_persisted_preview=True,
                    )
                else:
                    if workflow.current_step is None:
                        raise WorkflowExecutionError(
                            "Active workflow has no current step."
                        )
                    step = workflow.steps[workflow.current_step - 1]
                    if step.status is StepStatus.PENDING:
                        workflow = self.workflow_store.begin_step(
                            workflow_id,
                            step.step_number,
                        )
                        step = workflow.steps[step.step_number - 1]

                    if step.action_name == "locate_source":
                        self._locate_source(workflow, context)
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                f"Located documents/{context.source_path}."
                            ),
                            metadata={
                                "root": "documents",
                                "path": context.source_path,
                            },
                        )
                        self.output("Elise: Step 1/8 located the source file.")
                        continue

                    if step.action_name == "read_source":
                        result = self._read_source(
                            workflow,
                            context,
                            verify_persisted_hash=False,
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                f"Read {result.get('returned_chars')} characters "
                                "without truncation."
                            ),
                            metadata={
                                "source_sha256": context.source_sha256,
                                "size_bytes": result.get("size_bytes"),
                                "returned_chars": result.get("returned_chars"),
                                "truncated": False,
                            },
                        )
                        self.output("Elise: Step 2/8 read the source file.")
                        continue

                    if step.action_name == "summarize_source":
                        self._generate_summary(
                            workflow,
                            context,
                            verify_persisted_hash=False,
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                f"Generated a {len(context.summary_text)}-character "
                                "source-grounded summary."
                            ),
                            metadata={
                                "source_sha256": context.source_sha256,
                                "summary_sha256": context.summary_sha256,
                                "summary_chars": len(context.summary_text),
                            },
                        )
                        self.output("Elise: Step 3/8 generated the summary.")
                        continue

                    if step.action_name == "preview_destination":
                        preview = self._build_preview(
                            workflow,
                            context,
                            verify_persisted_preview=False,
                        )
                        self.workflow_store.complete_step(
                            workflow_id,
                            step.step_number,
                            result_summary=(
                                f"Prepared {context.write_tool} preview for "
                                f"documents/{context.destination_path}."
                            ),
                            metadata={
                                "write_tool": context.write_tool,
                                "destination_path": context.destination_path,
                                "content_sha256": context.summary_sha256,
                                "content_chars": len(context.summary_text),
                                "target_existed": bool(preview.get("target_exists")),
                                "current_size_bytes": int(
                                    preview.get("current_size_bytes", 0)
                                ),
                            },
                        )
                        self.output("Elise: Step 4/8 prepared the write preview.")
                        continue

                    if step.action_name == "confirm_write":
                        preview = self._build_preview(
                            workflow,
                            context,
                            verify_persisted_preview=True,
                        )
                        workflow = self.workflow_store.wait_for_confirmation(
                            workflow_id,
                            step.step_number,
                            summary="Waiting for explicit workflow write confirmation.",
                        )

                policy = self.tool_manager.get_tool_policy(context.write_tool)
                self.render_write_preview(preview, policy)
                self.output(
                    f"Elise: Workflow #{workflow_id} is waiting for write confirmation."
                )
                try:
                    approved = self.request_confirmation()
                except (KeyboardInterrupt, EOFError):
                    self.output(
                        "Elise: Confirmation was interrupted. The workflow remains "
                        "paused and can be resumed later."
                    )
                    return self.workflow_store.get_workflow(workflow_id)

                arguments = {
                    "root": "documents",
                    "path": context.destination_path,
                    "content": context.summary_text,
                }
                if not approved:
                    self._audit_denied_write(
                        workflow=workflow,
                        tool_name=context.write_tool,
                        arguments=arguments,
                    )
                    cancelled = self.workflow_store.cancel_workflow(
                        workflow_id,
                        reason="Workflow write was denied by the user.",
                    )
                    self.output(
                        f"Elise: Workflow #{workflow_id} cancelled; no file was written."
                    )
                    return cancelled

                self.workflow_store.resume_workflow(workflow_id)
                self.workflow_store.complete_step(
                    workflow_id,
                    5,
                    result_summary="User approved the exact displayed write preview.",
                    metadata={
                        "approved": True,
                        "write_tool": context.write_tool,
                        "content_sha256": context.summary_sha256,
                    },
                )
                self.workflow_store.begin_step(workflow_id, 6)
                workflow = self.workflow_store.get_workflow(workflow_id)
                result = self._execute_tool(
                    workflow=workflow,
                    tool_name=context.write_tool,
                    arguments=arguments,
                    confirmed=True,
                )
                if not result.get("success"):
                    raise WorkflowExecutionError(
                        "Confirmed write failed: " + str(result.get("error"))
                    )
                self.workflow_store.complete_step(
                    workflow_id,
                    6,
                    result_summary=(
                        f"Wrote {result.get('bytes_written')} bytes to "
                        f"documents/{context.destination_path}."
                    ),
                    metadata={
                        "write_tool": context.write_tool,
                        "destination_path": context.destination_path,
                        "content_sha256": result.get("content_sha256"),
                        "bytes_written": result.get("bytes_written"),
                        "chars_written": result.get("chars_written"),
                    },
                )
                self.output("Elise: Step 6/8 wrote the confirmed summary.")

                self.workflow_store.begin_step(workflow_id, 7)
                try:
                    file_count, chunk_count = self.document_store.reindex()
                except Exception as error:
                    raise WorkflowExecutionError(
                        f"The file was written, but document reindexing failed: {error}"
                    ) from error
                self.workflow_store.complete_step(
                    workflow_id,
                    7,
                    result_summary=(
                        f"Reindexed {file_count} files and {chunk_count} sections."
                    ),
                    metadata={
                        "file_count": int(file_count),
                        "chunk_count": int(chunk_count),
                    },
                )
                self.output("Elise: Step 7/8 refreshed document search.")

                self.workflow_store.begin_step(workflow_id, 8)
                completed = self.workflow_store.complete_step(
                    workflow_id,
                    8,
                    result_summary=(
                        f"Completed workflow and saved documents/"
                        f"{context.destination_path}."
                    ),
                    metadata={
                        "destination_path": context.destination_path,
                        "content_sha256": context.summary_sha256,
                    },
                )
                self.output(
                    f"Elise: Workflow #{workflow_id} completed successfully."
                )
                return completed

        except WorkflowExecutionError as error:
            failed = self._fail_current_step(workflow_id, error)
            self.output(f"Elise: Workflow #{workflow_id} failed: {error}")
            return failed
        except Exception as error:
            wrapped = WorkflowExecutionError(
                f"Unexpected workflow execution error: {error}"
            )
            failed = self._fail_current_step(workflow_id, wrapped)
            self.output(f"Elise: Workflow #{workflow_id} failed: {wrapped}")
            return failed
