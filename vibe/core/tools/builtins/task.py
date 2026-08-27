from __future__ import annotations

from collections.abc import AsyncGenerator
import fnmatch

from pydantic import Field

from vibe.core.agents.models import AgentType, BuiltinAgentName
from vibe.core.subagents import TaskArgs, TaskResult
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent
from vibe.utils.tool_presentation import ToolEffectKind


class TaskToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    allowlist: list[str] = Field(default=[BuiltinAgentName.EXPLORE])


class Task(
    BaseTool[TaskArgs, TaskResult, TaskToolConfig, BaseToolState],
    ToolUIData[TaskArgs, TaskResult],
):
    effect_kind = ToolEffectKind.SUBAGENT

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        args = event.args
        if isinstance(args, TaskArgs):
            message = f"{args.agent} agent: {args.task}"
            return ToolCallDisplay(
                summary=f"Running {message}",
                verb="Running",
                message=message,
                settled_verb="Ran",
                settled_message=message,
            )
        return ToolCallDisplay(
            summary="Running subagent",
            verb="Running",
            message="subagent",
            settled_verb="Ran",
            settled_message="subagent",
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        result = event.result
        if isinstance(result, TaskResult):
            turn_word = "turn" if result.turns_used == 1 else "turns"
            if not result.completed:
                return ToolResultDisplay(
                    success=False,
                    verb="Interrupted",
                    message=f"after {result.turns_used} {turn_word}",
                )
            return ToolResultDisplay(
                success=True,
                verb="Completed",
                message=f"in {result.turns_used} {turn_word}",
            )
        return ToolResultDisplay(success=True, verb="Completed", message="")

    @classmethod
    def get_status_text(cls) -> str:
        return "Running subagent"

    def resolve_permission(self, args: TaskArgs) -> PermissionContext | None:
        agent_name = args.agent

        for pattern in self.config.denylist:
            if fnmatch.fnmatch(agent_name, pattern):
                return PermissionContext(permission=ToolPermission.NEVER)

        for pattern in self.config.allowlist:
            if fnmatch.fnmatch(agent_name, pattern):
                return PermissionContext(permission=ToolPermission.ALWAYS)

        return None

    async def run(
        self, args: TaskArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TaskResult, None]:
        if not ctx or not ctx.agent_manager:
            raise ToolError("Task tool requires agent_manager in context")

        agent_manager = ctx.agent_manager
        if agent_manager.active_profile.agent_type is AgentType.SUBAGENT:
            raise ToolError(
                "Agent depth limit of 1 reached. Complete the task in the current "
                "subagent."
            )

        try:
            agent_profile = agent_manager.get_agent(args.agent)
        except ValueError as e:
            raise ToolError(f"Unknown agent: {args.agent}") from e

        if agent_profile.agent_type != AgentType.SUBAGENT:
            raise ToolError(
                f"Agent '{args.agent}' is a {agent_profile.agent_type.value} agent. "
                f"Only subagents can be used with the task tool. "
                f"This is a security constraint to prevent recursive spawning."
            )
        if ctx.subagent_runner is None:
            raise ToolError("Task tool requires a subagent runner in context")

        async for event in ctx.subagent_runner.run(args, ctx):
            yield event
