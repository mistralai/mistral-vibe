from __future__ import annotations

import pytest

from tests.agent_loop.e2e.conftest import (
    ProviderAPI,
    anthropic_e2e_config,
    assistant_text,
    build_e2e_agent_loop,
    openai_responses_e2e_config,
)
from tests.backend.data.anthropic import (
    anthropic_message,
    anthropic_reasoning_tool_use_stream,
    anthropic_request_content_blocks,
    anthropic_text_stream,
    anthropic_tool_use,
)
from tests.backend.data.openai_responses import (
    openai_function_call_item,
    openai_message_item,
    openai_reasoning_tool_call_stream,
    openai_response,
    openai_text_stream,
)
from vibe.core.types import ToolResultEvent


class TestAnthropic:
    @pytest.mark.asyncio
    async def test_agent_answers(self, anthropic_api: ProviderAPI) -> None:
        # A plain prompt is answered through the Anthropic messages wire.
        anthropic_api.reply(anthropic_message("pong"))
        agent = build_e2e_agent_loop(config=anthropic_e2e_config())

        events = [event async for event in agent.act("Reply with exactly: pong")]

        assert assistant_text(events) == "pong"
        assert agent.stats.context_tokens == 15

    @pytest.mark.asyncio
    async def test_agent_streams(self, anthropic_api: ProviderAPI) -> None:
        # Anthropic SSE deltas are reassembled into the final assistant content.
        anthropic_api.reply_stream(anthropic_text_stream("pong"))
        agent = build_e2e_agent_loop(
            config=anthropic_e2e_config(), enable_streaming=True
        )

        events = [event async for event in agent.act("Reply with exactly: pong")]

        assert assistant_text(events) == "pong"

    @pytest.mark.asyncio
    async def test_agent_executes_tool_call(self, anthropic_api: ProviderAPI) -> None:
        # A tool_use turn runs the tool, then Anthropic returns the final answer.
        anthropic_api.reply(
            anthropic_tool_use("todo", {"action": "read"}),
            anthropic_message("Your list is empty."),
        )
        agent = build_e2e_agent_loop(
            config=anthropic_e2e_config(enabled_tools=["todo"])
        )

        events = [event async for event in agent.act("What's on my todo list?")]

        assert any(isinstance(e, ToolResultEvent) for e in events)
        assert "Your list is empty." in assistant_text(events)

    @pytest.mark.asyncio
    async def test_agent_streams_reasoning_and_tool_call(
        self, anthropic_api: ProviderAPI
    ) -> None:
        # A streamed thinking + tool_use turn runs the tool, then replays that
        # reasoning/tool history into the follow-up Anthropic request.
        anthropic_api.reply_streams(
            anthropic_reasoning_tool_use_stream("todo", '{"action": "read"}'),
            anthropic_text_stream("Your list is empty."),
        )
        agent = build_e2e_agent_loop(
            config=anthropic_e2e_config(enabled_tools=["todo"]), enable_streaming=True
        )

        events = [event async for event in agent.act("What's on my todo list?")]

        assert "Your list is empty." in assistant_text(events)
        blocks = anthropic_request_content_blocks(anthropic_api.request_json)
        thinking = [b["thinking"] for b in blocks if b.get("type") == "thinking"]
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]

        assert any("thinking..." in text for text in thinking)
        assert tool_uses


class TestOpenAIResponses:
    @pytest.mark.asyncio
    async def test_agent_answers(self, openai_responses_api: ProviderAPI) -> None:
        # A plain prompt is answered through the OpenAI Responses wire.
        openai_responses_api.reply(openai_response([openai_message_item("pong")]))
        agent = build_e2e_agent_loop(config=openai_responses_e2e_config())

        events = [event async for event in agent.act("Reply with exactly: pong")]

        assert assistant_text(events) == "pong"
        assert agent.stats.context_tokens == 12

    @pytest.mark.asyncio
    async def test_agent_streams(self, openai_responses_api: ProviderAPI) -> None:
        # OpenAI Responses SSE deltas are reassembled into the final content.
        openai_responses_api.reply_stream(openai_text_stream("pong"))
        agent = build_e2e_agent_loop(
            config=openai_responses_e2e_config(), enable_streaming=True
        )

        events = [event async for event in agent.act("Reply with exactly: pong")]

        assert assistant_text(events) == "pong"

    @pytest.mark.asyncio
    async def test_agent_captures_reasoning(
        self, openai_responses_api: ProviderAPI
    ) -> None:
        # Commentary phase output is captured as reasoning on the assistant message.
        openai_responses_api.reply(
            openai_response(
                [
                    openai_message_item("Let me think.", phase="commentary"),
                    openai_message_item("pong", phase="final_answer"),
                ],
                output_tokens=5,
            )
        )
        agent = build_e2e_agent_loop(config=openai_responses_e2e_config())

        events = [event async for event in agent.act("Reply with exactly: pong")]

        assert assistant_text(events) == "pong"
        assert any(
            m.reasoning_content and "Let me think." in m.reasoning_content
            for m in agent.messages
        )

    @pytest.mark.asyncio
    async def test_agent_executes_tool_call(
        self, openai_responses_api: ProviderAPI
    ) -> None:
        # A function_call item runs the tool, then OpenAI returns the final answer.
        openai_responses_api.reply(
            openai_response([openai_function_call_item("todo", '{"action": "read"}')]),
            openai_response([openai_message_item("Your list is empty.")]),
        )
        agent = build_e2e_agent_loop(
            config=openai_responses_e2e_config(enabled_tools=["todo"])
        )

        events = [event async for event in agent.act("What's on my todo list?")]

        assert any(isinstance(e, ToolResultEvent) for e in events)
        assert "Your list is empty." in assistant_text(events)

    @pytest.mark.asyncio
    async def test_agent_streams_reasoning_and_tool_call(
        self, openai_responses_api: ProviderAPI
    ) -> None:
        # A streamed commentary + function_call turn runs the tool, then OpenAI
        # streams the final answer on the follow-up request.
        openai_responses_api.reply_streams(
            openai_reasoning_tool_call_stream("todo", '{"action": "read"}'),
            openai_text_stream("Your list is empty."),
        )
        agent = build_e2e_agent_loop(
            config=openai_responses_e2e_config(enabled_tools=["todo"]),
            enable_streaming=True,
        )

        events = [event async for event in agent.act("What's on my todo list?")]

        assert any(isinstance(e, ToolResultEvent) for e in events)
        assert "Your list is empty." in assistant_text(events)
