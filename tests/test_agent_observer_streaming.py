from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.llm.exceptions import BackendErrorBuilder
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
    ApprovalResponse,
    AssistantEvent,
    FunctionCall,
    LLMMessage,
    RateLimitError,
    ReasoningEvent,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)
from vibe.core.utils import CancellationReason, get_user_cancellation_message


class InjectBeforeMiddleware:
    injected_message = "<injected>"

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        "Inject a message just before the current step executes."
        return MiddlewareResult(
            action=MiddlewareAction.INJECT_MESSAGE, message=self.injected_message
        )

    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        return None


def make_config(
    *,
    enabled_tools: list[str] | None = None,
    tools: dict[str, BaseToolConfig] | None = None,
) -> VibeConfig:
    return build_test_vibe_config(
        auto_compact_threshold=0,
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
        enabled_tools=enabled_tools or [],
        tools=tools or {},
    )


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
    agent = build_test_agent_loop(
        config=make_config(), message_observer=observer, backend=backend
    )
    agent.middleware_pipeline.add(InjectBeforeMiddleware())

    async for _ in agent.act("How can you help?"):
        pass

    assert len(observed) == 3
    assert [r for r, _ in observed] == [Role.system, Role.user, Role.assistant]
    assert observed[0][1] == "You are Vibe, a super useful programming assistant."
    # injected content should be appended to the user's message before emission
    assert (
        observed[1][1]
        == f"How can you help?\n\n{InjectBeforeMiddleware.injected_message}"
    )
    assert observed[2][1] == "I can write very efficient code."


@pytest.mark.asyncio
async def test_stop_action_flushes_user_msg_before_returning(observer_capture) -> None:
    observed, observer = observer_capture

    # max_turns=0 forces an immediate STOP on the first before_turn
    backend = FakeBackend([
        mock_llm_chunk(content="My response will never reach you...")
    ])
    agent = build_test_agent_loop(
        config=make_config(), message_observer=observer, max_turns=0, backend=backend
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
    agent = build_test_agent_loop(
        config=make_config(), message_observer=observer, backend=backend
    )

    async for _ in agent.act("Ping?"):
        pass

    assert len(observed) == 3
    assert [r for r, _ in observed] == [Role.system, Role.user, Role.assistant]
    assert observed[1][1] == "Ping?"
    assert observed[2][1] == "Pong!"


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
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Stream, please.")]

    assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
    assert len(assistant_events) == 2
    assert [event.content for event in assistant_events] == [
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
        [
            mock_llm_chunk(content="Checking your todos."),
            mock_llm_chunk(content="", tool_calls=[todo_tool_call]),
        ],
        [mock_llm_chunk(content="Done reviewing todos.")],
    ])
    agent = build_test_agent_loop(
        config=make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("What about my todos?")]

    assert [type(event) for event in events] == [
        UserMessageEvent,
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
        AssistantEvent,
    ]
    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], AssistantEvent)
    assert events[1].content == "Checking your todos."
    assert isinstance(events[2], ToolCallEvent)
    assert events[2].tool_name == "todo"
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is None
    assert events[3].skipped is False
    assert isinstance(events[4], AssistantEvent)
    assert events[4].content == "Done reviewing todos."
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
        mock_llm_chunk(content=" complete"),
    ])
    agent = build_test_agent_loop(
        config=make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("Check todos with content.")]

    assert [type(event) for event in events] == [
        UserMessageEvent,
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], AssistantEvent)
    assert events[1].content == "Preparing todo request complete"
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
    agent = build_test_agent_loop(
        config=make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ALWAYS)},
        ),
        backend=backend,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        enable_streaming=True,
    )

    events = [event async for event in agent.act("Merge streamed tool call args.")]

    assert [type(event) for event in events] == [
        UserMessageEvent,
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert isinstance(events[0], UserMessageEvent)
    call_event = events[2]
    assert isinstance(call_event, ToolCallEvent)
    assert call_event.tool_call_id == "tc_merge"
    call_args = cast(TodoArgs, call_event.args)
    assert call_args.action == "read"
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is None
    assert events[3].skipped is False
    assistant_with_calls = next(
        m for m in agent.messages if m.role == Role.assistant and m.tool_calls
    )
    reconstructed_calls = assistant_with_calls.tool_calls or []
    assert len(reconstructed_calls) == 1
    assert reconstructed_calls[0].function.arguments == (
        '{"action": "read", "note": "First part"}'
    )


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
    ])
    agent = build_test_agent_loop(
        config=make_config(
            enabled_tools=["todo"],
            tools={"todo": BaseToolConfig(permission=ToolPermission.ASK)},
        ),
        backend=backend,
        agent_name=BuiltinAgentName.DEFAULT,
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
    agent.session_logger.save_interaction = AsyncMock(return_value=None)

    events = [event async for event in agent.act("Cancel mid stream?")]

    assert [type(event) for event in events] == [
        UserMessageEvent,
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
    assert agent.session_logger.save_interaction.await_count >= 1


@pytest.mark.asyncio
async def test_act_flushes_and_logs_when_streaming_errors(observer_capture) -> None:
    observed, observer = observer_capture
    backend = FakeBackend(exception_to_raise=RuntimeError("boom in streaming"))
    agent = build_test_agent_loop(
        config=make_config(),
        backend=backend,
        message_observer=observer,
        enable_streaming=True,
    )
    agent.session_logger.save_interaction = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="boom in streaming"):
        [_ async for _ in agent.act("Trigger stream failure")]

    assert [role for role, _ in observed] == [Role.system, Role.user]
    assert agent.session_logger.save_interaction.await_count == 1


@pytest.mark.asyncio
async def test_rate_limit(observer_capture) -> None:
    observed, observer = observer_capture
    response = httpx.Response(HTTPStatus.TOO_MANY_REQUESTS)
    backend_error = BackendErrorBuilder.build_http_error(
        provider="mistral",
        endpoint="test",
        response=response,
        headers=None,
        model="test-model",
        messages=[],
        temperature=0.0,
        has_tools=False,
        tool_choice=None,
    )
    backend = FakeBackend(exception_to_raise=backend_error)
    agent = build_test_agent_loop(
        config=make_config(),
        backend=backend,
        message_observer=observer,
        enable_streaming=True,
    )
    agent.session_logger.save_interaction = AsyncMock(return_value=None)

    with pytest.raises(RateLimitError):
        [_ async for _ in agent.act("Trigger rate limit failure while streaming")]

    assert [role for role, _ in observed] == [Role.system, Role.user]
    assert agent.session_logger.save_interaction.await_count == 1


def _snapshot_events(events: list) -> list[tuple[str, str]]:
    return [
        (type(e).__name__, e.content)
        for e in events
        if isinstance(e, (AssistantEvent, ReasoningEvent))
    ]


@pytest.mark.asyncio
async def test_reasoning_buffer_yields_before_content_on_transition() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Let me think"),
        mock_llm_chunk(content="", reasoning_content=" about this"),
        mock_llm_chunk(content="", reasoning_content=" problem..."),
        mock_llm_chunk(content="The answer is 42."),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("What's the answer?")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Let me think about this problem..."),
        ("AssistantEvent", "The answer is 42."),
    ]


