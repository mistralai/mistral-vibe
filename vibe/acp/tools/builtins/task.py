from __future__ import annotations

from acp.helpers import SessionUpdate
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
)
from vibe.acp.tools.session_update import (
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.builtins.task import Task as CoreTaskTool, TaskArgs, TaskResult
from vibe.core.types import ToolCallEvent, ToolResultEvent


class Task(
    CoreTaskTool, ToolCallSessionUpdateProtocol, ToolResultSessionUpdateProtocol
):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "task.md"

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, TaskArgs):
            return fallback_tool_call(event, "task")

        return ToolCallStart(
            session_update="tool_call",
            title=cls.get_call_display(event).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            field_meta={
                "tool_name": event.tool_name,
                "agent": event.args.agent,
                "task": event.args.task,
            },
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, TaskResult):
            return failure

        result = event.result
        assert isinstance(result, TaskResult)

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed" if result.completed else "failed",
            content=[
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(
                        type="text", text=cls.get_result_display(event).message
                    ),
                )
            ],
            kind=resolve_kind(event.tool_name),
            raw_output=result.model_dump_json(),
            field_meta={
                "tool_name": event.tool_name,
                "turn_count": result.turns_used,
                "response": result.response,
            },
        )
