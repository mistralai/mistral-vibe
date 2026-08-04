from __future__ import annotations

from typing import cast

import pytest

from tests.conftest import build_test_vibe_config, make_test_models
from tests.mock.utils import mock_llm_chunk
from vibe.core.compaction import CompactionFailedError, CompactionManager
from vibe.core.compaction.context import parse_previous_user_messages
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.types import (
    AgentStats,
    ContextTooLongError,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    MessageList,
    Role,
    ToolCall,
)


class _FakeComplete:
    """Stand-in for AgentLoop._complete: records calls, scripts results, and
    accounts usage into stats (as the real accounted path does).
    """

    def __init__(self, results: list[LLMChunk | Exception], stats: AgentStats) -> None:
        self._results = list(results)
        self._stats = stats
        self.calls: list[dict] = []

    async def __call__(self, *, model, messages, tools, tool_choice, call_type):
        self.calls.append({
            "model": model,
            "messages": list(messages),
            "tools": tools,
            "tool_choice": tool_choice,
            "call_type": call_type,
        })
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.usage is not None:
            self._stats.session_prompt_tokens += item.usage.prompt_tokens
            self._stats.session_completion_tokens += item.usage.completion_tokens
        return item


class _FakeTelemetry:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def send_compaction_failed(
        self, *, reason, session_id=None, parent_session_id=None
    ):
        self.failures.append(reason)


async def _noop() -> None:
    return None


def _tool_call_chunk() -> LLMChunk:
    return mock_llm_chunk(
        content="",
        tool_calls=[
            ToolCall(
                id="t1", index=0, function=FunctionCall(name="bash", arguments="{}")
            )
        ],
    )


def _build_manager(
    results: list[LLMChunk | Exception],
    *,
    messages: MessageList,
    stats: AgentStats,
    raise_on_failure: bool = False,
) -> tuple[CompactionManager, _FakeComplete, _FakeTelemetry]:
    cfg = build_test_vibe_config(
        models=make_test_models(auto_compact_threshold=999),
        raise_on_compaction_failure=raise_on_failure,
    )
    complete = _FakeComplete(results, stats)
    telemetry = _FakeTelemetry()
    manager = CompactionManager(
        messages=messages,
        stats_getter=lambda: stats,
        config_getter=lambda: cfg,
        complete=complete,
        available_tools=lambda: [],
        tool_choice=lambda: "auto",
        save=_noop,
        reset_session=_noop,
        telemetry_client=cast(TelemetryClient, telemetry),
        session_ids=lambda: ("sid", None),
    )
    return manager, complete, telemetry


def _conversation() -> MessageList:
    return MessageList([
        LLMMessage(role=Role.system, content="sys"),
        LLMMessage(role=Role.user, content="oldest ask"),
        LLMMessage(role=Role.assistant, content="work"),
        LLMMessage(role=Role.user, content="newest ask"),
    ])


