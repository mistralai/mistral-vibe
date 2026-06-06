from __future__ import annotations

from vibe.acp.tools.builtins.todo import Todo
from vibe.core.tools.builtins.todo import TodoItem, TodoPriority, TodoResult, TodoStatus
from vibe.core.types import ToolResultEvent


class TestAcpTodoSessionUpdates:
    def test_tool_result_session_update(self) -> None:
        result = TodoResult(
            message="Updated 2 todos",
            todos=[
                TodoItem(
                    id="1",
                    content="First",
                    status=TodoStatus.IN_PROGRESS,
                    priority=TodoPriority.HIGH,
                ),
                TodoItem(id="2", content="Second", status=TodoStatus.PENDING),
            ],
            total_count=2,
        )

        event = ToolResultEvent(
            tool_name="todo",
            tool_call_id="test_call_123",
            result=result,
            tool_class=Todo,
        )

        update = Todo.tool_result_session_update(event)
        assert update is not None
        assert update.session_update == "plan"
        assert len(update.entries) == 2
        assert update.entries[0].content == "First"
        assert update.entries[0].status == "in_progress"
        assert update.entries[0].priority == "high"

    def test_tool_result_session_update_failed_result(self) -> None:
        event = ToolResultEvent(
            tool_name="todo",
            tool_call_id="test_call_123",
            error="Todo IDs must be unique",
            tool_class=Todo,
        )

        update = Todo.tool_result_session_update(event)
        assert update is not None
        assert update.status == "failed"

    def test_tool_result_session_update_invalid_result(self) -> None:
        class InvalidResult:
            pass

        event = ToolResultEvent.model_construct(
            tool_name="todo",
            tool_call_id="test_call_123",
            result=InvalidResult(),  # type: ignore[arg-type]
            tool_class=Todo,
        )

        update = Todo.tool_result_session_update(event)
        assert update is not None
        assert update.status == "failed"
