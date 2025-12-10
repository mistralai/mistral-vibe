from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.llm.types import BackendLike
from vibe.core.middleware import (
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    MiddlewareResult,
    ResetReason,
)
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.tools.builtins.todo import TodoArgs
from vibe.core.types import (
    AssistantEvent,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)
from vibe.core.utils import (
    ApprovalResponse,
    CancellationReason,
    get_user_cancellation_message,
)


class InjectBeforeMiddleware:
    injectedMessage = "<injected>"

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        "Inject a message just before the current step executes."
        return MiddlewareResult(
            action=MiddlewareAction.INJECT_MESSAGE, message=self.injectedMessage
        )

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        return None


def make_config(
    *,
    disable_logging: bool = True,
    enabled_tools: list[str] | None = None,
    tools: dict[str, BaseToolConfig] | None = None,
) -> VibeConfig:
    cfg = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=not disable_logging),
        auto_compact_threshold=0,
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
        enabled_tools=enabled_tools or [],
        tools=tools or {},
    )
    return cfg


@pytest.fixture
def observer_capture() -> tuple[
    list[tuple[Role, str | None]], Callable[[LLMMessage], None]
]:
    observed: list[tuple[Role, str | None]] = []

    def observer(msg: LLMMessage) -> None:
        observed.append((msg.role, msg.content))

    return observed, observer


@pytest.mark.asyncio
async def test_act_flushes_batched_messages_with_injection_middleware(
    observer_capture,
) -> None:
    observed, observer = observer_capture

    backend = FakeBackend([mock_llm_chunk(content="I can write very efficient code.")])
    agent = Agent(make_config(), message_observer=observer, backend=backend)
    agent.middleware_pipeline.add(InjectBeforeMiddleware())

    async for _ in agent.act("How can you help?"):
        pass

    assert len(observed) == 3
    assert [r for r, _ in observed] == [Role.system, Role.user, Role.assistant]
    assert observed[0][1] == "You are Vibe, a super useful programming assistant."
    # injected content should be appended to the user's message before emission
    assert (
        observed[1][1]
        == f"How can you help?\n\n{InjectBeforeMiddleware.injectedMessage}"
    )
    assert observed[2][1] == "I can write very efficient code."


@pytest.mark.asyncio
async def test_stop_action_flushes_user_msg_before_returning(observer_capture) -> None:
    observed, observer = observer_capture

    # max_turns=0 forces an immediate STOP on the first before_turn
    backend = FakeBackend([
        mock_llm_chunk(content="My response will never reach you...")
    ])
    agent = Agent(
        make_config(), message_observer=observer, max_turns=0, backend=backend
    )

    async for _ in agent.act("Greet."):
        pass

    assert len(observed) == 2
    # user's message should have been flushed before returning
    assert [r for r, _ in observed] == [Role.system, Role.user]
    assert observed[0][1] == "You are Vibe, a super useful programming assistant."
    assert observed[1][1] == "Greet."


@pytest.mark.asyncio
async def test_act_emits_user_and_assistant_msgs(observer_capture) -> None:
    observed, observer = observer_capture

    backend = FakeBackend([mock_llm_chunk(content="Pong!")])
    agent = Agent(make_config(), message_observer=observer, backend=backend)

    async for _ in agent.act("Ping?"):
        pass

    assert len(observed) == 3
    assert [r for r, _ in observed] == [Role.system, Role.user, Role.assistant]
    assert observed[1][1] == "Ping?"
    assert observed[2][1] == "Pong!"


@pytest.mark.asyncio
async def test_act_yields_assistant_event_with_usage_stats() -> None:
    backend = FakeBackend([mock_llm_chunk(content="Pong!")])
    agent = Agent(make_config(), backend=backend)

    events = [ev async for ev in agent.act("Ping?")]

    assert len(events) == 1
    ev = events[-1]
    assert isinstance(ev, AssistantEvent)
    assert ev.content == "Pong!"
    # stats come from tests.mock.utils.mock_llm_result (prompt=10, completion=5)
    assert ev.prompt_tokens == 10
    assert ev.completion_tokens == 5
    assert ev.session_total_tokens == 15


@pytest.mark.asyncio
async def test_act_streams_batched_chunks_in_order() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="Hello"),
        mock_llm_chunk(content=" from"),
        mock_llm_chunk(content=" Vibe"),
        mock_llm_chunk(content="! "),
        mock_llm_chunk(content="More"),
        mock_llm_chunk(content=" and"),
        mock_llm_chunk(content=" end"),
    ])
    agent = Agent(make_config(), backend=backend, enable_streaming=True)

    events = [event async for event in agent.act("Stream, please.")]

    assert len(events) == 2
    assert [event.content for event in events if isinstance(event, AssistantEvent)] == [
        "Hello from Vibe! More",
        " and end",
    ]
    assert agent.messages[-1].role == Role.assistant
    assert agent.messages[-1].content == "Hello from Vibe! More and end"


@pytest.mark.asyncio
async def test_act_handles_streaming_with_tool_call_events_in_sequence() -> None:
    todo_tool_call = ToolCall(
        id="tc_stream",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )
    backend = FakeBackend([
        mock_llm_chunk(content="Checking your todos."),
        mock_llm_chunk(content="", tool_calls=[todo_tool_call]),
        mock_llm_chunk(content="", finish_reason="stop"),
        mock_llm_chunk(content="Done reviewing todos."),
    ])
    agent = Agent(
        make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        auto_approve=True,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("What about my todos?")]

    assert [type(event) for event in events] == [
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
        AssistantEvent,
    ]
    assert isinstance(events[0], AssistantEvent)
    assert events[0].content == "Checking your todos."
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].tool_name == "todo"
    assert isinstance(events[2], ToolResultEvent)
    assert events[2].error is None
    assert events[2].skipped is False
    assert isinstance(events[3], AssistantEvent)
    assert events[3].content == "Done reviewing todos."
    assert agent.messages[-1].content == "Done reviewing todos."


