from __future__ import annotations

from acp.helpers import SessionUpdate
from acp.schema import AgentPlanUpdate, PlanEntry, PlanEntryPriority, PlanEntryStatus

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.acp.tools.session_update import failed_tool_result
from vibe.core.tools.builtins.todo import (
    Todo as CoreTodoTool,
    TodoArgs,
    TodoPriority,
    TodoResult,
    TodoState,
    TodoStatus,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent

TodoArgs = TodoArgs


class AcpTodoState(TodoState, AcpToolState):
    pass


class Todo(CoreTodoTool, BaseAcpTool[AcpTodoState]):
    state: AcpTodoState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "todo.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpTodoState]:
        return AcpTodoState

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        return None

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, TodoResult):
            return failure

        result = event.result
        assert isinstance(result, TodoResult)
        todos = [todo for todo in result.todos if todo.status != TodoStatus.CANCELLED]
        matched_status: dict[TodoStatus, PlanEntryStatus] = {
            TodoStatus.PENDING: "pending",
            TodoStatus.IN_PROGRESS: "in_progress",
            TodoStatus.COMPLETED: "completed",
        }
        matched_priority: dict[TodoPriority, PlanEntryPriority] = {
            TodoPriority.LOW: "low",
            TodoPriority.MEDIUM: "medium",
            TodoPriority.HIGH: "high",
        }

        update = AgentPlanUpdate(
            session_update="plan",
            entries=[
                PlanEntry(
                    content=todo.content,
                    status=matched_status[todo.status],
                    priority=matched_priority[todo.priority],
                )
                for todo in todos
            ],
        )
        return update
