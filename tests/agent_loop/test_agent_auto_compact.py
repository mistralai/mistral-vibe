from __future__ import annotations

import asyncio
from pathlib import Path
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
from vibe.core.agent_loop import CompactionFailedError
from vibe.core.compaction import parse_previous_user_messages
from vibe.core.config import ModelConfig
from vibe.core.llm.exceptions import BackendError, PayloadSummary
from vibe.core.prompts import UtilityPrompt
from vibe.core.types import (
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextTooLongError,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
    UserMessageEvent,
)


def _ctx_too_long_error() -> BackendError:
    return BackendError(
        provider="mistral",
        endpoint="/chat/completions",
        status=400,
        reason="Bad Request",
        headers={},
        body_text="context too long",
        parsed_error=None,
        model="test",
        payload_summary=PayloadSummary(
            model="test",
            message_count=1,
            approx_chars=1,
            temperature=0.0,
            has_tools=False,
            tool_choice=None,
        ),
    )


def _tool_call_chunk() -> LLMChunk:
    return mock_llm_chunk(
        content="",
        tool_calls=[
            ToolCall(
                id="t1", index=0, function=FunctionCall(name="bash", arguments="{}")
            )
        ],
    )


class _ScriptedBackend(FakeBackend):
    """FakeBackend that can raise per call index and records tools/model."""

    def __init__(self, streams=None, *, raises_at=None):
        super().__init__(streams)
        self._raises_at = dict(raises_at or {})
        self._calls = 0
        self.requested_models: list[ModelConfig] = []
        self.requested_tools: list[object] = []
        self.requested_tool_choices: list[object] = []

    def _advance(
        self,
        *,
        model,
        messages,
        tools,
        tool_choice,
        extra_headers,
        metadata,
        max_tokens,
    ):
        index = self._calls
        self._calls += 1
        if index in self._raises_at:
            raise self._raises_at[index]
        self._requests_messages.append(list(messages))
        self._requests_extra_headers.append(extra_headers)
        self._requests_metadata.append(metadata)
        self._requests_max_tokens.append(max_tokens)
        self.requested_models.append(model)
        self.requested_tools.append(tools)
        self.requested_tool_choices.append(tool_choice)
        return self._streams.pop(0) if self._streams else [mock_llm_chunk(content="")]

    async def complete(
        self,
        *,
        model,
        messages,
        temperature,
        tools,
        tool_choice,
        extra_headers,
        max_tokens,
        metadata=None,
    ):
        stream = self._advance(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            extra_headers=extra_headers,
            metadata=metadata,
            max_tokens=max_tokens,
        )
        agg = LLMChunk(message=LLMMessage(role=Role.assistant))
        for chunk in stream:
            agg += chunk
        return agg

    async def complete_streaming(
        self,
        *,
        model,
        messages,
        temperature,
        tools,
        tool_choice,
        extra_headers,
        max_tokens,
        metadata=None,
    ):
        stream = self._advance(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            extra_headers=extra_headers,
            metadata=metadata,
            max_tokens=max_tokens,
        )
        for chunk in stream:
            yield chunk


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


def _get_compaction_failed_properties(
    telemetry_events: list[dict[str, object]],
) -> dict[str, object]:
    failed = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.compaction_failed"
    ]
    assert len(failed) == 1
    return cast(dict[str, object], failed[0]["properties"])


@pytest.mark.asyncio
async def test_auto_compact_emits_correct_events(telemetry_events: list[dict]) -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>done</summary>")],
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
    assert isinstance(end, CompactEndEvent)
    assert final.content == "<final>"

    properties = _get_auto_compact_properties(telemetry_events)
    assert properties["nb_context_tokens_before"] == 2
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
        [mock_llm_chunk(content="<summary>done</summary>")],
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
        [mock_llm_chunk(content="<summary>done</summary>")],
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
async def test_compact_replaces_messages_with_context() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>done</summary>")],
        [mock_llm_chunk(content="<final>")],
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=1))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 2

    [_ async for _ in agent.act("Hello")]

    # After compact + final response: system, compaction context, final.
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
        [mock_llm_chunk(content="<summary>done</summary>")],
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
        [mock_llm_chunk(content="<summary>done</summary>")],
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
    backend = FakeBackend([[mock_llm_chunk(content="<summary>done</summary>")]])
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
async def test_compact_uses_configured_compaction_prompt(
    mock_prompts_dirs: tuple[Path, Path],
) -> None:
    project_prompts, _ = mock_prompts_dirs
    (project_prompts / "theorem_compact.md").write_text("Summarize theorem progress")

    backend = FakeBackend([[mock_llm_chunk(content="<summary>done</summary>")]])
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=999),
        compaction_prompt_id="theorem_compact",
    )
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    await agent.compact()

    compaction_prompt = backend.requests_messages[0][-1].content
    assert compaction_prompt == "Summarize theorem progress"


