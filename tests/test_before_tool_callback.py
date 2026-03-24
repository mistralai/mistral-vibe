"""Tests for the before_tool_callback hook on AgentLoop.

The hook lets callers intercept every tool call before execution and
optionally rewrite the arguments — e.g. prepend ``rtk`` to bash commands.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
)
from vibe.core.types import FunctionCall, ToolCall, ToolResultEvent, ToolStreamEvent


# ---------------------------------------------------------------------------
# Minimal stub tool with a rewritable ``command`` field
# ---------------------------------------------------------------------------


class CommandArgs(BaseModel):
    command: str


class CommandResult(BaseModel):
    executed_command: str


class CommandToolState(BaseToolState):
    pass


class FakeCommandTool(
    BaseTool[CommandArgs, CommandResult, BaseToolConfig, CommandToolState]
):
    """Records the exact ``command`` it was invoked with for test assertions."""

    # Class-level storage so tests can read it after the agent loop finishes.
    _last_command: str = ""

    @classmethod
    def get_name(cls) -> str:
        return "fake_command"

    async def run(
        self, args: CommandArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CommandResult, None]:
        FakeCommandTool._last_command = args.command
        yield CommandResult(executed_command=args.command)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(call_id: str, command: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        index=0,
        function=FunctionCall(
            name="fake_command", arguments=f'{{"command": "{command}"}}'
        ),
    )


def _make_agent_loop(backend: FakeBackend) -> AgentLoop:
    """Build an AgentLoop with FakeCommandTool registered directly."""
    config = build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    loop = build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=backend,
    )
    # Register the stub tool without touching the filesystem.
    loop.tool_manager._available["fake_command"] = FakeCommandTool
    return loop


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_receives_tool_name_and_args() -> None:
    """Callback receives the correct tool_name and args_dict."""
    captured: list[tuple[str, dict[str, Any]]] = []

    async def spy(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        captured.append((tool_name, dict(args)))
        return None  # passthrough

    backend = FakeBackend([
        [mock_llm_chunk(content="Running.", tool_calls=[_tool_call("c1", "git status")])],
        [mock_llm_chunk(content="Done.")],
    ])
    loop = _make_agent_loop(backend)
    loop.set_before_tool_callback(spy)

    async for _ in loop.act("run git status"):
        pass

    assert len(captured) == 1
    assert captured[0][0] == "fake_command"
    assert captured[0][1]["command"] == "git status"


@pytest.mark.asyncio
async def test_callback_none_leaves_args_unchanged() -> None:
    """Returning None from callback leaves the original args intact."""
    async def passthrough(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        return None

    backend = FakeBackend([
        [mock_llm_chunk(content="Running.", tool_calls=[_tool_call("c2", "git status")])],
        [mock_llm_chunk(content="Done.")],
    ])
    loop = _make_agent_loop(backend)
    loop.set_before_tool_callback(passthrough)

    async for _ in loop.act("run git status"):
        pass

    assert FakeCommandTool._last_command == "git status"


@pytest.mark.asyncio
async def test_callback_can_rewrite_args() -> None:
    """Returning a modified dict rewrites the args delivered to the tool."""
    async def rtk_rewrite(
        tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        if tool_name == "fake_command":
            cmd = args.get("command", "")
            if not cmd.startswith("rtk "):
                return {**args, "command": f"rtk {cmd}"}
        return None

    backend = FakeBackend([
        [mock_llm_chunk(content="Running.", tool_calls=[_tool_call("c3", "git status")])],
        [mock_llm_chunk(content="Done.")],
    ])
    loop = _make_agent_loop(backend)
    loop.set_before_tool_callback(rtk_rewrite)

    async for _ in loop.act("run git status"):
        pass

    assert FakeCommandTool._last_command == "rtk git status"


@pytest.mark.asyncio
async def test_no_callback_tool_executes_normally() -> None:
    """Without a callback, the tool receives the original args."""
    backend = FakeBackend([
        [mock_llm_chunk(content="Listing.", tool_calls=[_tool_call("c4", "ls -la")])],
        [mock_llm_chunk(content="Done.")],
    ])
    loop = _make_agent_loop(backend)
    assert loop.before_tool_callback is None

    async for _ in loop.act("list files"):
        pass

    assert FakeCommandTool._last_command == "ls -la"


@pytest.mark.asyncio
async def test_rewritten_args_produce_successful_tool_result() -> None:
    """ToolResultEvent has no error when args are rewritten by the callback."""
    async def rewrite(
        tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        return {**args, "command": "rtk git status"}

    backend = FakeBackend([
        [mock_llm_chunk(content="Running.", tool_calls=[_tool_call("c5", "git status")])],
        [mock_llm_chunk(content="Done.")],
    ])
    loop = _make_agent_loop(backend)
    loop.set_before_tool_callback(rewrite)

    events = [ev async for ev in loop.act("run git status")]

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 1
    assert results[0].error is None
    assert results[0].skipped is False
    assert FakeCommandTool._last_command == "rtk git status"
