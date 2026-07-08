from __future__ import annotations

from collections.abc import AsyncGenerator

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.types import (
    BaseEvent,
    ContextClearedEvent,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
    ToolStreamEvent,
)
from vibe.core.utils import VIBE_WARNING_TAG


class _ClearToolArgs(BaseModel):
    pass


class _ClearToolResult(BaseModel):
    message: str = "ok"


class _ClearRequestingTool(
    BaseTool[_ClearToolArgs, _ClearToolResult, BaseToolConfig, BaseToolState]
):
    """Stand-in for exit_plan_mode: it just requests the deferred context clear."""

    @classmethod
    def get_name(cls) -> str:
        return "stub_tool"

    async def run(
        self, args: _ClearToolArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | _ClearToolResult, None]:
        assert ctx is not None and ctx.request_clear_context_callback is not None
        await ctx.request_clear_context_callback()
        yield _ClearToolResult()


def _build_loop(backend: FakeBackend) -> AgentLoop:
    config = build_test_vibe_config(
        enabled_tools=["stub_tool"],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    loop = build_test_agent_loop(
        config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
    )
    loop.tool_manager._all_tools["stub_tool"] = _ClearRequestingTool
    return loop


def _tool_call() -> ToolCall:
    return ToolCall(
        id="call_1", index=0, function=FunctionCall(name="stub_tool", arguments="{}")
    )


@pytest.mark.asyncio
async def test_request_clear_context_sets_flag() -> None:
    loop = build_test_agent_loop()
    assert loop._pending_clear_context is False
    await loop._request_clear_context()
    assert loop._pending_clear_context is True


@pytest.mark.asyncio
async def test_deferred_clear_wipes_history_and_seeds_plan() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="Approving.", tool_calls=[_tool_call()])],
        [mock_llm_chunk(content="Implementing now.")],
    ])
    loop = _build_loop(backend)
    loop._plan_session.read = lambda: "# Approved Plan\n\n- Step 1"  # type: ignore[method-assign]

    events: list[BaseEvent] = [ev async for ev in loop.act("ship it")]

    cleared = [e for e in events if isinstance(e, ContextClearedEvent)]
    assert len(cleared) == 1
    # The plan path rides along so the UI can keep the plan on screen.
    assert cleared[0].plan_file_path == loop._plan_session.plan_file_path
    assert loop._pending_clear_context is False

    # History was wiped to the system prompt, then re-seeded with the plan and
    # the post-clear implementation turn.
    assert loop.messages[0].role == Role.system
    seed = loop.messages[1]
    assert seed.role == Role.user
    assert seed.injected is True
    assert "# Approved Plan" in (seed.content or "")
    assert VIBE_WARNING_TAG in (seed.content or "")
    # None of the pre-clear planning messages survive.
    assert not any("ship it" in (m.content or "") for m in loop.messages)


@pytest.mark.asyncio
async def test_deferred_clear_discards_pending_plan_update_injection() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="Approving.", tool_calls=[_tool_call()])],
        [mock_llm_chunk(content="Implementing now.")],
    ])
    loop = _build_loop(backend)
    loop._plan_session.read = lambda: "# Approved Plan\n\n- Step 1"  # type: ignore[method-assign]
    loop._pending_injected_messages.append(
        LLMMessage(
            role=Role.user, content="stale plan update from review", injected=True
        )
    )

    events: list[BaseEvent] = [ev async for ev in loop.act("ship it")]

    assert any(isinstance(e, ContextClearedEvent) for e in events)
    assert loop._pending_injected_messages == []
    assert len(backend.requests_messages) == 2
    assert not any(
        "stale plan update from review" in (m.content or "") for m in loop.messages
    )


@pytest.mark.asyncio
async def test_deferred_clear_with_empty_plan_appends_no_seed() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="Approving.", tool_calls=[_tool_call()])],
        [mock_llm_chunk(content="Implementing now.")],
    ])
    loop = _build_loop(backend)
    loop._plan_session.read = lambda: None  # type: ignore[method-assign]

    events: list[BaseEvent] = [ev async for ev in loop.act("ship it")]

    cleared = [e for e in events if isinstance(e, ContextClearedEvent)]
    assert len(cleared) == 1
    # No plan content means no path to re-display.
    assert cleared[0].plan_file_path is None
    assert loop._pending_clear_context is False
    # Clear happened (history reset to system prompt), no injected plan seed.
    assert loop.messages[0].role == Role.system
    assert not any(m.injected for m in loop.messages)
