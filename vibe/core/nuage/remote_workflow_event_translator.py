from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import json
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from vibe.core.nuage.agent_models import AgentCompletionState
from vibe.core.nuage.events import (
    CustomTaskCanceled,
    CustomTaskCompleted,
    CustomTaskFailed,
    CustomTaskInProgress,
    CustomTaskStarted,
    CustomTaskTimedOut,
    JSONPatchAppend,
    JSONPatchPayload,
    JSONPatchRemove,
    JSONPatchReplace,
    JSONPayload,
    WorkflowEvent,
    WorkflowExecutionCanceled,
    WorkflowExecutionCompleted,
    WorkflowExecutionFailed,
)
from vibe.core.nuage.remote_workflow_event_models import (
    AgentToolCallState,
    AnyToolUIState,
    AskUserQuestionArgs,
    AssistantMessageState,
    BaseUIState,
    CommandUIState,
    FileUIState,
    GenericToolUIState,
    PendingInputRequest,
    PredefinedAnswersState,
    RemoteToolArgs,
    RemoteToolResult,
    WaitForInputPayload,
    WorkingState,
    parse_tool_ui_state,
)
from vibe.core.nuage.workflow import WorkflowExecutionStatus
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolError
from vibe.core.tools.ui import ToolUIData
from vibe.core.types import (
    AgentStats,
    AssistantEvent,
    BaseEvent,
    FunctionCall,
    LLMMessage,
    ReasoningEvent,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
    WaitingForInputEvent,
)

_WAIT_FOR_INPUT_TASK_TYPE = "wait_for_input"
# These names must match the remote workflow's tool naming convention
_ASK_USER_QUESTION_TOOL = "ask_user_question"
_SEND_USER_MESSAGE_TOOL = "send_user_message"


class _RemoteTool(
    BaseTool[RemoteToolArgs, RemoteToolResult, BaseToolConfig, BaseToolState],
    ToolUIData[RemoteToolArgs, RemoteToolResult],
):
    remote_name = "remote_tool"

    @classmethod
    def get_name(cls) -> str:
        return cls.remote_name

    @classmethod
    def get_status_text(cls) -> str:
        return f"Running {cls.remote_name}"

    @classmethod
    def format_call_display(cls, args: RemoteToolArgs) -> Any:
        from vibe.core.tools.ui import ToolCallDisplay

        return ToolCallDisplay(summary=args.summary or cls.remote_name)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> Any:
        from vibe.core.tools.ui import ToolResultDisplay

        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        if isinstance(event.result, RemoteToolResult):
            return ToolResultDisplay(
                success=True, message=event.result.message or cls.remote_name
            )
        return ToolResultDisplay(success=True, message=cls.remote_name)

    async def run(
        self, args: RemoteToolArgs, ctx: Any = None
    ) -> AsyncGenerator[ToolStreamEvent | RemoteToolResult, None]:
        raise ToolError("Remote workflow tools cannot be invoked locally")
        yield  # type: ignore[misc]


_REMOTE_TOOL_CACHE: dict[str, type[_RemoteTool]] = {}


def _remote_tool_class(tool_name: str) -> type[_RemoteTool]:
    cached = _REMOTE_TOOL_CACHE.get(tool_name)
    if cached is not None:
        return cached

    class_name = "".join(
        char if char.isalnum() or char == "_" else "_"
        for char in f"RemoteTool_{tool_name.replace('-', '_')}"
    )
    tool_class = type(
        class_name, (_RemoteTool,), {"remote_name": tool_name, "__module__": __name__}
    )
    _REMOTE_TOOL_CACHE[tool_name] = tool_class
    return tool_class


