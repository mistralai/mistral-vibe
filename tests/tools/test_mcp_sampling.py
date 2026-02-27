from __future__ import annotations

from unittest.mock import MagicMock

from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    SamplingMessage,
    TextContent,
)
import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.tools.mcp_sampling import (
    MCPSamplingHandler,
    _extract_text_content,
    _map_sampling_messages,
)
from vibe.core.types import LLMMessage, Role


def _make_config(model_name: str = "test-model") -> MagicMock:
    config = MagicMock()
    model = MagicMock()
    model.name = model_name
    model.temperature = 0.7
    config.get_active_model.return_value = model
    return config


def _make_params(
    messages: list[SamplingMessage] | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 100,
) -> CreateMessageRequestParams:
    if messages is None:
        messages = [
            SamplingMessage(role="user", content=TextContent(type="text", text="Hello"))
        ]
    return CreateMessageRequestParams(
        messages=messages,
        systemPrompt=system_prompt,
        temperature=temperature,
        maxTokens=max_tokens,
    )


class TestExtractTextContent:
    def test_single_text_block(self) -> None:
        block = TextContent(type="text", text="hello")
        assert _extract_text_content(block) == "hello"

    def test_list_of_text_blocks(self) -> None:
        blocks = [
            TextContent(type="text", text="a"),
            TextContent(type="text", text="b"),
        ]
        assert _extract_text_content(blocks) == "a\nb"

    def test_unsupported_single_block(self) -> None:
        block = MagicMock(type="image", text=None)
        assert _extract_text_content(block) == ""

    def test_mixed_blocks_skips_non_text(self) -> None:
        blocks = [TextContent(type="text", text="keep"), MagicMock(type="image")]
        assert _extract_text_content(blocks) == "keep"


class TestMapSamplingMessages:
    def test_maps_user_message(self) -> None:
        msgs = [
            SamplingMessage(role="user", content=TextContent(type="text", text="hi"))
        ]
        result = _map_sampling_messages(msgs)
        assert len(result) == 1
        assert result[0].role == Role.user
        assert result[0].content == "hi"

    def test_maps_assistant_message(self) -> None:
        msgs = [
            SamplingMessage(
                role="assistant", content=TextContent(type="text", text="hello")
            )
        ]
        result = _map_sampling_messages(msgs)
        assert result[0].role == Role.assistant
        assert result[0].content == "hello"

    def test_maps_multiple_messages(self) -> None:
        msgs = [
            SamplingMessage(role="user", content=TextContent(type="text", text="q")),
            SamplingMessage(
                role="assistant", content=TextContent(type="text", text="a")
            ),
        ]
        result = _map_sampling_messages(msgs)
        assert len(result) == 2
        assert result[0].role == Role.user
        assert result[1].role == Role.assistant


class TestMCPSamplingHandler:
    @pytest.mark.asyncio
    async def test_basic_text_response(self) -> None:
        chunk = mock_llm_chunk(content="LLM says hi")
        backend = FakeBackend(chunk)
        config = _make_config("my-model")
        handler = MCPSamplingHandler(
            backend_getter=lambda: backend, config_getter=lambda: config
        )

        result = await handler(MagicMock(), _make_params())

        assert not isinstance(result, Exception)
        assert isinstance(result, CreateMessageResult)
        assert result.role == "assistant"
        assert result.content.type == "text"
        assert result.content.text == "LLM says hi"
        assert result.model == "my-model"
        assert result.stopReason == "endTurn"

    @pytest.mark.asyncio
    async def test_system_prompt_prepended(self) -> None:
        chunk = mock_llm_chunk(content="ok")
        backend = FakeBackend(chunk)
        config = _make_config()
        handler = MCPSamplingHandler(
            backend_getter=lambda: backend, config_getter=lambda: config
        )

        await handler(MagicMock(), _make_params(system_prompt="Be helpful"))

        sent_messages: list[LLMMessage] = backend.requests_messages[0]
        assert sent_messages[0].role == Role.system
        assert sent_messages[0].content == "Be helpful"
        assert sent_messages[1].role == Role.user

    @pytest.mark.asyncio
    async def test_calls_backend_with_messages(self) -> None:
        """Verify the handler forwards messages to the backend."""
        chunk = mock_llm_chunk(content="ok")
        backend = FakeBackend(chunk)
        config = _make_config()
        handler = MCPSamplingHandler(
            backend_getter=lambda: backend, config_getter=lambda: config
        )

        await handler(MagicMock(), _make_params())

        assert len(backend.requests_messages) == 1
        sent = backend.requests_messages[0]
        assert len(sent) == 1
        assert sent[0].role == Role.user
        assert sent[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_returns_error_on_backend_failure(self) -> None:
        backend = FakeBackend(exception_to_raise=RuntimeError("boom"))
        config = _make_config()
        handler = MCPSamplingHandler(
            backend_getter=lambda: backend, config_getter=lambda: config
        )

        result = await handler(MagicMock(), _make_params())

        assert isinstance(result, ErrorData)
        assert result.code == -1
        assert "boom" in result.message
