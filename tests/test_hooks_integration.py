"""Integration tests: verify hooks fire from AgentLoop at correct lifecycle points."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config._settings import HookEntry, HooksConfig
from vibe.core.types import (
    ApprovalResponse,
    BaseEvent,
    FunctionCall,
    ToolCall,
)


# ── Helpers ──


def _hook_config_all_events(cmd: str = "cat") -> HooksConfig:
    """Create a HooksConfig with the same command registered for all events."""
    entry = [HookEntry(command=cmd)]
    return HooksConfig(
        session_start=list(entry),
        user_prompt_submit=list(entry),
        pre_tool_use=list(entry),
        post_tool_use=list(entry),
        turn_end=list(entry),
    )


def _mock_proc() -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _make_agent(
    hooks: HooksConfig,
    backend: FakeBackend | None = None,
    auto_approve: bool = True,
) -> AgentLoop:
    config = build_test_vibe_config(
        hooks=hooks,
        enabled_tools=["todo"],
        tools={"todo": {"permission": "always"}},
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent_name = BuiltinAgentName.AUTO_APPROVE if auto_approve else BuiltinAgentName.DEFAULT
    return build_test_agent_loop(
        config=config,
        agent_name=agent_name,
        backend=backend or FakeBackend(),
    )


def _make_todo_tool_call(call_id: str = "call_1") -> ToolCall:
    return ToolCall(
        id=call_id,
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )


async def _act_and_collect(agent: AgentLoop, prompt: str) -> list[BaseEvent]:
    return [ev async for ev in agent.act(prompt)]


def _extract_payloads(mock_shell: AsyncMock) -> list[dict]:
    """Extract JSON payloads from all create_subprocess_shell calls."""
    payloads = []
    for call in mock_shell.call_args_list:
        # The proc.communicate is called with input=payload_bytes
        proc = mock_shell.return_value
        for comm_call in proc.communicate.call_args_list:
            raw = comm_call[1].get("input") or comm_call[0][0] if comm_call[0] else None
            if raw is None:
                raw = comm_call[1].get("input")
            if raw:
                payloads.append(json.loads(raw))
    return payloads


# ── Tests ──


class TestSessionStartHook:
    @pytest.mark.asyncio
    async def test_fires_once_on_first_act(self) -> None:
        hooks = HooksConfig(session_start=[HookEntry(command="cat")])
        agent = _make_agent(hooks)
        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc) as mock_shell:
            await _act_and_collect(agent, "hello")
            await _act_and_collect(agent, "world")
        # session_start should fire exactly once
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                p = json.loads(raw)
                if p["hook_event_name"] == "session_start":
                    payloads.append(p)
        assert len(payloads) == 1


class TestUserPromptSubmitHook:
    @pytest.mark.asyncio
    async def test_fires_per_prompt(self) -> None:
        hooks = HooksConfig(user_prompt_submit=[HookEntry(command="cat")])
        agent = _make_agent(hooks)
        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc):
            await _act_and_collect(agent, "hello")
            await _act_and_collect(agent, "world")
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                p = json.loads(raw)
                if p["hook_event_name"] == "user_prompt_submit":
                    payloads.append(p)
        assert len(payloads) == 2
        assert payloads[0]["prompt"] == "hello"
        assert payloads[1]["prompt"] == "world"


class TestToolHooks:
    @pytest.mark.asyncio
    async def test_pre_and_post_tool_use_on_success(self) -> None:
        hooks = HooksConfig(
            pre_tool_use=[HookEntry(command="cat")],
            post_tool_use=[HookEntry(command="cat")],
        )
        tool_call = _make_todo_tool_call()
        backend = FakeBackend([
            [mock_llm_chunk(content="Checking todos.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Done.")],
        ])
        agent = _make_agent(hooks, backend=backend)
        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc):
            await _act_and_collect(agent, "show todos")
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                payloads.append(json.loads(raw))
        pre = [p for p in payloads if p["hook_event_name"] == "pre_tool_use"]
        post = [p for p in payloads if p["hook_event_name"] == "post_tool_use"]
        assert len(pre) == 1
        assert pre[0]["tool_name"] == "todo"
        assert len(post) == 1
        assert post[0]["tool_outcome"] == "success"
        assert post[0]["tool_name"] == "todo"

    @pytest.mark.asyncio
    async def test_post_tool_use_skipped_no_pre(self) -> None:
        hooks = HooksConfig(
            pre_tool_use=[HookEntry(command="cat")],
            post_tool_use=[HookEntry(command="cat")],
        )
        tool_call = _make_todo_tool_call()
        backend = FakeBackend([
            [mock_llm_chunk(content="Checking.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Ok.")],
        ])
        # Use ASK permission so the approval callback is consulted
        config = build_test_vibe_config(
            hooks=hooks,
            enabled_tools=["todo"],
            tools={"todo": {"permission": "ask"}},
            system_prompt_id="tests",
            include_project_context=False,
            include_prompt_detail=False,
        )
        agent = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.DEFAULT,
            backend=backend,
        )

        async def reject_tool(name, args, call_id, perms) -> tuple[ApprovalResponse, str | None]:
            return ApprovalResponse.NO, "not allowed"

        agent.approval_callback = reject_tool

        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc):
            await _act_and_collect(agent, "show todos")
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                payloads.append(json.loads(raw))
        pre = [p for p in payloads if p["hook_event_name"] == "pre_tool_use"]
        post = [p for p in payloads if p["hook_event_name"] == "post_tool_use"]
        assert len(pre) == 0  # no pre_tool_use for skipped
        assert len(post) == 1
        assert post[0]["tool_outcome"] == "skipped"


class TestTurnEndHook:
    @pytest.mark.asyncio
    async def test_fires_after_act(self) -> None:
        hooks = HooksConfig(turn_end=[HookEntry(command="cat")])
        agent = _make_agent(hooks)
        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc):
            await _act_and_collect(agent, "hello")
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                payloads.append(json.loads(raw))
        turn_ends = [p for p in payloads if p["hook_event_name"] == "turn_end"]
        assert len(turn_ends) == 1


class TestFullLifecycleOrdering:
    @pytest.mark.asyncio
    async def test_hook_ordering_with_tool_call(self) -> None:
        hooks = _hook_config_all_events("cat")
        tool_call = _make_todo_tool_call()
        backend = FakeBackend([
            [mock_llm_chunk(content="Checking.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Done.")],
        ])
        agent = _make_agent(hooks, backend=backend)
        proc = _mock_proc()
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell", return_value=proc):
            await _act_and_collect(agent, "show todos")
        payloads = []
        for comm_call in proc.communicate.await_args_list:
            raw = comm_call[1].get("input")
            if raw:
                payloads.append(json.loads(raw))
        event_names = [p["hook_event_name"] for p in payloads]
        # Verify ordering
        assert event_names.index("session_start") < event_names.index("user_prompt_submit")
        assert event_names.index("user_prompt_submit") < event_names.index("pre_tool_use")
        assert event_names.index("pre_tool_use") < event_names.index("post_tool_use")
        assert event_names.index("post_tool_use") < event_names.index("turn_end")


class TestNoHooksByDefault:
    @pytest.mark.asyncio
    async def test_no_subprocess_spawned(self) -> None:
        agent = build_test_agent_loop()  # default config, no hooks
        with patch("vibe.core.hooks.asyncio.create_subprocess_shell") as mock_shell:
            await _act_and_collect(agent, "hello")
        mock_shell.assert_not_called()