class RemoteWorkflowEventTranslator:
    def __init__(
        self,
        *,
        available_tools: dict[str, type[BaseTool]],
        stats: AgentStats,
        merge_message: Callable[[LLMMessage], None],
    ) -> None:
        self._available_tools = available_tools
        self._stats = stats
        self._merge_message = merge_message
        self._task_state: dict[str, dict[str, Any]] = {}
        self._completion_message_ids: dict[str, str] = {}
        self._seen_tool_call_ids: set[str] = set()
        self._seen_tool_results: set[str] = set()
        self._open_tool_calls: dict[str, str] = {}
        self._input_snapshots: dict[str, str] = {}
        self._tool_stream_snapshots: dict[str, str] = {}
        self._pending_tool_progress: dict[str, tuple[str, str]] = {}
        self._pending_input_request: PendingInputRequest | None = None
        self._pending_question_prompt: str | None = None
        self._pending_ask_user_question_call_id: str | None = None
        self._last_status: WorkflowExecutionStatus | None = None

    @property
    def pending_input_request(self) -> PendingInputRequest | None:
        return self._pending_input_request

    @pending_input_request.setter
    def pending_input_request(self, value: PendingInputRequest | None) -> None:
        self._pending_input_request = value

    @property
    def last_status(self) -> WorkflowExecutionStatus | None:
        return self._last_status

    @property
    def task_state(self) -> dict[str, dict[str, Any]]:
        return self._task_state

    def consume_workflow_event(self, event: WorkflowEvent) -> list[BaseEvent]:
        if self._consume_workflow_lifecycle_event(event):
            return []

        wait_for_input_events = self._consume_wait_for_input_event(event)
        if wait_for_input_events is not None:
            return wait_for_input_events

        if not isinstance(
            event,
            (
                CustomTaskStarted,
                CustomTaskInProgress,
                CustomTaskCompleted,
                CustomTaskFailed,
                CustomTaskTimedOut,
                CustomTaskCanceled,
            ),
        ):
            return []

        return self._consume_agent_task_event(event)

    def is_idle_boundary(self, event: WorkflowEvent) -> bool:
        if isinstance(
            event,
            (
                WorkflowExecutionCompleted,
                WorkflowExecutionFailed,
                WorkflowExecutionCanceled,
            ),
        ):
            return True

        if isinstance(event, CustomTaskStarted):
            return event.attributes.custom_task_type == _WAIT_FOR_INPUT_TASK_TYPE

        if not isinstance(event, (CustomTaskInProgress, CustomTaskCompleted)):
            return False

        if event.attributes.custom_task_type != "AgentInputState":
            return False

        if self._open_tool_calls:
            return False

        state = self._task_state.get(event.attributes.custom_task_id, {})
        return state.get("input") is None

    def flush_open_tool_calls(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        for tool_call_id, tool_name in list(self._open_tool_calls.items()):
            tool_class = self._resolve_tool_class(tool_name)
            events.append(
                ToolResultEvent(
                    tool_name=tool_name,
                    tool_class=tool_class,
                    tool_call_id=tool_call_id,
                )
            )
        self._open_tool_calls.clear()
        return events

    def _consume_workflow_lifecycle_event(self, event: WorkflowEvent) -> bool:
        if isinstance(event, WorkflowExecutionCompleted):
            self._last_status = WorkflowExecutionStatus.COMPLETED
            self._pending_input_request = None
            return True

        if isinstance(event, WorkflowExecutionCanceled):
            self._last_status = WorkflowExecutionStatus.CANCELED
            self._pending_input_request = None
            return True

        if isinstance(event, WorkflowExecutionFailed):
            self._last_status = WorkflowExecutionStatus.FAILED
            self._pending_input_request = None
            return True

        return False

    def _consume_wait_for_input_event(
        self, event: WorkflowEvent
    ) -> list[BaseEvent] | None:
        if isinstance(event, CustomTaskStarted):
            return self._wait_for_input_started_events(event)

        if not isinstance(
            event,
            (
                CustomTaskCompleted,
                CustomTaskCanceled,
                CustomTaskFailed,
                CustomTaskTimedOut,
            ),
        ):
            return None
        return self._wait_for_input_terminal_events(event)

    def _consume_agent_task_event(
        self,
        event: (
            CustomTaskStarted
            | CustomTaskInProgress
            | CustomTaskCompleted
            | CustomTaskFailed
            | CustomTaskTimedOut
            | CustomTaskCanceled
        ),
    ) -> list[BaseEvent]:
        task_type = event.attributes.custom_task_type
        if task_type not in {
            "AgentCompletionState",
            "AgentToolCallState",
            "AgentStepState",
            "AgentInputState",
            "assistant_message",
            "working",
        }:
            return []

        if isinstance(
            event, (CustomTaskFailed, CustomTaskTimedOut, CustomTaskCanceled)
        ):
            return self._agent_task_terminal_events(event)

        previous_state, state = self._get_current_state(event)
        task_id = event.attributes.custom_task_id
        events: list[BaseEvent] = []

        match task_type:
            case "AgentCompletionState":
                events = self._completion_events(task_id, previous_state, state)
            case "assistant_message":
                events = self._assistant_message_events(task_id, previous_state, state)
            case "working":
                events = self._working_events(task_id, previous_state, state, event)
            case "AgentToolCallState":
                events = self._tool_events(task_id, state, event)
            case "AgentInputState":
                self._input_events(task_id, state)
        return events

    def _wait_for_input_started_events(
        self, event: CustomTaskStarted
    ) -> list[BaseEvent] | None:
        if event.attributes.custom_task_type != _WAIT_FOR_INPUT_TASK_TYPE:
            return None

        payload_value = event.attributes.payload.value
        label: str | None = None
        if isinstance(payload_value, dict):
            self._pending_input_request = PendingInputRequest.model_validate({
                "task_id": event.attributes.custom_task_id,
                **payload_value,
            })
            label = self._pending_input_request.label

        events: list[BaseEvent] = []
        if label:
            events.extend(self._assistant_question_events(label))

        events.append(
            WaitingForInputEvent(
                task_id=event.attributes.custom_task_id,
                label=label,
                predefined_answers=self._extract_predefined_answers(payload_value),
            )
        )
        return events

    def _wait_for_input_terminal_events(
        self,
        event: CustomTaskCompleted
        | CustomTaskCanceled
        | CustomTaskFailed
        | CustomTaskTimedOut,
    ) -> list[BaseEvent] | None:
        if event.attributes.custom_task_type != _WAIT_FOR_INPUT_TASK_TYPE:
            return None

        self._pending_input_request = None
        self._pending_question_prompt = None
        ask_user_question_call_id = self._pending_ask_user_question_call_id
        self._pending_ask_user_question_call_id = None

        if not isinstance(event, CustomTaskCompleted):
            if ask_user_question_call_id:
                return self._emit_tool_result_events(
                    tool_name=_ASK_USER_QUESTION_TOOL,
                    tool_call_id=ask_user_question_call_id,
                    output=None,
                    error="Cancelled",
                )
            return []
        events = self._completed_wait_for_input_events(
            event.attributes.payload.value, ask_user_question_call_id
        )
        return events

    def _completed_wait_for_input_events(
        self, payload_value: Any, ask_user_question_call_id: str | None = None
    ) -> list[BaseEvent]:
        if not isinstance(payload_value, dict):
            return []
        payload = WaitForInputPayload.model_validate(payload_value)
        if payload.input is None:
            return []

        textual_input = self._extract_user_text(payload.input.message)
        if not textual_input:
            return []

        events: list[BaseEvent] = []
        if ask_user_question_call_id:
            events.extend(
                self._emit_tool_result_events(
                    tool_name=_ASK_USER_QUESTION_TOOL,
                    tool_call_id=ask_user_question_call_id,
                    output={"answer": textual_input},
                    error=None,
                )
            )

        user_message = LLMMessage(role=Role.user, content=textual_input)
        self._merge_message(user_message)
        if user_message.message_id is None:
            return events

        events.append(
            UserMessageEvent(content=textual_input, message_id=user_message.message_id)
        )
        return events

    def _get_current_state(
        self, event: CustomTaskStarted | CustomTaskInProgress | CustomTaskCompleted
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        task_id = event.attributes.custom_task_id
        previous_state = self._task_state.get(task_id, {})
        if isinstance(event.attributes.payload, JSONPayload):
            new_state = self._normalize_state(event.attributes.payload.value)
        else:
            new_state = self._apply_json_patch(
                previous_state, cast(JSONPatchPayload, event.attributes.payload)
            )
        self._task_state[task_id] = new_state
        return previous_state, new_state

    def _agent_task_terminal_events(
        self, event: CustomTaskFailed | CustomTaskTimedOut | CustomTaskCanceled
    ) -> list[BaseEvent]:
        if event.attributes.custom_task_type != "AgentToolCallState":
            return []

        task_id = event.attributes.custom_task_id
        state = self._task_state.get(task_id, {})
        return self._tool_terminal_events(task_id, state, event)

    def _normalize_state(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _apply_json_patch(
        self, previous_state: dict[str, Any], payload: JSONPatchPayload
    ) -> dict[str, Any]:
        new_state = cast(dict[str, Any], self._json_safe_value(previous_state))
        for patch in payload.value:
            path = [part for part in patch.path.split("/") if part]
            if not path:
                if isinstance(patch, JSONPatchReplace):
                    new_state = self._normalize_state(patch.value)
                continue

            if isinstance(patch, JSONPatchRemove):
                self._remove_path(new_state, path)
                continue

            if isinstance(patch, JSONPatchAppend):
                current = self._get_path(new_state, path)
                self._set_path(new_state, path, f"{current or ''}{patch.value}")
                continue

            self._set_path(new_state, path, patch.value)

        return new_state

    def _get_path(self, state: dict[str, Any], path: list[str]) -> Any:
        current: Any = state
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _set_path(self, state: dict[str, Any], path: list[str], value: Any) -> None:
        current: dict[str, Any] = state
        for part in path[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[path[-1]] = value

    def _remove_path(self, state: dict[str, Any], path: list[str]) -> None:
        current: dict[str, Any] = state
        for part in path[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                return
            current = child
        current.pop(path[-1], None)

    def _completion_events(
        self, task_id: str, previous_state: dict[str, Any], state: dict[str, Any]
    ) -> list[BaseEvent]:
        completion_state = AgentCompletionState.model_validate(state)
        previous_completion_state = AgentCompletionState.model_validate(previous_state)
        current_content = completion_state.content
        current_reasoning = completion_state.reasoning_content
        previous_content = previous_completion_state.content
        previous_reasoning = previous_completion_state.reasoning_content

        if not (
            (not current_content or current_content.startswith(previous_content))
            and (
                not current_reasoning
                or current_reasoning.startswith(previous_reasoning)
            )
        ):
            previous_content = ""
            previous_reasoning = ""
            self._completion_message_ids.pop(task_id, None)

        content_delta = current_content[len(previous_content) :]
        reasoning_delta = current_reasoning[len(previous_reasoning) :]
        if not content_delta and not reasoning_delta:
            return []

        message_id = self._completion_message_ids.setdefault(
            task_id, LLMMessage(role=Role.assistant).message_id or task_id
        )

        self._merge_message(
            LLMMessage(
                role=Role.assistant,
                content=content_delta or None,
                reasoning_content=reasoning_delta or None,
                message_id=message_id,
            )
        )

        events: list[BaseEvent] = []
        if reasoning_delta:
            events.append(
                ReasoningEvent(content=reasoning_delta, message_id=message_id)
            )
        if content_delta:
            events.append(AssistantEvent(content=content_delta, message_id=message_id))
        return events

    def _assistant_message_events(
        self, task_id: str, previous_state: dict[str, Any], state: dict[str, Any]
    ) -> list[BaseEvent]:
        current_text = self._extract_content_chunks_text(state)
        previous_text = self._extract_content_chunks_text(previous_state)

        if not (not current_text or current_text.startswith(previous_text)):
            previous_text = ""
            self._completion_message_ids.pop(task_id, None)

        delta = current_text[len(previous_text) :]
        if not delta:
            return []

        message_id = self._completion_message_ids.setdefault(
            task_id, LLMMessage(role=Role.assistant).message_id or task_id
        )
        self._merge_message(
            LLMMessage(role=Role.assistant, content=delta, message_id=message_id)
        )
        return [AssistantEvent(content=delta, message_id=message_id)]

    def _extract_content_chunks_text(self, state: dict[str, Any]) -> str:
        msg = AssistantMessageState.model_validate(state)
        return "".join(
            chunk.text for chunk in msg.contentChunks if chunk.type == "text"
        )

    def _working_events(
        self,
        task_id: str,
        previous_state: dict[str, Any],
        state: dict[str, Any],
        event: CustomTaskStarted | CustomTaskInProgress | CustomTaskCompleted,
    ) -> list[BaseEvent]:
        working = WorkingState.model_validate(state)
        previous_working = WorkingState.model_validate(previous_state)
        parsed_ui_state = (
            parse_tool_ui_state(working.toolUIState) if working.toolUIState else None
        )
        base_ui_state = (
            BaseUIState.model_validate(working.toolUIState)
            if working.toolUIState
            else None
        )
        tool_call_id = base_ui_state.toolCallId if base_ui_state else None

        if not tool_call_id:
            return self._working_events_without_tool_call(
                task_id, working, previous_working, parsed_ui_state, event
            )

        return self._working_events_with_tool_call(
            task_id, working, parsed_ui_state, tool_call_id, event
        )

    def _working_events_without_tool_call(
        self,
        task_id: str,
        working: WorkingState,
        previous_working: WorkingState,
        parsed_ui_state: AnyToolUIState | None,
        event: CustomTaskStarted | CustomTaskInProgress | CustomTaskCompleted,
    ) -> list[BaseEvent]:
        if isinstance(event, CustomTaskStarted):
            return []

        if working.type == "thinking":
            return self._working_thinking_events(task_id, working, previous_working)

        tool_name = working.title.removeprefix("Executing ")
        if not tool_name or tool_name == _SEND_USER_MESSAGE_TOOL:
            return []

        events = self._emit_tool_call_events(
            tool_name=tool_name,
            tool_call_id=task_id,
            tool_args={"summary": working.title},
            task_key=task_id,
        )
        stream_output = self._working_stream_output(
            parsed_ui_state=parsed_ui_state, content=working.content
        )
        if stream_output:
            events.extend(
                self._tool_stream_events(
                    tool_name=tool_name,
                    tool_call_id=task_id,
                    result_key=task_id,
                    output=stream_output,
                )
            )
        if isinstance(event, CustomTaskCompleted):
            output, error = self._tool_result_from_ui_state(parsed_ui_state)
            events.extend(
                self._emit_tool_result_events(
                    tool_name=tool_name,
                    tool_call_id=task_id,
                    output=output or {"message": working.title},
                    error=error,
                )
            )
        return events

    def _working_thinking_events(
        self, task_id: str, working: WorkingState, previous_working: WorkingState
    ) -> list[BaseEvent]:
        delta = working.content[len(previous_working.content) :]
        if not delta:
            return []
        message_id = self._completion_message_ids.setdefault(
            task_id, LLMMessage(role=Role.assistant).message_id or task_id
        )
        self._merge_message(
            LLMMessage(
                role=Role.assistant, reasoning_content=delta, message_id=message_id
            )
        )
        return [ReasoningEvent(content=delta, message_id=message_id)]

    def _working_events_with_tool_call(
        self,
        task_id: str,
        working: WorkingState,
        parsed_ui_state: AnyToolUIState | None,
        tool_call_id: str,
        event: CustomTaskStarted | CustomTaskInProgress | CustomTaskCompleted,
    ) -> list[BaseEvent]:
        tool_name = working.title.removeprefix("Executing ")
        if not tool_name or tool_name == _SEND_USER_MESSAGE_TOOL:
            return []

        tool_args = self._tool_args_from_ui_state(parsed_ui_state)
        events = self._emit_tool_call_events(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            task_key=task_id,
        )

        if not isinstance(parsed_ui_state, FileUIState) and working.content:
            events.extend(
                self._tool_stream_events(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    result_key=tool_call_id,
                    output=working.content,
                )
            )

        if isinstance(event, CustomTaskCompleted):
            output, error = self._tool_result_from_ui_state(parsed_ui_state)
            events.extend(
                self._emit_tool_result_events(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    output=output or {"message": working.title},
                    error=error,
                )
            )

        return events

    def _working_stream_output(
        self, *, parsed_ui_state: AnyToolUIState | None, content: str
    ) -> Any:
        if content:
            return content

        output, error = self._tool_result_from_ui_state(parsed_ui_state)
        if error:
            return {"error": error}
        if output is not None:
            return output
        return None

    def _tool_events(
        self,
        task_id: str,
        state: dict[str, Any],
        event: CustomTaskStarted | CustomTaskInProgress | CustomTaskCompleted,
    ) -> list[BaseEvent]:
        parsed = AgentToolCallState.model_validate(state)
        tool_name = parsed.name
        if tool_name == _SEND_USER_MESSAGE_TOOL:
            return []

        events = self._tool_call_and_stream_events(task_id, state)

        if not isinstance(event, CustomTaskCompleted) or not tool_name:
            return events

        tool_call_id = parsed.tool_call_id or task_id
        events.extend(
            self._emit_tool_result_events(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                output=parsed.output,
                error=None,
            )
        )
        return events

    def _tool_args_from_ui_state(
        self, ui_state: AnyToolUIState | None
    ) -> dict[str, Any]:
        if isinstance(ui_state, FileUIState):
            if not ui_state.operations:
                return {}
            op = ui_state.operations[0]
            return {
                "path": op.uri,
                "content": op.content,
                "overwrite": op.type == "replace",
            }
        if isinstance(ui_state, CommandUIState):
            return {"command": ui_state.command}
        if isinstance(ui_state, GenericToolUIState):
            return ui_state.arguments
        return {}

    def _tool_result_from_ui_state(
        self, ui_state: AnyToolUIState | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if isinstance(ui_state, FileUIState):
            return self._file_ui_result(ui_state)
        if isinstance(ui_state, CommandUIState):
            return self._command_ui_result(ui_state)
        if isinstance(ui_state, GenericToolUIState):
            return self._generic_ui_result(ui_state)
        return None, None

    def _file_ui_result(
        self, ui_state: FileUIState
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not ui_state.operations:
            return None, "No file operations in result"
        op = ui_state.operations[0]
        return {
            "path": op.uri,
            "bytes_written": len(op.content.encode()),
            "file_existed": op.type == "replace",
            "content": op.content,
        }, None

    def _command_ui_result(
        self, ui_state: CommandUIState
    ) -> tuple[dict[str, Any] | None, str | None]:
        result = ui_state.result
        if result is None or result.status == "running":
            return None, None
        if result.status == "failed":
            return None, result.output or "Command failed"
        return {
            "command": ui_state.command,
            "stdout": result.output,
            "stderr": "",
            "returncode": 0,
        }, None

    def _generic_ui_result(
        self, ui_state: GenericToolUIState
    ) -> tuple[dict[str, Any] | None, str | None]:
        result = ui_state.result
        if result is None or result.status == "running":
            return None, None
        if result.status == "failed":
            return None, result.error or "Tool failed"
        return ui_state.arguments, None

    def _emit_tool_call_events(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_args: dict[str, Any],
        task_key: str,
    ) -> list[BaseEvent]:
        if not tool_name or tool_name == _SEND_USER_MESSAGE_TOOL:
            return []

        question_events = self._ask_user_question_events(tool_name, tool_args)
        tool_class = self._resolve_tool_class(tool_name)
        args_model, _ = tool_class._get_tool_args_results()
        validated_args: BaseModel | None = None
        try:
            validated_args = args_model.model_validate(tool_args)
        except ValidationError:
            validated_args = None

        if tool_call_id in self._seen_tool_call_ids:
            return []

        self._seen_tool_call_ids.add(tool_call_id)
        self._stats.tool_calls_agreed += 1
        self._open_tool_calls[tool_call_id] = tool_name
        if tool_name == _ASK_USER_QUESTION_TOOL:
            self._pending_ask_user_question_call_id = tool_call_id
        self._merge_message(
            LLMMessage(
                role=Role.assistant,
                tool_calls=[
                    ToolCall(
                        id=tool_call_id,
                        function=FunctionCall(
                            name=tool_name, arguments=self._json_string(tool_args)
                        ),
                    )
                ],
            )
        )
        return [
            ToolCallEvent(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_class=tool_class,
                args=validated_args,
            ),
            *question_events,
        ]

    def _tool_stream_events(
        self, tool_name: str, tool_call_id: str, result_key: str, output: Any
    ) -> list[ToolStreamEvent]:
        preview = self._output_preview_text(output)
        if not preview:
            return []

        previous_preview = self._tool_stream_snapshots.get(result_key, "")
        if preview == previous_preview:
            return []

        self._tool_stream_snapshots[result_key] = preview
        if not preview.strip():
            return []

        return [
            ToolStreamEvent(
                tool_name=tool_name, tool_call_id=tool_call_id, message=preview
            )
        ]

    def _resolve_tool_class(self, tool_name: str) -> type[BaseTool]:
        if tool_class := self._available_tools.get(tool_name):
            return tool_class

        short_name = tool_name.rsplit(".", 1)[-1]
        if short_name != tool_name and (
            tool_class := self._available_tools.get(short_name)
        ):
            return tool_class

        suffix_matches = [
            available_tool_class
            for available_name, available_tool_class in self._available_tools.items()
            if available_name.endswith(f".{short_name}") or available_name == short_name
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        return _remote_tool_class(tool_name)

    def _finalize_tool_call(self, tool_call_id: str) -> None:
        self._seen_tool_results.add(tool_call_id)
        self._open_tool_calls.pop(tool_call_id, None)
        self._pending_tool_progress.pop(tool_call_id, None)
        self._tool_stream_snapshots.pop(tool_call_id, None)

    def _emit_tool_result_events(
        self, *, tool_name: str, tool_call_id: str, output: Any, error: str | None
    ) -> list[BaseEvent]:
        if tool_name == _SEND_USER_MESSAGE_TOOL:
            return []

        if tool_call_id in self._seen_tool_results:
            return []

        tool_class = self._resolve_tool_class(tool_name)

        if error:
            self._finalize_tool_call(tool_call_id)
            return self._emit_tool_error_result(
                tool_name=tool_name,
                tool_class=tool_class,
                tool_call_id=tool_call_id,
                error=error,
            )

        if output is None:
            self._finalize_tool_call(tool_call_id)
            return self._emit_missing_tool_output(
                tool_name=tool_name, tool_class=tool_class, tool_call_id=tool_call_id
            )

        output_dict = self._normalize_output(output)
        output_error = output_dict.get("error")
        if isinstance(output_error, str) and output_error:
            self._finalize_tool_call(tool_call_id)
            return self._emit_tool_error_result(
                tool_name=tool_name,
                tool_class=tool_class,
                tool_call_id=tool_call_id,
                error=output_error,
            )

        self._finalize_tool_call(tool_call_id)

        _, result_model = tool_class._get_tool_args_results()
        result_value: BaseModel | None = None
        try:
            result_value = result_model.model_validate(output_dict)
        except ValidationError:
            result_value = None

        self._stats.tool_calls_succeeded += 1
        result_text = "\n".join(f"{k}: {v}" for k, v in output_dict.items())
        self._merge_message(
            LLMMessage(
                role=Role.tool,
                name=tool_name,
                tool_call_id=tool_call_id,
                content=result_text,
            )
        )
        return [
            ToolResultEvent(
                tool_name=tool_name,
                tool_class=tool_class,
                result=result_value,
                tool_call_id=tool_call_id,
            )
        ]

    def _emit_missing_tool_output(
        self, tool_name: str, tool_class: type[BaseTool], tool_call_id: str
    ) -> list[BaseEvent]:
        error = "Tool did not produce output"
        self._stats.tool_calls_failed += 1
        self._merge_message(
            LLMMessage(
                role=Role.tool, name=tool_name, tool_call_id=tool_call_id, content=error
            )
        )
        return [
            ToolResultEvent(
                tool_name=tool_name,
                tool_class=tool_class,
                error=error,
                tool_call_id=tool_call_id,
            )
        ]

    def _emit_tool_error_result(
        self, tool_name: str, tool_class: type[BaseTool], tool_call_id: str, error: str
    ) -> list[BaseEvent]:
        self._stats.tool_calls_failed += 1
        self._merge_message(
            LLMMessage(
                role=Role.tool, name=tool_name, tool_call_id=tool_call_id, content=error
            )
        )
        return [
            ToolResultEvent(
                tool_name=tool_name,
                tool_class=tool_class,
                error=error,
                tool_call_id=tool_call_id,
            )
        ]

    def _tool_call_and_stream_events(
        self, task_id: str, state: dict[str, Any]
    ) -> list[BaseEvent]:
        parsed = AgentToolCallState.model_validate(state)
        tool_name = parsed.name
        tool_call_id = parsed.tool_call_id or task_id
        tool_args = self._normalize_mapping(parsed.kwargs)
        events = self._emit_tool_call_events(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            task_key=task_id,
        )

        if pending_progress := self._pending_tool_progress.pop(tool_call_id, None):
            pending_tool_name, pending_content = pending_progress
            if pending_tool_name == tool_name:
                events.extend(
                    self._tool_stream_events(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        result_key=tool_call_id,
                        output=pending_content,
                    )
                )

        if tool_call_id in self._seen_tool_results:
            return events

        events.extend(
            self._tool_stream_events(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                result_key=tool_call_id,
                output=parsed.output,
            )
        )
        return events

    def _normalize_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return cast(dict[str, Any], self._json_safe_value(value))
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return cast(dict[str, Any], self._json_safe_value(parsed))
        return {}

    def _normalize_output(self, output: Any) -> dict[str, Any]:
        if isinstance(output, dict):
            return cast(dict[str, Any], self._json_safe_value(output))
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                return {"value": output}
            if isinstance(parsed, dict):
                return cast(dict[str, Any], self._json_safe_value(parsed))
            return {"value": parsed}
        return {"value": self._json_safe_value(output)}

    def _output_preview_text(self, output: Any) -> str | None:
        output_dict = self._normalize_output(output)

        # Priority: known preview keys > raw string > single-value dict > all scalar fields
        for key in ("preview", "message", "status_text", "status", "delta"):
            value = output_dict.get(key)
            if isinstance(value, str) and value:
                return value

        if isinstance(output, str) and output:
            return output

        if len(output_dict) == 1 and "value" in output_dict:
            value = output_dict["value"]
            return value if isinstance(value, str) and value else None

        return (
            "\n".join(
                f"{key}: {value}"
                for key, value in output_dict.items()
                if value is not None and not isinstance(value, (dict, list))
            )
            or None
        )

    def _tool_terminal_events(
        self,
        task_id: str,
        state: dict[str, Any],
        event: CustomTaskFailed | CustomTaskTimedOut | CustomTaskCanceled,
    ) -> list[BaseEvent]:
        parsed = AgentToolCallState.model_validate(state)
        tool_name = parsed.name
        if not tool_name:
            return []
        if tool_name == _SEND_USER_MESSAGE_TOOL:
            return []
        if tool_name == _ASK_USER_QUESTION_TOOL:
            self._pending_question_prompt = None

        tool_call_id = parsed.tool_call_id or task_id
        if tool_call_id in self._seen_tool_results:
            return []

        tool_class = self._resolve_tool_class(tool_name)
        error = self._tool_terminal_error(event)
        self._finalize_tool_call(tool_call_id)
        self._stats.tool_calls_failed += 1
        self._merge_message(
            LLMMessage(
                role=Role.tool, name=tool_name, tool_call_id=tool_call_id, content=error
            )
        )
        return [
            ToolResultEvent(
                tool_name=tool_name,
                tool_class=tool_class,
                error=error,
                cancelled=isinstance(event, CustomTaskCanceled),
                tool_call_id=tool_call_id,
            )
        ]

    def _tool_terminal_error(
        self, event: CustomTaskFailed | CustomTaskTimedOut | CustomTaskCanceled
    ) -> str:
        if isinstance(event, CustomTaskFailed):
            return event.attributes.failure.message

        if isinstance(event, CustomTaskTimedOut):
            timeout_type = event.attributes.timeout_type
            return f"Timed out ({timeout_type})" if timeout_type else "Timed out"

        if event.attributes.reason:
            return f"Canceled: {event.attributes.reason}"
        return "Canceled"

    def _input_events(self, task_id: str, state: dict[str, Any]) -> None:
        parsed = WaitForInputPayload.model_validate(state)
        if parsed.input is None:
            return

        textual_input = self._extract_user_text(parsed.input.message)
        if not textual_input:
            return

        if self._input_snapshots.get(task_id) == textual_input:
            return

        self._input_snapshots[task_id] = textual_input
        self._pending_question_prompt = None
        self._merge_message(LLMMessage(role=Role.user, content=textual_input))

    def _extract_user_text(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return None

        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])

        if not parts:
            return None
        return "".join(parts)

    def _extract_predefined_answers(self, value: Any) -> list[str] | None:
        if not isinstance(value, dict):
            return None
        parsed = PredefinedAnswersState.model_validate(value)
        if parsed.input_schema is None or parsed.input_schema.properties is None:
            return None
        message = parsed.input_schema.properties.message
        if message is None:
            return None

        answers: list[str] = []
        for example in message.examples:
            answer = self._extract_user_text(example)
            if not answer or answer.lower() == "other" or answer in answers:
                continue
            answers.append(answer)

        return answers or None

    def _ask_user_question_events(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> list[BaseEvent]:
        if tool_name != _ASK_USER_QUESTION_TOOL:
            return []

        parsed = AskUserQuestionArgs.model_validate(tool_args)
        prompt = "\n\n".join(q.question for q in parsed.questions)
        if not prompt:
            return []

        return self._assistant_question_events(prompt)

    def _assistant_question_events(self, prompt: str) -> list[BaseEvent]:
        if self._pending_question_prompt == prompt:
            return []

        self._pending_question_prompt = prompt
        message_id = LLMMessage(role=Role.assistant).message_id
        self._merge_message(
            LLMMessage(role=Role.assistant, content=prompt, message_id=message_id)
        )
        return [AssistantEvent(content=prompt, message_id=message_id)]

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return self._json_safe_value(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_value(item) for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, set):
            return [self._json_safe_value(item) for item in sorted(value, key=repr)]
        return value

    def _json_string(self, value: Any) -> str:
        return json.dumps(self._json_safe_value(value))
