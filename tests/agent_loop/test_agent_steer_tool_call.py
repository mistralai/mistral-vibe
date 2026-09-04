from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_interaction_requests import ApprovalRequestHandler
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import ToolPermission
from vibe.core.types import (
    ApprovalRequestEvent,
    ApprovalResponse,
    BaseEvent,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
    UserMessageEvent,
)

_STEER_TEXT = "sorry i meant the python runtime inside it"


def _assert_tool_calls_immediately_paired(messages: list[LLMMessage]) -> None:
    # Anthropic requires every assistant `tool_use` to be followed immediately by
    # the matching `tool_result`, with no other message in between.
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == Role.assistant and message.tool_calls:
            pending = {tc.id for tc in message.tool_calls if tc.id is not None}
            cursor = index + 1
            while pending and cursor < len(messages):
                following = messages[cursor]
                assert following.role == Role.tool, (
                    f"message #{cursor} ({following.role}) landed between a "
                    f"tool_use and its tool_result: {following.content!r}"
                )
                if following.tool_call_id is not None:
                    pending.discard(following.tool_call_id)
                cursor += 1
            assert not pending, f"tool_use without a tool_result: {pending}"
        index += 1


def _make_steering_loop(backend: FakeBackend) -> AgentLoop:
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    return build_test_agent_loop(
        config=config, agent_name=BuiltinAgentName.ASK, backend=backend
    )


@pytest.mark.asyncio
async def test_steer_during_tool_call_keeps_backend_payload_tool_paired() -> None:
    tool_call = ToolCall(
        id="call_1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action": "read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="Let me check your todos.", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done.")],
    ])
    agent_loop = _make_steering_loop(backend)

    injected_events: list[BaseEvent] = []

    async def steer_then_approve(
        _tool_name: str,
        _tool_args: object,
        _tool_call_id: str,
        _required_permissions: object,
    ) -> tuple[ApprovalResponse, None]:
        # Steer while the tool call is in flight: the assistant `tool_use` is
        # already in history but its `tool_result` has not been recorded yet.
        injected_events.extend(
            await agent_loop.inject_user_context(_STEER_TEXT, as_message=True)
        )
        return ApprovalResponse.YES, None

    handler: ApprovalRequestHandler = steer_then_approve

    async for event in agent_loop.act("please check the archi of vibe_sdk"):
        if isinstance(event, ApprovalRequestEvent):
            response, feedback = await handler(
                event.tool_name,
                event.tool_args,
                event.tool_call_id,
                event.required_permissions,
            )
            agent_loop.resolve_approval_request(event.request_id, response, feedback)

    # Every request that reached the backend must satisfy tool_use/tool_result
    # adjacency -- this is exactly what a real provider would reject otherwise.
    assert backend.requests_messages, "no backend request was made"
    for request in backend.requests_messages:
        _assert_tool_calls_immediately_paired(request)

    # The steered follow-up must reach the model, after the tool result, on the
    # second call.
    final_request = backend.requests_messages[-1]
    steered = [
        m for m in final_request if m.role == Role.user and m.content == _STEER_TEXT
    ]
    assert len(steered) == 1
    steered_index = final_request.index(steered[0])
    last_tool_index = max(i for i, m in enumerate(final_request) if m.role == Role.tool)
    assert steered_index > last_tool_index

    # The UI still learns about the steered message immediately.
    assert any(
        isinstance(e, UserMessageEvent) and e.content == _STEER_TEXT
        for e in injected_events
    )