@pytest.mark.asyncio
async def test_primary_success_resets_to_envelope() -> None:
    messages = _conversation()
    stats = AgentStats()
    manager, complete, telemetry = _build_manager(
        [mock_llm_chunk(content="<summary>all done</summary>")],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "all done"
    assert len(complete.calls) == 1  # no fallback
    assert not telemetry.failures
    assert [m.role for m in messages] == [Role.system, Role.user]
    assert "all done" in (messages[1].content or "")
    assert parse_previous_user_messages(messages[1].content or "") == [
        "oldest ask",
        "newest ask",
    ]
    assert stats.context_tokens == 0


@pytest.mark.asyncio
async def test_fallback_usage_is_accounted() -> None:
    # Regression: the fallback must go through the accounted `complete` path so
    # its tokens land in session stats (the old direct backend.complete bug).
    messages = _conversation()
    stats = AgentStats()
    manager, complete, telemetry = _build_manager(
        [_tool_call_chunk(), mock_llm_chunk(content="<summary>recovered</summary>")],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "recovered"
    assert len(complete.calls) == 2  # primary + fallback both via `complete`
    assert complete.calls[1]["tools"] is None  # fallback disables tools
    # Both calls' usage is accounted (10 prompt + 5 completion each).
    assert stats.session_prompt_tokens == 20
    assert stats.session_completion_tokens == 10
    assert not telemetry.failures  # fallback recovered → not a failure


@pytest.mark.asyncio
async def test_fallback_transcript_keeps_tool_calls() -> None:
    # The fallback transcript must include the assistant's tool calls (its
    # actions), not just the tool results, so trajectory context survives.
    messages = MessageList([
        LLMMessage(role=Role.system, content="sys"),
        LLMMessage(role=Role.user, content="do it"),
        LLMMessage(
            role=Role.assistant,
            content="",
            tool_calls=[
                ToolCall(
                    id="t1",
                    index=0,
                    function=FunctionCall(name="run_tests", arguments='{"path": "x"}'),
                )
            ],
        ),
        LLMMessage(role=Role.tool, content="3 failures", tool_call_id="t1"),
        LLMMessage(role=Role.user, content="fix them"),
    ])
    stats = AgentStats()
    manager, complete, _ = _build_manager(
        [_tool_call_chunk(), mock_llm_chunk(content="<summary>done</summary>")],
        messages=messages,
        stats=stats,
    )

    await manager.compact()

    fallback_prompt = complete.calls[1]["messages"][1].content or ""
    assert "run_tests" in fallback_prompt  # assistant's tool call is preserved
    assert "3 failures" in fallback_prompt  # tool result is preserved


@pytest.mark.asyncio
async def test_overflow_trims_whole_rounds_then_succeeds() -> None:
    messages = _conversation()
    stats = AgentStats()
    manager, complete, _ = _build_manager(
        [
            ContextTooLongError("p", "m"),
            mock_llm_chunk(content="<summary>fit now</summary>"),
        ],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "fit now"
    # Second attempt sent fewer messages than the first (a round was dropped).
    assert len(complete.calls[1]["messages"]) < len(complete.calls[0]["messages"])
    # Prior user messages are preserved from the pre-trim snapshot.
    assert parse_previous_user_messages(messages[1].content or "") == [
        "oldest ask",
        "newest ask",
    ]


@pytest.mark.asyncio
async def test_fallback_trims_on_overflow_then_succeeds() -> None:
    # The fallback must also recover from ContextTooLongError by dropping the
    # oldest round and retrying (same overflow handling as the primary).
    messages = _conversation()
    stats = AgentStats()
    manager, complete, telemetry = _build_manager(
        [
            _tool_call_chunk(),  # primary fails (tool call)
            ContextTooLongError("p", "m"),  # fallback overflows once
            mock_llm_chunk(content="<summary>fit now</summary>"),  # fallback retries
        ],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "fit now"
    assert len(complete.calls) == 3  # primary + fallback (overflow) + fallback (retry)
    assert not telemetry.failures
    # The fallback embeds the transcript in one user message, so the retry's
    # transcript is shorter than its first attempt (a round was dropped).
    first_transcript = complete.calls[1]["messages"][1].content or ""
    retry_transcript = complete.calls[2]["messages"][1].content or ""
    assert len(retry_transcript) < len(first_transcript)


@pytest.mark.asyncio
async def test_compaction_does_not_consume_turn_budget() -> None:
    # Compaction (primary + fallback + overflow retries) must not bump
    # stats.steps — otherwise overflow recovery could trip the turn limit and
    # abort the very turn the reactive compaction is trying to save.
    messages = _conversation()
    stats = AgentStats()
    steps_before = stats.steps
    manager, _, _ = _build_manager(
        [
            ContextTooLongError("p", "m"),  # primary overflows once
            _tool_call_chunk(),  # primary fails (tool call)
            mock_llm_chunk(content="<summary>done</summary>"),  # fallback succeeds
        ],
        messages=messages,
        stats=stats,
    )

    await manager.compact()

    assert stats.steps == steps_before


@pytest.mark.asyncio
async def test_live_messages_untouched_on_failure() -> None:
    messages = _conversation()
    original = list(messages)
    stats = AgentStats()
    manager, _, _ = _build_manager(
        [RuntimeError("boom")], messages=messages, stats=stats
    )

    with pytest.raises(RuntimeError, match="boom"):
        await manager.compact()

    # Snapshot-based generation must never mutate the live list on failure.
    assert list(messages) == original


@pytest.mark.asyncio
async def test_strict_mode_raises_on_primary_failure() -> None:
    messages = _conversation()
    original = list(messages)
    stats = AgentStats()
    manager, complete, telemetry = _build_manager(
        [_tool_call_chunk()], messages=messages, stats=stats, raise_on_failure=True
    )

    with pytest.raises(CompactionFailedError) as exc_info:
        await manager.compact()

    assert exc_info.value.reason == "tool_call"
    assert telemetry.failures == ["tool_call"]
    assert len(complete.calls) == 1  # strict: no fallback
    assert list(messages) == original


@pytest.mark.asyncio
async def test_terminal_failure_reports_primary_reason() -> None:
    # Primary tool-calls, fallback also fails: the terminal telemetry keeps the
    # primary's reason (tool_call), not the fallback's always-empty reason.
    messages = _conversation()
    stats = AgentStats()
    manager, complete, telemetry = _build_manager(
        [_tool_call_chunk(), mock_llm_chunk(content="no tags here")],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "(no summary available)"
    assert telemetry.failures == ["tool_call"]  # fires once, primary reason kept
    assert len(complete.calls) == 2
    assert "(no summary available)" in (messages[1].content or "")


@pytest.mark.asyncio
async def test_terminal_failure_reports_empty_when_primary_empty() -> None:
    messages = _conversation()
    stats = AgentStats()
    manager, _, telemetry = _build_manager(
        [mock_llm_chunk(content="no tags"), mock_llm_chunk(content="still none")],
        messages=messages,
        stats=stats,
    )

    summary = await manager.compact()

    assert summary == "(no summary available)"
    assert telemetry.failures == ["empty_summary"]
