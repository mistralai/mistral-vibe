from __future__ import annotations

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import (
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    LLMMessage,
    Role,
)


@pytest.mark.asyncio
async def test_auto_compact_triggers_and_batches_observer() -> None:
    observed: list[tuple[Role, str | None]] = []

    def observer(msg: LLMMessage) -> None:
        observed.append((msg.role, msg.content))

    backend = FakeBackend([
        mock_llm_chunk(content="<summary>"),
        mock_llm_chunk(content="<final>"),
    ])
    cfg = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), auto_compact_threshold=1
    )
    agent = Agent(cfg, message_observer=observer, backend=backend)
    agent.stats.context_tokens = 2

    events = [ev async for ev in agent.act("Hello")]

    assert len(events) == 3
    assert isinstance(events[0], CompactStartEvent)
    assert isinstance(events[1], CompactEndEvent)
    assert isinstance(events[2], AssistantEvent)
    start: CompactStartEvent = events[0]
    end: CompactEndEvent = events[1]
    final: AssistantEvent = events[2]
    assert start.current_context_tokens == 2
    assert start.threshold == 1
    assert end.old_context_tokens == 2
    assert end.new_context_tokens >= 1
    assert final.content == "<final>"

    roles = [r for r, _ in observed]
    assert roles == [Role.system, Role.user, Role.assistant]
    assert (
        observed[1][1] is not None
        and "Last request from user was: Hello" in observed[1][1]
    )
    assert observed[2][1] == "<final>"
