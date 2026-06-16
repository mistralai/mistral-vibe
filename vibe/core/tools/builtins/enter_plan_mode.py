from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData


class EnterPlanModeArgs(BaseModel):
    pass


class EnterPlanModeResult(BaseModel):
    message: str


class EnterPlanModeConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class EnterPlanMode(
    BaseTool[EnterPlanModeArgs, EnterPlanModeResult, EnterPlanModeConfig, BaseToolState],
    ToolUIData[EnterPlanModeArgs, EnterPlanModeResult],
):
    description: ClassVar[str] = (
        "Switch to plan mode to research and write a plan before implementing. "
        "Use this when a request is complex enough to warrant planning first. "
        "In plan mode you are restricted to read-only tools plus writing to the plan file. "
        "Call exit_plan_mode when your plan is complete to request user approval."
    )

    @classmethod
    def format_call_display(cls, args: EnterPlanModeArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Switching to plan mode")

    @classmethod
    def format_result_display(cls, result: EnterPlanModeResult) -> ToolResultDisplay:
        return ToolResultDisplay(success=True, message=result.message)

    async def run(
        self, args: EnterPlanModeArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[EnterPlanModeResult, None]:
        if ctx is None or ctx.agent_manager is None:
            raise ToolError("EnterPlanMode requires an agent manager context.")

        if ctx.agent_manager.active_profile.name == BuiltinAgentName.PLAN:
            raise ToolError("Already in plan mode.")

        if ctx.switch_agent_callback:
            await ctx.switch_agent_callback(BuiltinAgentName.PLAN)
        else:
            ctx.agent_manager.switch_profile(BuiltinAgentName.PLAN)

        yield EnterPlanModeResult(
            message="Switched to plan mode. Use read-only tools to research, write your plan to the plan file, then call exit_plan_mode when ready."
        )
