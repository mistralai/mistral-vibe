from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import AssistantEvent, BaseEvent, ToolResultEvent, ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.tools.base import InvokeContext


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(description="The task for the agent to perform")
    agent: str = Field(
        default="explore",
        description="The type of specialized subagent to use for this task",
    )


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str = Field(description="The accumulated response from the subagent")
    turns_used: int = Field(description="Number of turns the subagent used")
    completed: bool = Field(description="Whether the task completed normally")


class SubagentRunnerPort(Protocol):
    def run(
        self, args: TaskArgs, ctx: InvokeContext
    ) -> AsyncGenerator[ToolStreamEvent | TaskResult, None]: ...


@dataclass(slots=True)
class SubagentRunAccumulator:
    _response: list[str] = field(default_factory=list)
    _completed: bool = True

    def observe(self, event: BaseEvent, *, tool_call_id: str) -> ToolStreamEvent | None:
        if isinstance(event, AssistantEvent):
            if event.content:
                self._response.append(event.content)
            if event.stopped_by_middleware:
                self._completed = False
            return None
        if not isinstance(event, ToolResultEvent):
            return None
        if event.skipped:
            self._completed = False
            return None
        if event.result is None or event.tool_class is None:
            return None
        if event.presentation is not None:
            display = event.presentation.display
        else:
            display = ToolUIDataAdapter(event.tool_class).get_result_display(event)
        return ToolStreamEvent(
            tool_name="task",
            message=f"{event.tool_name}: {display.text}",
            tool_call_id=tool_call_id,
        )

    def record_error(self, message: str) -> None:
        self._completed = False
        self._response.append(f"\n[Subagent error: {message}]")

    def build_result(self, *, turns_used: int, completed: bool = True) -> TaskResult:
        return TaskResult(
            response="".join(self._response),
            turns_used=turns_used,
            completed=self._completed and completed,
        )


def prepare_subagent_prompt(task: str, ctx: InvokeContext) -> str:
    if ctx.scratchpad_dir is None:
        return task
    return (
        f"Scratchpad directory: {ctx.scratchpad_dir}\n"
        "You can read and write files here without permission prompts.\n\n"
        f"{task}"
    )