@pytest.mark.asyncio
async def test_reasoning_buffer_yields_before_content_with_batching() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Step 1"),
        mock_llm_chunk(content="", reasoning_content=", Step 2"),
        mock_llm_chunk(content="", reasoning_content=", Step 3"),
        mock_llm_chunk(content="", reasoning_content=", Step 4"),
        mock_llm_chunk(content="", reasoning_content=", Step 5"),  # Triggers batch
        mock_llm_chunk(content="", reasoning_content=", Step 6"),
        mock_llm_chunk(content="", reasoning_content=", Final"),
        mock_llm_chunk(content="Done thinking!"),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Think step by step")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Step 1, Step 2, Step 3, Step 4, Step 5"),
        ("ReasoningEvent", ", Step 6, Final"),
        ("AssistantEvent", "Done thinking!"),
    ]


@pytest.mark.asyncio
async def test_content_buffer_yields_before_reasoning_on_transition() -> None:
    """When content is buffered and reasoning arrives, content yields first."""
    backend = FakeBackend([
        mock_llm_chunk(content="Starting the response"),
        mock_llm_chunk(content=" here..."),
        mock_llm_chunk(content="", reasoning_content="Wait, let me reconsider"),
        mock_llm_chunk(content="", reasoning_content=" this approach..."),
        mock_llm_chunk(content="Actually, the final answer."),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Give me an answer")]

    assert _snapshot_events(events) == [
        ("AssistantEvent", "Starting the response here..."),
        ("ReasoningEvent", "Wait, let me reconsider this approach..."),
        ("AssistantEvent", "Actually, the final answer."),
    ]


@pytest.mark.asyncio
async def test_interleaved_reasoning_content_preserves_order() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Think 1"),
        mock_llm_chunk(content="Answer 1 "),
        mock_llm_chunk(content="", reasoning_content="Think 2"),
        mock_llm_chunk(content="Answer 2 "),
        mock_llm_chunk(content="", reasoning_content="Think 3"),
        mock_llm_chunk(content="Answer 3"),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Interleaved test")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Think 1"),
        ("AssistantEvent", "Answer 1 "),
        ("ReasoningEvent", "Think 2"),
        ("AssistantEvent", "Answer 2 "),
        ("ReasoningEvent", "Think 3"),
        ("AssistantEvent", "Answer 3"),
    ]

    assistant_msg = next(m for m in agent.messages if m.role == Role.assistant)
    assert assistant_msg.reasoning_content == "Think 1Think 2Think 3"
    assert assistant_msg.content == "Answer 1 Answer 2 Answer 3"


@pytest.mark.asyncio
async def test_only_reasoning_chunks_yields_reasoning_event() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Just thinking..."),
        mock_llm_chunk(content="", reasoning_content=" nothing to say yet."),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Silent thinking")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Just thinking... nothing to say yet.")
    ]


@pytest.mark.asyncio
async def test_final_buffers_flush_in_correct_order() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Final thought"),
        mock_llm_chunk(content="Final words"),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("End buffers test")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Final thought"),
        ("AssistantEvent", "Final words"),
    ]


@pytest.mark.asyncio
async def test_empty_content_chunks_do_not_trigger_false_yields() -> None:
    backend = FakeBackend([
        mock_llm_chunk(content="", reasoning_content="Reasoning here"),
        mock_llm_chunk(content=""),  # Empty content shouldn't flush reasoning
        mock_llm_chunk(content="", reasoning_content=" more reasoning"),
        mock_llm_chunk(content="Actual content"),
    ])
    agent = build_test_agent_loop(
        config=make_config(), backend=backend, enable_streaming=True
    )

    events = [event async for event in agent.act("Empty content test")]

    assert _snapshot_events(events) == [
        ("ReasoningEvent", "Reasoning here more reasoning"),
        ("AssistantEvent", "Actual content"),
    ]
