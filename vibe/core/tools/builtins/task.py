from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
import fnmatch
from typing import ClassVar

from pydantic import BaseModel, Field

from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import AgentType, BuiltinAgentName
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.mcp.registry import RemoteRegistry
from vibe.core.mcp.vibe_server import VibeMCPServer
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import (
    ToolCallDisplay,
    ToolResultDisplay,
    ToolUIData,
    ToolUIDataAdapter,
)
from vibe.core.types import (
    AssistantEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
)


class TaskArgs(BaseModel):
    task: str = Field(description="The task to delegate to the subagent")
    agent: str = Field(
        default="explore",
        description="Name of the agent profile to use (must be a subagent)",
    )


class TaskResult(BaseModel):
    response: str = Field(description="The accumulated response from the subagent")
    turns_used: int = Field(description="Number of turns the subagent used")
    completed: bool = Field(description="Whether the task completed normally")
    fallback_used: bool = Field(
        default=False,
        description="Whether local fallback was used due to remote failure"
    )


class TaskToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    allowlist: list[str] = Field(default=[BuiltinAgentName.EXPLORE])


class Task(
    BaseTool[TaskArgs, TaskResult, TaskToolConfig, BaseToolState],
    ToolUIData[TaskArgs, TaskResult],
):
    description: ClassVar[str] = (
        "Delegate a task to a subagent for independent execution. "
        "Useful for exploration, research, or parallel work that doesn't "
        "require user interaction. The subagent runs in-memory and "
        "saves interaction logs.\n\n"

        "REMOTE EXECUTION: Prefix agent names with 'server_name:' to "
        "execute on remote Vibe instances (e.g., 'main_server:explore'). "
        "If no server is specified, tasks run locally. Automatic fallback "
        "to local execution occurs if remote servers are unavailable."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        args = event.args
        if isinstance(args, TaskArgs):
            return ToolCallDisplay(summary=f"Running {args.agent} agent: {args.task}")
        return ToolCallDisplay(summary="Running subagent")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        result = event.result
        if isinstance(result, TaskResult):
            turn_word = "turn" if result.turns_used == 1 else "turns"
            if not result.completed:
                return ToolResultDisplay(
                    success=False,
                    message=f"Agent interrupted after {result.turns_used} {turn_word}",
                )
            return ToolResultDisplay(
                success=True,
                message=f"Agent completed in {result.turns_used} {turn_word}",
            )
        return ToolResultDisplay(success=True, message="Agent completed")

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

    def _get_remote_registry(self, ctx: InvokeContext) -> RemoteRegistry:
        """Get or create remote registry from context."""
        if not hasattr(ctx, "_remote_registry"):
            if not ctx.config:
                raise ToolError("Remote execution requires config in context")
            ctx._remote_registry = RemoteRegistry(ctx.config)
        return ctx._remote_registry

    def _is_remote_agent(self, agent_name: str, registry: RemoteRegistry) -> bool:
        """Check if agent should be executed remotely."""
        return registry.is_remote_agent(agent_name)

    def _parse_agent_address(self, agent_name: str, registry: RemoteRegistry) -> tuple[str, str]:
        """Parse agent address in format 'remote_name:agent_name'."""
        return registry.parse_agent_address(agent_name)

    async def _run_local_subagent(
        self, args: TaskArgs, ctx: InvokeContext
    ) -> AsyncGenerator[ToolStreamEvent | TaskResult, None]:
        """Execute subagent locally (original implementation)."""
        agent_manager = ctx.agent_manager

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

        session_logging = SessionLoggingConfig(
            save_dir=str(ctx.session_dir / "agents") if ctx.session_dir else "",
            session_prefix=args.agent,
            enabled=ctx.session_dir is not None,
        )
        base_config = VibeConfig.load(session_logging=session_logging)
        subagent_loop = AgentLoop(
            config=base_config,
            agent_name=args.agent,
            entrypoint_metadata=ctx.entrypoint_metadata,
            is_subagent=True,
        )

        if ctx and ctx.approval_callback:
            subagent_loop.set_approval_callback(ctx.approval_callback)

        accumulated_response: list[str] = []
        completed = True
        try:
            async with aclosing(subagent_loop.act(args.task)) as events:
                async for event in events:
                    if isinstance(event, AssistantEvent) and event.content:
                        accumulated_response.append(event.content)
                        if event.stopped_by_middleware:
                            completed = False
                    elif isinstance(event, ToolResultEvent):
                        if event.skipped:
                            completed = False
                        elif event.result and event.tool_class:
                            adapter = ToolUIDataAdapter(event.tool_class)
                            display = adapter.get_result_display(event)
                            message = f"{event.tool_name}: {display.message}"
                            yield ToolStreamEvent(
                                tool_name=self.get_name(),
                                message=message,
                                tool_call_id=ctx.tool_call_id,
                            )

            turns_used = sum(
                msg.role == Role.assistant for msg in subagent_loop.messages
            )

        except Exception as e:
            completed = False
            accumulated_response.append(f"\n[Subagent error: {e}]")
            turns_used = sum(
                msg.role == Role.assistant for msg in subagent_loop.messages
            )

        yield TaskResult(
            response="".join(accumulated_response),
            turns_used=turns_used,
            completed=completed,
        )

    async def _run_remote_subagent(
        self, args: TaskArgs, ctx: InvokeContext
    ) -> AsyncGenerator[ToolStreamEvent | TaskResult, None]:
        """Execute subagent on remote Vibe instance via MCP."""
        registry = self._get_remote_registry(ctx)

        try:
            # Parse agent address (remote_name:agent_name)
            remote_name, actual_agent_name = self._parse_agent_address(args.agent, registry)

            # Check if local fallback is allowed (for subagents only)
            allow_fallback = True
            try:
                agent_profile = ctx.agent_manager.get_agent(actual_agent_name)
                allow_fallback = agent_profile.agent_type == AgentType.SUBAGENT
            except:
                allow_fallback = False

            # Get remote registry and execute with fallback option
            accumulated_response: list[str] = []
            completed = True
            turns_used = 0
            fallback_triggered = False

            async for event_data in registry.execute_on_remote(
                remote_name=remote_name,
                tool_name=f"subagent_{actual_agent_name}",
                arguments={"task": args.task},
                fallback_to_local=allow_fallback
            ):
                # Convert MCP event data to our format
                event_type = event_data.get("type")
                if event_type == "assistant":
                    content = event_data.get("content", "")
                    if content:
                        accumulated_response.append(content)
                        yield ToolStreamEvent(
                            tool_name=self.get_name(),
                            message=content,
                            tool_call_id=ctx.tool_call_id,
                        )
                elif event_type == "tool_result":
                    tool_name = event_data.get("tool_name", "")
                    message = event_data.get("message", "")
                    if tool_name and message:
                        yield ToolStreamEvent(
                            tool_name=self.get_name(),
                            message=f"{tool_name}: {message}",
                            tool_call_id=ctx.tool_call_id,
                        )
                elif event_type == "error":
                    completed = False
                    error_msg = event_data.get("message", "Unknown error")
                    fallback_available = event_data.get("fallback_available", False)
                    accumulated_response.append(f"\n[Remote subagent error: {error_msg}]")

                    yield ToolStreamEvent(
                        tool_name=self.get_name(),
                        message=f"Remote error: {error_msg}",
                        tool_call_id=ctx.tool_call_id,
                        is_error=True,
                    )

                    # If fallback is available and enabled, try local execution
                    if fallback_available and allow_fallback:
                        yield ToolStreamEvent(
                            tool_name=self.get_name(),
                            message="Falling back to local execution...",
                            tool_call_id=ctx.tool_call_id,
                            is_info=True,
                        )
                        fallback_triggered = True

                        # Execute locally as fallback
                        async for fallback_event in self._run_local_subagent(
                            TaskArgs(task=args.task, agent=actual_agent_name),
                            ctx
                        ):
                            if isinstance(fallback_event, ToolStreamEvent):
                                yield fallback_event
                            elif isinstance(fallback_event, TaskResult):
                                # Update with fallback results
                                accumulated_response.append(fallback_event.response)
                                turns_used = fallback_event.turns_used
                                completed = fallback_event.completed

            # If we didn't fallback, count turns from remote execution
            if not fallback_triggered:
                turns_used = sum(
                    1 for msg in accumulated_response if msg.strip()
                )

            yield TaskResult(
                response="".join(accumulated_response),
                turns_used=turns_used,
                completed=completed,
                fallback_used=fallback_triggered,
            )

        except Exception as e:
            error_msg = f"Remote execution failed: {e}"

            # Try fallback if available
            if allow_fallback:
                yield ToolStreamEvent(
                    tool_name=self.get_name(),
                    message=f"{error_msg}. Falling back to local execution...",
                    tool_call_id=ctx.tool_call_id,
                    is_error=True,
                )

                # Execute locally as fallback
                async for fallback_event in self._run_local_subagent(
                    TaskArgs(task=args.task, agent=actual_agent_name),
                    ctx
                ):
                    if isinstance(fallback_event, ToolStreamEvent):
                        yield fallback_event
                    elif isinstance(fallback_event, TaskResult):
                        yield TaskResult(
                            response=fallback_event.response,
                            turns_used=fallback_event.turns_used,
                            completed=fallback_event.completed,
                            fallback_used=True,
                        )
                        return

            # No fallback available
            yield TaskResult(
                response=error_msg,
                turns_used=0,
                completed=False,
                fallback_used=False,
            )

    async def run(
        self, args: TaskArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | TaskResult, None]:
        if not ctx or not ctx.agent_manager:
            raise ToolError("Task tool requires agent_manager in context")

        # Route to remote or local execution based on agent name
        if self._is_remote_agent(args.agent):
            return await self._run_remote_subagent(args, ctx)
        else:
            return await self._run_local_subagent(args, ctx)
