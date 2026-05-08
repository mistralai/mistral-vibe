from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    make_test_models,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import ModelConfig
from vibe.core.types import (
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    LLMMessage,
    Role,
    UserMessageEvent,
)


def _get_auto_compact_properties(
    telemetry_events: list[dict[str, object]],
) -> dict[str, object]:
    auto_compact = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.auto_compact_triggered"
    ]
    assert len(auto_compact) == 1
    return cast(dict[str, object], auto_compact[0]["properties"])


@pytest.mark.asyncio
async def test_auto_compact_emits_correct_events(telemetry_events: list[dict]) -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2
    old_session_id = agent.session_id

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

    properties = _get_auto_compact_properties(telemetry_events)
    assert properties["nb_context_tokens_before"] == 2
    assert properties["nb_context_tokens_after"] == end.new_context_tokens
    assert properties["auto_compact_threshold"] == 1
    assert properties["status"] == "success"
    assert properties["session_id"] == old_session_id
    assert properties["parent_session_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "expected_exception", "match", "expected_status"),
    [
        pytest.param(
            RuntimeError("boom"), RuntimeError, "boom", "failure", id="failure"
        ),
        pytest.param(
            asyncio.CancelledError(),
            asyncio.CancelledError,
            None,
            "cancelled",
            id="cancelled",
        ),
    ],
)
async def test_auto_compact_emits_terminal_telemetry(
    side_effect: BaseException,
    expected_exception: type[BaseException],
    match: str | None,
    expected_status: str,
    telemetry_events: list[dict],
) -> None:
    backend = FakeBackend([[mock_llm_chunk(content="<final>")]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2
    old_session_id = agent.session_id

    events = []
    with patch.object(agent, "compact", AsyncMock(side_effect=side_effect)):
        if match is None:
            with pytest.raises(expected_exception):
                async for event in agent.act("Hello"):
                    events.append(event)
        else:
            with pytest.raises(expected_exception, match=match):
                async for event in agent.act("Hello"):
                    events.append(event)

    assert len(events) == 2
    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], CompactStartEvent)

    properties = _get_auto_compact_properties(telemetry_events)
    assert properties["nb_context_tokens_before"] == 2
    assert properties["nb_context_tokens_after"] == 2
    assert properties["auto_compact_threshold"] == 1
    assert properties["status"] == expected_status
    assert properties["session_id"] == old_session_id
    assert properties["parent_session_id"] is None


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
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
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
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
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
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    # After compact + final response: system, summary, final
    assert agent.messages[0].role == Role.system
    assert agent.messages[-1].role == Role.assistant
    assert agent.messages[-1].content == "<final>"


class _ModelTrackingBackend(FakeBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requested_models: list[ModelConfig] = []

    async def complete(self, *, model, **kwargs):
        self.requested_models.append(model)
        return await super().complete(model=model, **kwargs)


@pytest.mark.asyncio
async def test_compact_uses_compaction_model() -> None:
    """When compaction_model is set, compact() uses it instead of active_model."""
    compaction = ModelConfig(
        name="compaction-model",
        provider="mistral",
        alias="compaction",
        auto_compact_threshold=1,
    )
    backend = _ModelTrackingBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=1), compaction_model=compaction
    )
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    assert backend.requested_models[0].name == "compaction-model"
    assert backend.requested_models[1].name != "compaction-model"


@pytest.mark.asyncio
async def test_compact_uses_active_model_when_no_compaction_model() -> None:
    """Without compaction_model, compact() falls back to the active model."""
    backend = _ModelTrackingBackend([
        [mock_llm_chunk(content="<summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    active = cfg.get_active_model()
    assert backend.requested_models[0].name == active.name
    assert backend.requested_models[1].name == active.name


@pytest.mark.asyncio
async def test_compact_appends_extra_instructions_to_prompt() -> None:
    backend = FakeBackend([[mock_llm_chunk(content="<summary>")]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    await agent.compact(extra_instructions="focus on auth")

    compaction_prompt = backend.requests_messages[0][-1].content
    assert compaction_prompt is not None
    assert "## Additional Instructions" in compaction_prompt
    assert "focus on auth" in compaction_prompt


@pytest.mark.asyncio
async def test_compact_without_extra_instructions_has_no_additional_section() -> None:
    backend = FakeBackend([[mock_llm_chunk(content="<summary>")]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    await agent.compact()

    compaction_prompt = backend.requests_messages[0][-1].content
    assert compaction_prompt is not None
    assert "## Additional Instructions" not in compaction_prompt
