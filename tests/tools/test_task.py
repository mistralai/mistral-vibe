from __future__ import annotations

import pytest

from vibe.core.agents.models import BUILTIN_AGENTS, AgentType
from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins.task import Task, TaskArgs, TaskToolConfig
from vibe.core.tools.permissions import PermissionContext


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
