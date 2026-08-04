from __future__ import annotations

from typing import Any

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop
from tests.agent_loop.e2e.providers import assistant_text
from tests.backend.data.mistral import (
    STREAMED_SIMPLE_CONVERSATION_PARAMS,
    mistral_completion,
)
from tests.conftest import build_test_vibe_config
from vibe.core.types import (
    AssistantEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)

TODO_TOOL_CALL = [
    {
        "id": "call_todo_1",
        "function": {"name": "todo", "arguments": '{"action": "read"}'},
        "index": 0,
    }
]


def _chunked_completion(reasoning: str, answer: str) -> dict[str, Any]:
    completion = mistral_completion(answer)
    completion["choices"][0]["message"]["content"] = [
        {"type": "thinking", "thinking": [{"type": "text", "text": reasoning}]},
        {"type": "text", "text": answer},
    ]
    return completion


@pytest.mark.asyncio
async def test_act_completes_against_real_backend(mistral_api: MistralAPI) -> None:
    # A plain prompt yields a user event then the backend's assistant reply, and
    # the loop accumulates prompt+completion tokens from the usage block.
    mistral_api.reply(
        mistral_completion("Hi there!", prompt_tokens=120, completion_tokens=30)
    )
    agent = build_e2e_agent_loop()

    events = [event async for event in agent.act("Hello")]

    assert [type(e) for e in events] == [UserMessageEvent, AssistantEvent]
    assert isinstance(events[1], AssistantEvent)
    assert events[1].content == "Hi there!"
    assert agent.stats.context_tokens == 150


@pytest.mark.asyncio
async def test_act_streaming(mistral_api: MistralAPI) -> None:
    # Streamed SSE chunks are reassembled into the final assistant content.
    _, chunks, _ = STREAMED_SIMPLE_CONVERSATION_PARAMS[0]
    mistral_api.reply_stream(chunks)
    agent = build_e2e_agent_loop(enable_streaming=True)

    events = [event async for event in agent.act("Hi")]

    assert assistant_text(events).endswith("Some content")


@pytest.mark.asyncio
async def test_act_tool_call_round_trip(mistral_api: MistralAPI) -> None:
    # A tool call is parsed, executed, and its result fed back for a final reply.
    mistral_api.reply(
        mistral_completion("", tool_calls=TODO_TOOL_CALL),
        mistral_completion("All done"),
    )
    agent = build_e2e_agent_loop(config=build_test_vibe_config(enabled_tools=["todo"]))

    events = [event async for event in agent.act("Show my todos")]

    assert any(isinstance(e, ToolCallEvent) for e in events)
    assert any(isinstance(e, ToolResultEvent) for e in events)
    final = [e for e in events if isinstance(e, AssistantEvent)]
    assert final and final[-1].content == "All done"


@pytest.mark.asyncio
async def test_act_serializes_tools_in_request_payload(mistral_api: MistralAPI) -> None:
    # Enabled tools are serialized into the outgoing chat-completions request.
    mistral_api.reply(mistral_completion("ok"))
    agent = build_e2e_agent_loop(config=build_test_vibe_config(enabled_tools=["todo"]))

    _ = [event async for event in agent.act("Hello")]

    names = {t["function"]["name"] for t in mistral_api.request_json.get("tools", [])}
    assert "todo" in names


@pytest.mark.asyncio
async def test_act_extracts_text_from_chunked_content_array(
    mistral_api: MistralAPI,
) -> None:
    # A thinking+text content array has its text part extracted as the reply.
    mistral_api.reply(_chunked_completion("thinking hard", "the answer"))
    agent = build_e2e_agent_loop()

    events = [event async for event in agent.act("Why?")]

    assistant = [e for e in events if isinstance(e, AssistantEvent)]
    assert assistant and assistant[-1].content == "the answer"