@pytest.mark.asyncio
async def test_compact_without_extra_instructions_has_no_additional_section() -> None:
    backend = FakeBackend([[mock_llm_chunk(content="<summary>done</summary>")]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    await agent.compact()

    compaction_prompt = backend.requests_messages[0][-1].content
    assert compaction_prompt is not None
    assert "## Additional Instructions" not in compaction_prompt


@pytest.mark.asyncio
async def test_compact_raises_on_tool_call_when_flag_enabled(
    telemetry_events: list[dict],
) -> None:
    """With the flag on, a compaction that returns a tool call raises."""
    backend = FakeBackend([
        [
            mock_llm_chunk(
                content="",
                tool_calls=[
                    ToolCall(
                        id="t1",
                        index=0,
                        function=FunctionCall(name="bash", arguments="{}"),
                    )
                ],
            )
        ]
    ])
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=999),
        raise_on_compaction_failure=True,
    )
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    with pytest.raises(CompactionFailedError) as exc_info:
        await agent.compact()
    assert exc_info.value.reason == "tool_call"
    assert _get_compaction_failed_properties(telemetry_events)["reason"] == "tool_call"


@pytest.mark.asyncio
async def test_compact_raises_on_empty_summary_when_flag_enabled(
    telemetry_events: list[dict],
) -> None:
    """With the flag on, a compaction with empty content raises."""
    backend = FakeBackend([[mock_llm_chunk(content="   ")]])
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=999),
        raise_on_compaction_failure=True,
    )
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    with pytest.raises(CompactionFailedError) as exc_info:
        await agent.compact()
    assert exc_info.value.reason == "empty_summary"
    assert (
        _get_compaction_failed_properties(telemetry_events)["reason"] == "empty_summary"
    )


