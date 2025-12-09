from __future__ import annotations

from enum import StrEnum, auto
from typing import ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolCallEvent, ToolResultEvent


class TodoStatus(StrEnum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    CANCELLED = auto()


class TodoPriority(StrEnum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class TodoItem(BaseModel):
    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM


class TodoArgs(BaseModel):
    action: str = Field(description="Either 'read' or 'write'")
    todos: list[TodoItem] | None = Field(
        default=None, description="Complete list of todos when writing."
    )


class TodoResult(BaseModel):
    message: str
    todos: list[TodoItem]
    total_count: int


class TodoConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_todos: int = 100


class TodoState(BaseToolState):
    todos: list[TodoItem] = Field(default_factory=list)


class Todo(
    BaseTool[TodoArgs, TodoResult, TodoConfig, TodoState],
    ToolUIData[TodoArgs, TodoResult],
):
    description: ClassVar[str] = (
        "Manage todos. Use action='read' to view, action='write' with complete list to update."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, TodoArgs):
            return ToolCallDisplay(summary="Invalid arguments")

        args = event.args

        match args.action:
            case "read":
                return ToolCallDisplay(
                    summary="Reading todos", details={"action": "read"}
                )
            case "write":
                count = len(args.todos) if args.todos else 0
                return ToolCallDisplay(
                    summary=f"Writing {count} todos",
                    details={"action": "write", "count": count},
                )
            case _:
                return ToolCallDisplay(
                    summary=f"Unknown action: {args.action}",
                    details={"action": args.action},
                )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, TodoResult):
            return ToolResultDisplay(success=True, message="Success")

        result = event.result

        by_status = {"in_progress": [], "pending": [], "completed": [], "cancelled": []}

        for todo in result.todos:
            by_status[todo.status].append({"content": todo.content, "id": todo.id})

        return ToolResultDisplay(
            success=True,
            message=result.message,
            details={"todos_by_status": by_status, "total_count": result.total_count},
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Managing todos"

    async def run(self, args: TodoArgs) -> TodoResult:
        match args.action:
            case "read":
                return self._read_todos()
            case "write":
                return self._write_todos(args.todos or [])
            case _:
                raise ToolError(
                    f"Invalid action '{args.action}'. Use 'read' or 'write'."
                )

    def _read_todos(self) -> TodoResult:
        return TodoResult(
            message=f"Retrieved {len(self.state.todos)} todos",
            todos=self.state.todos,
            total_count=len(self.state.todos),
        )

    def _write_todos(self, todos: list[TodoItem]) -> TodoResult:
        if len(todos) > self.config.max_todos:
            raise ToolError(f"Cannot store more than {self.config.max_todos} todos")

        ids = [todo.id for todo in todos]
        if len(ids) != len(set(ids)):
            raise ToolError("Todo IDs must be unique")

        self.state.todos = todos

        return TodoResult(
            message=f"Updated {len(todos)} todos",
            todos=self.state.todos,
            total_count=len(self.state.todos),
        )
