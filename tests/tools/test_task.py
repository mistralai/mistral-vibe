from __future__ import annotations

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from tests.mock.utils import collect_result
from tests.stubs.fake_interaction_requests import FakeInteractionRequests
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BUILTIN_AGENTS, AgentType
from vibe.core.config import VibeConfigSchema
from vibe.core.telemetry.types import LaunchContext, TerminalEmulator
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError, ToolPermission
from vibe.core.tools.builtins.task import Task, TaskArgs, TaskResult, TaskToolConfig
from vibe.core.tools.permissions import PermissionContext
from vibe.core.types import ToolStreamEvent


@pytest.fixture
def task_tool() -> Task:
    return Task(config_getter=lambda: TaskToolConfig(), state=BaseToolState())


class TestTaskArgs:
    def test_default_agent_is_explore(self) -> None:
        args = TaskArgs(task="do something")
        assert args.agent == "explore"

    def test_custom_values(self) -> None:
        args = TaskArgs(task="do something", agent="explore")
        assert args.task == "do something"
        assert args.agent == "explore"


class TestTaskToolValidation:
    @pytest.fixture
    def ctx(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> InvokeContext:
        config = build_config()
        manager = AgentManager(load_orchestrator(config))
        return InvokeContext(
            tool_call_id="test-call-id",
            agent_manager=manager,
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
        )

    @pytest.mark.asyncio
    async def test_rejects_primary_agent(
        self, task_tool: Task, ctx: InvokeContext
    ) -> None:
        args = TaskArgs(task="do something", agent="default")

        with pytest.raises(ToolError) as exc_info:
            await collect_result(task_tool.run(args, ctx))

        assert "agent" in str(exc_info.value).lower()
        assert "subagent" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_agent(
        self, task_tool: Task, ctx: InvokeContext
    ) -> None:
        args = TaskArgs(task="do something", agent="nonexistent")

        with pytest.raises(ToolError) as exc_info:
            await collect_result(task_tool.run(args, ctx))

        assert "Unknown agent" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_requires_agent_manager_in_context(self, task_tool: Task) -> None:
        args = TaskArgs(task="do something", agent="explore")
        ctx = InvokeContext(tool_call_id="test-call-id")  # No agent_manager

        with pytest.raises(ToolError) as exc_info:
            await collect_result(task_tool.run(args, ctx))

        assert "agent_manager" in str(exc_info.value).lower()

    def test_explore_agent_is_valid_subagent(self) -> None:
        agent = BUILTIN_AGENTS["explore"]
        assert agent.agent_type == AgentType.SUBAGENT


class TestTaskToolResolvePermission:
    def test_explore_allowed_by_default(self, task_tool: Task) -> None:
        args = TaskArgs(task="do something", agent="explore")
        result = task_tool.resolve_permission(args)
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_unknown_agent_returns_none(self, task_tool: Task) -> None:
        args = TaskArgs(task="do something", agent="custom_agent")
        result = task_tool.resolve_permission(args)
        assert result is None

    def test_denylist_takes_precedence(self) -> None:
        config = TaskToolConfig(allowlist=["explore"], denylist=["explore"])
        tool = Task(config_getter=lambda: config, state=BaseToolState())
        args = TaskArgs(task="do something", agent="explore")
        result = tool.resolve_permission(args)
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_glob_pattern_in_allowlist(self) -> None:
        config = TaskToolConfig(allowlist=["exp*"])
        tool = Task(config_getter=lambda: config, state=BaseToolState())
        args = TaskArgs(task="do something", agent="explore")
        result = tool.resolve_permission(args)
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_glob_pattern_in_denylist(self) -> None:
        config = TaskToolConfig(denylist=["danger*"])
        tool = Task(config_getter=lambda: config, state=BaseToolState())
        args = TaskArgs(task="do something", agent="dangerous_agent")
        result = tool.resolve_permission(args)
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_empty_lists_returns_none(self) -> None:
        config = TaskToolConfig(allowlist=[], denylist=[])
        tool = Task(config_getter=lambda: config, state=BaseToolState())
        args = TaskArgs(task="do something", agent="explore")
        result = tool.resolve_permission(args)
        assert result is None

    def test_default_config_has_explore_in_allowlist(self) -> None:
        config = TaskToolConfig()
        assert "explore" in config.allowlist


class TestTaskToolExecution:
    @pytest.fixture
    def ctx(
        self,
        build_config: ConfigBuilder,
        load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    ) -> InvokeContext:
        config = build_config()
        manager = AgentManager(load_orchestrator(config))
        runner = FakeSubagentRunner([
            ToolStreamEvent(
                tool_name="task",
                message="read_file: completed",
                tool_call_id="test-call-id",
            ),
            TaskResult(response="done", turns_used=1, completed=True),
        ])
        return InvokeContext(
            tool_call_id="test-call-id",
            agent_manager=manager,
            interaction_requests=FakeInteractionRequests(),
            subagent_runner=runner,
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
        )

    @pytest.mark.asyncio
    async def test_happy_path_returns_subagent_response(
        self, task_tool: Task, ctx: InvokeContext
    ) -> None:
        args = TaskArgs(task="explore the codebase", agent="explore")
        events = [event async for event in task_tool.run(args, ctx)]

        assert isinstance(events[0], ToolStreamEvent)
        assert events[0].message == "read_file: completed"
        assert events[1] == TaskResult(response="done", turns_used=1, completed=True)
        runner = ctx.subagent_runner
        assert isinstance(runner, FakeSubagentRunner)
        assert runner.calls == [(args, ctx)]

    @pytest.mark.asyncio
    async def test_requires_subagent_runner(
        self, task_tool: Task, ctx: InvokeContext
    ) -> None:
        ctx.subagent_runner = None

        with pytest.raises(ToolError, match="subagent runner"):
            await collect_result(
                task_tool.run(TaskArgs(task="do something", agent="explore"), ctx)
            )


class FakeSubagentRunner:
    def __init__(self, events: list[ToolStreamEvent | TaskResult]) -> None:
        self.events = events
        self.calls: list[tuple[TaskArgs, InvokeContext]] = []

    async def run(self, args: TaskArgs, ctx: InvokeContext):
        self.calls.append((args, ctx))
        for event in self.events:
            yield event