@pytest.mark.asyncio
async def test_compact_falls_back_when_flag_disabled(
    telemetry_events: list[dict],
) -> None:
    """With the flag off (default), empty content uses the legacy fallback.

    The compaction failure telemetry event must still be sent even though the
    flag is off and compact() returns normally.
    """
    backend = FakeBackend([[mock_llm_chunk(content="")]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()
    assert summary == "(no summary available)"
    assert (
        _get_compaction_failed_properties(telemetry_events)["reason"] == "empty_summary"
    )


@pytest.mark.asyncio
async def test_compact_message_shape_preserves_prior_user_messages() -> None:
    from vibe.core.compaction import parse_previous_user_messages
    from vibe.core.prompts import UtilityPrompt

    summary_prefix = UtilityPrompt.COMPACT_SUMMARY_PREFIX.read()
    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>fresh summary body</summary>")]
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    system_message_before = agent.messages[0]

    agent.messages.append(LLMMessage(role=Role.user, content="first real ask"))
    agent.messages.append(
        LLMMessage(role=Role.user, content="middleware ping", injected=True)
    )
    agent.messages.append(LLMMessage(role=Role.assistant, content="ack"))
    agent.messages.append(
        LLMMessage(
            role=Role.user,
            content=f"{summary_prefix}\nprior summary blob",
            injected=True,
        )
    )
    agent.messages.append(LLMMessage(role=Role.user, content="follow-up ask"))
    agent.stats.context_tokens = 100

    await agent.compact()

    final = list(agent.messages)
    assert len(final) == 2  # [system, compaction_context]
    assert final[0] is system_message_before
    assert final[1].role == Role.user
    assert final[1].injected is True
    assert parse_previous_user_messages(final[1].content or "") == [
        "first real ask",
        "follow-up ask",
    ]
    assert "Here are some of the most recent previous user messages" in (
        final[1].content or ""
    )
    assert "<compaction_summary>" in (final[1].content or "")
    assert "fresh summary body" in (final[1].content or "")
    # Injected and prior-summary user messages must be filtered out.
    assert all("middleware ping" not in (m.content or "") for m in final)
    assert sum("prior summary blob" in (m.content or "") for m in final) == 0


@pytest.mark.asyncio
async def test_compact_preserves_user_messages_across_repeated_compactions() -> None:
    from vibe.core.compaction import parse_previous_user_messages

    backend = FakeBackend([
        [mock_llm_chunk(content="<summary>summary one</summary>")],
        [mock_llm_chunk(content="<summary>summary two</summary>")],
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)

    agent.messages.append(LLMMessage(role=Role.user, content="first ask"))
    agent.stats.context_tokens = 100
    await agent.compact()

    agent.messages.append(LLMMessage(role=Role.user, content="second ask"))
    agent.stats.context_tokens = 100
    await agent.compact()

    final = list(agent.messages)
    assert len(final) == 2
    assert parse_previous_user_messages(final[1].content or "") == [
        "first ask",
        "second ask",
    ]


@pytest.mark.asyncio
async def test_compact_uses_fallback_when_primary_returns_tool_call(
    telemetry_events: list[dict],
) -> None:
    backend = _ScriptedBackend([
        [_tool_call_chunk()],  # primary attempt: invalid (tool call)
        [mock_llm_chunk(content="<summary>recovered</summary>")],  # dedicated fallback
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()

    assert summary == "recovered"
    # The fallback (second call) runs without tools, with the summarizer prompt.
    assert backend.requested_tools[1] is None
    assert backend.requested_tool_choices[1] is None
    assert backend.requested_models[1].thinking == "off"
    fallback_messages = backend.requests_messages[1]
    assert fallback_messages[0].role == Role.system
    assert fallback_messages[0].content == UtilityPrompt.COMPACT_SYSTEM.read()
    # The fallback recovered, so this is not a failure — no failure event fires.
    assert not any(
        e.get("event_name") == "vibe.compaction_failed" for e in telemetry_events
    )
    assert "recovered" in (agent.messages[1].content or "")


@pytest.mark.asyncio
async def test_compact_fallback_failure_returns_placeholder(
    telemetry_events: list[dict],
) -> None:
    # Primary invalid and the fallback also yields nothing usable.
    backend = _ScriptedBackend([[_tool_call_chunk()]])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()

    assert summary == "(no summary available)"
    # The terminal telemetry keeps the primary's failure mode, not the
    # fallback's always-empty one.
    assert _get_compaction_failed_properties(telemetry_events)["reason"] == "tool_call"


@pytest.mark.asyncio
async def test_compact_fallback_maps_context_too_long_error() -> None:
    # The fallback goes through _complete, so a context-length backend error is
    # surfaced as ContextTooLongError (not a raw backend/internal error).
    backend = _ScriptedBackend(
        [[_tool_call_chunk()]], raises_at={1: _ctx_too_long_error()}
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    with pytest.raises(ContextTooLongError):
        await agent.compact()


@pytest.mark.asyncio
async def test_reactive_compaction_recovers_from_overflow() -> None:
    backend = _ScriptedBackend(
        [
            [mock_llm_chunk(content="<summary>done</summary>")],  # compaction summary
            [mock_llm_chunk(content="<final>")],  # retried turn
        ],
        raises_at={0: _ctx_too_long_error()},  # the first turn overflows
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 0

    events = [ev async for ev in agent.act("Hello")]

    kinds = [type(ev) for ev in events]
    assert CompactStartEvent in kinds
    assert CompactEndEvent in kinds
    assert any(
        isinstance(ev, AssistantEvent) and ev.content == "<final>" for ev in events
    )
    assert agent.messages[-1].content == "<final>"
    assert parse_previous_user_messages(agent.messages[1].content or "") == ["Hello"]
    # The compaction summary is a utility call; the retried turn is the main one.
    call_types = [(m or {}).get("call_type") for m in backend.requests_metadata]
    assert call_types == ["secondary_call", "main_call"]


@pytest.mark.asyncio
async def test_reactive_recovery_does_not_consume_turn_budget() -> None:
    # Regression: the failed overflow attempt must not eat a turn. With
    # max_turns=1 the retried (compacted) turn must still run — if the failed
    # attempt's step weren't rolled back, TurnLimitMiddleware would STOP the
    # retry before it ever executes.
    backend = _ScriptedBackend(
        [
            [mock_llm_chunk(content="<summary>done</summary>")],  # compaction summary
            [mock_llm_chunk(content="<final>")],  # retried turn
        ],
        raises_at={0: _ctx_too_long_error()},  # the first turn overflows
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.set_max_turns(1)
    agent.stats.context_tokens = 0

    events = [ev async for ev in agent.act("Hello")]

    # The compacted turn completed within the single-turn budget.
    assert any(
        isinstance(ev, AssistantEvent) and ev.content == "<final>" for ev in events
    )
    assert agent.messages[-1].content == "<final>"
    # Exactly one productive turn was counted (user step + one LLM turn).
    assert agent.stats.steps == 2


@pytest.mark.asyncio
async def test_reactive_compaction_disabled_in_strict_mode() -> None:
    backend = _ScriptedBackend(
        [[mock_llm_chunk(content="<final>")]], raises_at={0: _ctx_too_long_error()}
    )
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=999),
        raise_on_compaction_failure=True,
    )
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.stats.context_tokens = 0

    with pytest.raises(ContextTooLongError):
        async for _ in agent.act("Hello"):
            pass


@pytest.mark.asyncio
async def test_compact_trims_history_when_summary_overflows() -> None:
    # First summary attempt overflows; after trimming the oldest round it fits.
    backend = _ScriptedBackend(
        [[mock_llm_chunk(content="<summary>ok</summary>")]],
        raises_at={0: _ctx_too_long_error()},
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="oldest ask"))
    agent.messages.append(LLMMessage(role=Role.assistant, content="work"))
    agent.messages.append(LLMMessage(role=Role.user, content="newest ask"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()

    assert summary == "ok"
    assert parse_previous_user_messages(agent.messages[1].content or "") == [
        "oldest ask",
        "newest ask",
    ]


@pytest.mark.asyncio
async def test_compact_restores_history_when_summary_keeps_overflowing() -> None:
    # Every summary attempt overflows, so PTL trimming eventually gives up.
    backend = _ScriptedBackend(
        [], raises_at={attempt: _ctx_too_long_error() for attempt in range(4)}
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="oldest ask"))
    agent.messages.append(LLMMessage(role=Role.assistant, content="old work"))
    agent.messages.append(LLMMessage(role=Role.user, content="middle ask"))
    agent.messages.append(LLMMessage(role=Role.assistant, content="middle work"))
    agent.messages.append(LLMMessage(role=Role.user, content="newest ask"))
    agent.stats.context_tokens = 100
    original_messages = list(agent.messages)

    with pytest.raises(ContextTooLongError):
        await agent.compact()

    # The trimmed history must not persist after a failed compaction.
    assert list(agent.messages) == original_messages


@pytest.mark.asyncio
async def test_compact_fallback_uses_trimmed_history_after_primary_overflow() -> None:
    backend = _ScriptedBackend(
        [
            [_tool_call_chunk()],  # primary retry (post-trim) returns bad content
            [mock_llm_chunk(content="<summary>recovered</summary>")],  # fallback
        ],
        raises_at={0: _ctx_too_long_error()},  # primary first attempt overflows
    )
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="oldest ask"))
    agent.messages.append(LLMMessage(role=Role.assistant, content="old work"))
    agent.messages.append(LLMMessage(role=Role.user, content="newest ask"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()

    assert summary == "recovered"
    # The fallback (2nd recorded request) summarizes the trimmed history.
    fallback_prompt = backend.requests_messages[1][1].content or ""
    assert "newest ask" in fallback_prompt
    assert "oldest ask" not in fallback_prompt


@pytest.mark.asyncio
async def test_compact_fallback_rejects_empty_summary_tags() -> None:
    # Primary fails, and the fallback returns an empty <summary></summary> block.
    backend = _ScriptedBackend([
        [_tool_call_chunk()],
        [mock_llm_chunk(content="<summary></summary>")],
    ])
    cfg = build_test_vibe_config(models=make_test_models(auto_compact_threshold=999))
    agent = build_test_agent_loop(config=cfg, backend=backend)
    agent.messages.append(LLMMessage(role=Role.user, content="Hello"))
    agent.stats.context_tokens = 100

    summary = await agent.compact()

    assert summary == "(no summary available)"
    assert "<summary>" not in (agent.messages[1].content or "")
