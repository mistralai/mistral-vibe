from __future__ import annotations

from enum import StrEnum, auto
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission


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
    id: str = Field(
        description="Stable unique identifier for the task, reused across updates"
    )
    content: str = Field(description="Brief description of the task")
    status: TodoStatus = Field(
        default=TodoStatus.PENDING,
        description="Current status of the task: pending, in_progress, completed, cancelled",
    )
    priority: TodoPriority = Field(
        default=TodoPriority.MEDIUM,
        description="Priority level of the task: high, medium, low",
    )


class TodoArgs(BaseModel):
    action: Literal["read", "write"] = Field(
        description="Required on every call: 'read' to view the current list, or 'write' to replace it"
    )
    todos: list[TodoItem] | None = Field(
        default=None,
        description="Required when action='write': the full todo list, which replaces the previous one",
    )


class TodoResult(BaseModel):
    verb: str
    todos: list[TodoItem]
    total_count: int

    @computed_field
    @property
    def message(self) -> str:
        return f"{self.verb} {self.total_count} todos"


class TodoValidationError(ValueError):
    pass


def read_todos(todos: list[TodoItem]) -> TodoResult:
    return TodoResult(verb="Retrieved", todos=todos, total_count=len(todos))


def write_todos(todos: list[TodoItem], *, max_todos: int) -> TodoResult:
    if len(todos) > max_todos:
        raise TodoValidationError(f"Cannot store more than {max_todos} todos")

    ids = [todo.id for todo in todos]
    if len(ids) != len(set(ids)):
        raise TodoValidationError("Todo IDs must be unique")

    return TodoResult(verb="Updated", todos=todos, total_count=len(todos))


class TodoConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_todos: int = 100


class TodoState(BaseToolState):
    todos: list[TodoItem] = Field(default_factory=list)


class Todo(BaseTool[TodoArgs, TodoResult, TodoConfig, TodoState]):
    pass
