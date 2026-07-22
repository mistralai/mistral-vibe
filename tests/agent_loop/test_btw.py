from __future__ import annotations

from contextlib import aclosing

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.commands import CommandRegistry
from vibe.core.types import LLMMessage, Role


def _history_snapshot(agent_loop) -> list[dict]:
    return [m.model_dump(mode="json", exclude_none=True) for m in agent_loop.messages]


@pytest.mark.asyncio
async def test_btw_leaves_main_history_byte_identical() -> None:
    backend = FakeBackend(mock_llm_chunk(content="side answer", prompt_tokens=7))
    agent_loop = build_test_agent_loop(backend=backend)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.assistant, content="hi there"),
    ])
    before = _history_snapshot(agent_loop)

    async with aclosing(agent_loop.btw("what is InvokeContext?")) as chunks:
        answer = "".join([chunk async for chunk in chunks])

    assert answer == "side answer"
    assert _history_snapshot(agent_loop) == before


@pytest.mark.asyncio
async def test_btw_sends_empty_tools() -> None:
    backend = FakeBackend(mock_llm_chunk(content="ok"))
    agent_loop = build_test_agent_loop(backend=backend)
    agent_loop.messages.append(LLMMessage(role=Role.user, content="context"))

    async with aclosing(agent_loop.btw("side question")) as chunks:
        async for _ in chunks:
            pass

    assert backend.requests_tools == [[]]
    assert backend.requests_tool_choices == [None]
    last_message = backend.requests_messages[-1][-1]
    assert last_message.role == Role.user
    assert last_message.content == "side question"


@pytest.mark.asyncio
async def test_btw_counts_usage_in_session_stats() -> None:
    backend = FakeBackend(
        mock_llm_chunk(content="ok", prompt_tokens=11, completion_tokens=3)
    )
    agent_loop = build_test_agent_loop(backend=backend)
    before_prompt = agent_loop.stats.session_prompt_tokens
    before_completion = agent_loop.stats.session_completion_tokens

    async with aclosing(agent_loop.btw("cost me")) as chunks:
        async for _ in chunks:
            pass

    assert agent_loop.stats.session_prompt_tokens == before_prompt + 11
    assert agent_loop.stats.session_completion_tokens == before_completion + 3


@pytest.mark.asyncio
async def test_btw_api_error_leaves_session_intact() -> None:
    backend = FakeBackend(exception_to_raise=RuntimeError("boom"))
    agent_loop = build_test_agent_loop(backend=backend)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.assistant, content="hi"),
    ])
    before = _history_snapshot(agent_loop)
    before_prompt = agent_loop.stats.session_prompt_tokens

    with pytest.raises(RuntimeError, match="API error"):
        async with aclosing(agent_loop.btw("will fail")) as chunks:
            async for _ in chunks:
                pass

    assert _history_snapshot(agent_loop) == before
    assert agent_loop.stats.session_prompt_tokens == before_prompt


@pytest.mark.asyncio
async def test_btw_streaming_leaves_history_unchanged() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="part ", prompt_tokens=0, completion_tokens=0),
        mock_llm_chunk(content="two", prompt_tokens=4, completion_tokens=2),
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
    before = _history_snapshot(agent_loop)

    async with aclosing(agent_loop.btw("stream me")) as chunks:
        answer = "".join([chunk async for chunk in chunks])

    assert answer == "part two"
    assert _history_snapshot(agent_loop) == before
    assert backend.requests_tools == [[]]


def test_btw_command_registration() -> None:
    registry = CommandRegistry()
    assert registry.get_command_name("/btw") == "btw"
    result = registry.parse_command("/btw what is this?")
    assert result is not None
    cmd_name, cmd, cmd_args = result
    assert cmd_name == "btw"
    assert cmd.handler == "_btw_command"
    assert cmd_args == "what is this?"
    assert "/btw" in registry.get_help_text()


def test_btw_command_without_args_parses_empty_args() -> None:
    registry = CommandRegistry()
    result = registry.parse_command("/btw")
    assert result is not None
    _, _, cmd_args = result
    assert cmd_args == ""


def test_programmatic_btw_returns_interactive_only_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from tests.conftest import build_test_vibe_config
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.core.programmatic import run_programmatic

    fake_loop = MagicMock()
    fake_loop.emit_session_closed_telemetry = MagicMock()
    fake_loop.aclose = AsyncMock()
    fake_loop.telemetry_client.aclose = AsyncMock()
    monkeypatch.setattr(
        "vibe.core.programmatic.AgentLoop", lambda *args, **kwargs: fake_loop
    )

    result = run_programmatic(
        FakeConfigOrchestrator(build_test_vibe_config()), prompt="/btw what is this?"
    )

    assert result == "/btw is only available in interactive mode."
    fake_loop.act.assert_not_called()