@pytest.mark.asyncio
async def test_act_handles_tool_call_chunk_with_content() -> None:
    todo_tool_call = ToolCall(
        id="tc_content",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )
    backend = FakeBackend([
        mock_llm_chunk(content="Preparing "),
        mock_llm_chunk(content="todo request", tool_calls=[todo_tool_call]),
        mock_llm_chunk(content=" complete", finish_reason="stop"),
    ])
    agent = Agent(
        make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        auto_approve=True,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("Check todos with content.")]

    assert [type(event) for event in events] == [
        AssistantEvent,
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert isinstance(events[0], AssistantEvent)
    assert events[0].content == "Preparing todo request"
    assert isinstance(events[1], AssistantEvent)
    assert events[1].content == " complete"
    assert any(
        m.role == Role.assistant and m.content == "Preparing todo request complete"
        for m in agent.messages
    )


@pytest.mark.asyncio
async def test_act_merges_streamed_tool_call_arguments() -> None:
    tool_call_part_one = ToolCall(
        id="tc_merge",
        index=0,
        function=FunctionCall(
            name="todo", arguments='{"action": "read", "note": "First '
        ),
    )
    tool_call_part_two = ToolCall(
        id="tc_merge", index=0, function=FunctionCall(name="todo", arguments='part"}')
    )
    backend = FakeBackend([
        mock_llm_chunk(content="Planning: "),
        mock_llm_chunk(content="", tool_calls=[tool_call_part_one]),
        mock_llm_chunk(content="", tool_calls=[tool_call_part_two]),
    ])
    agent = Agent(
        make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        auto_approve=True,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("Merge streamed tool call args.")]

    assert [type(event) for event in events] == [
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    call_event = events[1]
    assert isinstance(call_event, ToolCallEvent)
    assert call_event.tool_call_id == "tc_merge"
    call_args = cast(TodoArgs, call_event.args)
    assert call_args.action == "read"
    assert isinstance(events[2], ToolResultEvent)
    assert events[2].error is None
    assert events[2].skipped is False
    assistant_with_calls = next(
        m for m in agent.messages if m.role == Role.assistant and m.tool_calls
    )
    reconstructed_calls = assistant_with_calls.tool_calls or []
    assert len(reconstructed_calls) == 1
    assert reconstructed_calls[0].function.arguments == (
        '{"action": "read", "note": "First part"}'
    )


@pytest.mark.asyncio
async def test_act_raises_when_stream_never_signals_finish() -> None:
    class IncompleteStreamingBackend(BackendLike):
        def __init__(self, chunks: list[LLMChunk]) -> None:
            self._chunks = list(chunks)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def complete_streaming(self, **_: object):
            while self._chunks:
                yield self._chunks.pop(0)

        async def complete(self, **_: object):
            return mock_llm_chunk(content="", finish_reason="stop")

        async def count_tokens(self, **_: object) -> int:
            return 0

    backend = IncompleteStreamingBackend([mock_llm_chunk(content="partial")])
    agent = Agent(make_config(), backend=backend, enable_streaming=True)

    with pytest.raises(RuntimeError, match="Streamed completion returned no chunks"):
        [event async for event in agent.act("Will this finish?")]


@pytest.mark.asyncio
async def test_act_handles_user_cancellation_during_streaming() -> None:
    class CountingMiddleware(MiddlewarePipeline):
        def __init__(self) -> None:
            self.before_calls = 0
            self.after_calls = 0

        async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
            self.before_calls += 1
            return MiddlewareResult()

        async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
            self.after_calls += 1
            return MiddlewareResult()

        def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
            return None

    todo_tool_call = ToolCall(
        id="tc_cancel",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )
    backend = FakeBackend([
        mock_llm_chunk(content="Preparing "),
        mock_llm_chunk(content="todo request", tool_calls=[todo_tool_call]),
        mock_llm_chunk(content="", finish_reason="stop"),
    ])
    agent = Agent(
        make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ASK)},
        ),
        backend=backend,
        auto_approve=False,
        enable_streaming=True,
    )
    middleware = CountingMiddleware()
    agent.middleware_pipeline.add(middleware)
    agent.set_approval_callback(
        lambda _name, _args, _id: (
            ApprovalResponse.NO,
            str(get_user_cancellation_message(CancellationReason.OPERATION_CANCELLED)),
        )
    )
    agent.interaction_logger.save_interaction = AsyncMock(return_value=None)

    events = [event async for event in agent.act("Cancel mid stream?")]

    assert [type(event) for event in events] == [
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert middleware.before_calls == 1
    assert middleware.after_calls == 0
    assert isinstance(events[-1], ToolResultEvent)
    assert events[-1].skipped is True
    assert events[-1].skip_reason is not None
    assert "<user_cancellation>" in events[-1].skip_reason
    assert agent.interaction_logger.save_interaction.await_count == 2


@pytest.mark.asyncio
async def test_act_flushes_and_logs_when_streaming_errors(observer_capture) -> None:
    observed, observer = observer_capture
    backend = FakeBackend(exception_to_raise=RuntimeError("boom in streaming"))
    agent = Agent(
        make_config(), backend=backend, message_observer=observer, enable_streaming=True
    )
    agent.interaction_logger.save_interaction = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="boom in streaming"):
        [_ async for _ in agent.act("Trigger stream failure")]

    assert [role for role, _ in observed] == [Role.system, Role.user]
    assert agent.interaction_logger.save_interaction.await_count == 1
