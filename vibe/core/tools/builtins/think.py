from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolResultEvent


class ThinkArgs(BaseModel):
    thought: str = Field(
        description="Your reasoning, planning, or analysis before acting."
    )


class ThinkResult(BaseModel):
    thought: str = Field(description="The thought that was processed.")


class ThinkToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Think(
    BaseTool[ThinkArgs, ThinkResult, ThinkToolConfig, BaseToolState],
    ToolUIData[ThinkArgs, ThinkResult],
):
    description: ClassVar[str] = (
        "Use this tool to think through a problem step-by-step before acting. "
        "This is useful for planning multi-step tasks, analyzing tool outputs, "
        "or reasoning about complex decisions. The tool has no side effects - "
        "it simply provides space for structured thinking."
    )

    @classmethod
    def format_call_display(cls, args: ThinkArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Thinking...")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        return ToolResultDisplay(success=True, message="Done thinking")

    @classmethod
    def get_status_text(cls) -> str:
        return "Thinking"

    async def run(
        self, args: ThinkArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ThinkResult, None]:
        if not args.thought.strip():
            raise ToolError("Empty thought provided.")
        yield ThinkResult(thought=args.thought)
