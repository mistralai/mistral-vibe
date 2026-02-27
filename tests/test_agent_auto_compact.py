from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.types import (
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    LLMMessage,
    Role,
    UserMessageEvent,
)


@pytest.mark.asyncio
async def test_auto_compact_emits_correct_events(telemetry_events: list[dict]) -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(auto_compact_threshold=1)
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    events = [ev async for ev in agent.act("Hello")]

    assert len(events) == 4
    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], CompactStartEvent)
    assert isinstance(events[2], CompactEndEvent)
    assert isinstance(events[3], AssistantEvent)
    start: CompactStartEvent = events[1]
    end: CompactEndEvent = events[2]
    final: AssistantEvent = events[3]
    assert start.current_context_tokens == 2
    assert start.threshold == 1
    assert end.old_context_tokens == 2
    assert end.new_context_tokens >= 1
    assert final.content == "<final>"

    auto_compact = [
        e
        for e in telemetry_events
        if e.get("event_name") == "vibe.auto_compact_triggered"
    ]
    assert len(auto_compact) == 1


@pytest.mark.asyncio
async def test_auto_compact_observer_sees_user_msg_not_summary() -> None:
    """Observer sees the original user message and final response.

    Compact internals (summary request, LLM summary) are invisible
    to the observer because they happen inside silent() / reset().
    """
    observed: list[tuple[Role, str | None]] = []

    def observer(msg: LLMMessage) -> None:
        observed.append((msg.role, msg.content))

    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(auto_compact_threshold=1)
    agent = build_test_agent_loop(
        config=cfg, message_observer=observer, backend=backend
    )
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    roles = [r for r, _ in observed]
    assert roles == [Role.system, Role.user, Role.assistant]
    assert observed[1][1] == "Hello"
    assert observed[2][1] == "<final>"


@pytest.mark.asyncio
async def test_auto_compact_observer_does_not_see_summary_request() -> None:
    """The compact summary request and LLM response must not leak to observer."""
    observed: list[tuple[Role, str | None]] = []

    def observer(msg: LLMMessage) -> None:
        observed.append((msg.role, msg.content))

    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(auto_compact_threshold=1)
    agent = build_test_agent_loop(
        config=cfg, message_observer=observer, backend=backend
    )
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    contents = [c for _, c in observed]
    assert "<summary>" not in contents
    assert all("compact" not in (c or "").lower() for c in contents)


@pytest.mark.asyncio
async def test_compact_replaces_messages_with_summary() -> None:
    """After compact, messages list contains only system + summary."""
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(auto_compact_threshold=1)
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    # After compact + final response: system, summary, final
    assert agent.messages[0].role == Role.system
    assert agent.messages[-1].role == Role.assistant
    assert agent.messages[-1].content == "<final>"
