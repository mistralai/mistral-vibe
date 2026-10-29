from __future__ import annotations

import json

import httpx
from mistralai.client.models import (
    AssistantMessage,
    ContentChunk,
    TextChunk,
    ThinkChunk,
)
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.constants import CHAT_COMPLETIONS_PATH
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.llm.backend.generic import GenericBackend, OpenAIAdapter
from vibe.core.llm.backend.mistral import MistralBackend, MistralMapper, ParsedContent
from vibe.core.types import LLMMessage, Role
from vibe.user_content import UserDisplayContent


def make_config() -> VibeConfigSchema:
    return build_test_vibe_config(
        include_commit_signature=False, enabled_tools=[], tools={}
    )


class TestMistralMapperParseContent:
    def test_parse_content_string_returns_content_only(self):
        mapper = MistralMapper()
        result = mapper.parse_content("Hello, world!")

        assert result == ParsedContent(content="Hello, world!", reasoning_content=None)

    def test_parse_content_text_chunk_returns_content_only(self):
        mapper = MistralMapper()
        content: list[ContentChunk] = [
            TextChunk(type="text", text="Hello from text chunk")
        ]

        result = mapper.parse_content(content)

        assert result == ParsedContent(
            content="Hello from text chunk", reasoning_content=None
        )

    def test_parse_content_thinking_chunk_extracts_reasoning(self):
        mapper = MistralMapper()
        content: list[ContentChunk] = [
            ThinkChunk(
                type="thinking",
                thinking=[TextChunk(type="text", text="Let me think...")],
            ),
            TextChunk(type="text", text="The answer is 42."),
        ]

        result = mapper.parse_content(content)

        assert result == ParsedContent(
            content="The answer is 42.", reasoning_content="Let me think..."
        )

    def test_parse_content_multiple_thinking_chunks_concatenates(self):
        mapper = MistralMapper()
        content: list[ContentChunk] = [
            ThinkChunk(
                type="thinking",
                thinking=[TextChunk(type="text", text="First thought. ")],
            ),
            ThinkChunk(
                type="thinking",
                thinking=[TextChunk(type="text", text="Second thought.")],
            ),
            TextChunk(type="text", text="Final answer."),
        ]

        result = mapper.parse_content(content)

        assert result == ParsedContent(
            content="Final answer.", reasoning_content="First thought. Second thought."
        )

    def test_parse_content_thinking_only_returns_empty_content(self):
        mapper = MistralMapper()
        content: list[ContentChunk] = [
            ThinkChunk(
                type="thinking",
                thinking=[TextChunk(type="text", text="Just thinking...")],
            )
        ]

        result = mapper.parse_content(content)

        assert result == ParsedContent(content="", reasoning_content="Just thinking...")

    def test_parse_content_empty_list_returns_empty(self):
        mapper = MistralMapper()
        content: list[ContentChunk] = []

        result = mapper.parse_content(content)

        assert result == ParsedContent(content="", reasoning_content=None)


class TestMistralMapperPrepareMessage:
    def test_prepare_assistant_message_without_reasoning(self):
        mapper = MistralMapper()
        msg = LLMMessage(role=Role.assistant, content="Hello!")

        result = mapper.prepare_message(msg)

        assert isinstance(result, AssistantMessage)
        assert result.content == "Hello!"

    def test_prepare_assistant_message_with_reasoning_creates_chunks(self):
        mapper = MistralMapper()
        msg = LLMMessage(
            role=Role.assistant,
            content="The answer is 42.",
            reasoning_content="Let me calculate...",
        )

        result = mapper.prepare_message(msg)

        assert isinstance(result, AssistantMessage)
        assert isinstance(result.content, list)
        assert len(result.content) == 2

        think_chunk = result.content[0]
        assert isinstance(think_chunk, ThinkChunk)
        assert think_chunk.type == "thinking"
        assert len(think_chunk.thinking) == 1
        inner_chunk = think_chunk.thinking[0]
        assert isinstance(inner_chunk, TextChunk)
        assert inner_chunk.text == "Let me calculate..."

        text_chunk = result.content[1]
        assert isinstance(text_chunk, TextChunk)
        assert text_chunk.type == "text"
        assert text_chunk.text == "The answer is 42."

    def test_prepare_assistant_message_with_reasoning_and_none_content(self):
        mapper = MistralMapper()
        msg = LLMMessage(
            role=Role.assistant, content=None, reasoning_content="Just thinking..."
        )

        result = mapper.prepare_message(msg)

        assert isinstance(result, AssistantMessage)
        assert isinstance(result.content, list)
        assert len(result.content) == 1

        think_chunk = result.content[0]
        assert isinstance(think_chunk, ThinkChunk)
        assert think_chunk.type == "thinking"
        assert len(think_chunk.thinking) == 1
        inner_chunk = think_chunk.thinking[0]
        assert isinstance(inner_chunk, TextChunk)
        assert inner_chunk.text == "Just thinking..."


class TestGenericBackendReasoningContent:
    @pytest.mark.asyncio
    async def test_complete_extracts_reasoning_content(self):
        base_url = "https://api.example.com"
        json_response = {
            "id": "fake_id",
            "created": 1234567890,
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 42.",
                        "reasoning_content": "Let me think step by step...",
                    },
                }
            ],
        }

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, json=json_response)
            )
            provider = ProviderConfig(
                name="test", api_base=f"{base_url}/v1", api_key_env_var="API_KEY"
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(name="test-model", provider="test", alias="test")
            messages = [LLMMessage(role=Role.user, content="What is the answer?")]

            result = await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert result.message.content == "The answer is 42."
            assert result.message.reasoning_content == "Let me think step by step..."

    @pytest.mark.asyncio
    async def test_complete_streaming_extracts_reasoning_content(self):
        base_url = "https://api.example.com"
        chunks = [
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"Thinking..."},"finish_reason":null}]}',
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{"content":"Answer"},"finish_reason":null}]}',
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
            b"data: [DONE]",
        ]

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="test", api_base=f"{base_url}/v1", api_key_env_var="API_KEY"
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(name="test-model", provider="test", alias="test")
            messages = [LLMMessage(role=Role.user, content="Stream please")]

            results = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(chunk)

            assert results[0].message.reasoning_content == "Thinking..."
            assert results[0].message.content == ""
            assert results[1].message.content == "Answer"
            assert results[1].message.reasoning_content is None


class TestLLMMessageReasoningContent:
    def test_llm_message_from_dict_with_reasoning_content(self):
        data = {
            "role": "assistant",
            "content": "Answer",
            "reasoning_content": "Thinking...",
        }

        msg = LLMMessage.model_validate(data)

        assert msg.reasoning_content == "Thinking..."

    def test_llm_message_model_dump_includes_reasoning_content(self):
        msg = LLMMessage(
            role=Role.assistant, content="Answer", reasoning_content="Thinking..."
        )

        dumped = msg.model_dump(exclude_none=True)

        assert dumped["reasoning_content"] == "Thinking..."

    def test_llm_message_model_dump_includes_reasoning_payloads(self):
        payload = {"type": "reasoning", "encrypted_content": "enc:abc"}
        msg = LLMMessage(
            role=Role.assistant, content="Answer", reasoning_payloads=[payload]
        )

        dumped = msg.model_dump(exclude_none=True)

        assert dumped["reasoning_payloads"] == [payload]

    def test_llm_message_model_dump_excludes_none_reasoning_content(self):
        msg = LLMMessage(role=Role.assistant, content="Answer")

        dumped = msg.model_dump(exclude_none=True)

        assert "reasoning_content" not in dumped

    def test_llm_message_content_list_skips_thinking_chunk(self):
        data = {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": "Let me think..."}],
                    "closed": True,
                },
                {"type": "text", "text": "The answer is 42."},
            ],
        }

        msg = LLMMessage.model_validate(data)

        assert msg.content == "The answer is 42."

    def test_llm_message_content_never_falls_back_to_dict_repr(self):
        thinking = {
            "type": "thinking",
            "thinking": [{"type": "text", "text": "Let me think..."}],
        }
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}}

        assert (
            LLMMessage.model_validate({
                "role": "assistant",
                "content": [thinking, image],
            }).content
            == ""
        )
        assert (
            LLMMessage.model_validate({
                "role": "assistant",
                "content": thinking,
            }).content
            == ""
        )


class TestReasoningFieldNameConversion:
    def test_reasoning_to_api_keeps_default_field(self):
        adapter = OpenAIAdapter()
        msg_dict = {
            "role": "assistant",
            "content": "Answer",
            "reasoning_content": "Thinking...",
        }

        result = adapter._reasoning_to_api(msg_dict, "reasoning_content")

        assert result["reasoning_content"] == "Thinking..."
        assert "reasoning" not in result

    def test_reasoning_to_api_renames_to_custom_field(self):
        adapter = OpenAIAdapter()
        msg_dict = {
            "role": "assistant",
            "content": "Answer",
            "reasoning_content": "Thinking...",
        }

        result = adapter._reasoning_to_api(msg_dict, "reasoning")

        assert result["reasoning"] == "Thinking..."
        assert "reasoning_content" not in result

    def test_reasoning_from_api_converts_custom_field(self):
        adapter = OpenAIAdapter()
        msg_dict = {
            "role": "assistant",
            "content": "Answer",
            "reasoning": "Thinking...",
        }

        result = adapter._reasoning_from_api(msg_dict, "reasoning")

        assert result["reasoning_content"] == "Thinking..."
        assert "reasoning" not in result

    def test_reasoning_from_api_keeps_default_field(self):
        adapter = OpenAIAdapter()
        msg_dict = {
            "role": "assistant",
            "content": "Answer",
            "reasoning_content": "Thinking...",
        }

        result = adapter._reasoning_from_api(msg_dict, "reasoning_content")

        assert result["reasoning_content"] == "Thinking..."

    def test_prepare_request_excludes_reasoning_payloads_from_completions_payload(self):
        adapter = OpenAIAdapter()
        provider = ProviderConfig(
            name="test",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
        )

        request = adapter.prepare_request(
            model_name="test-model",
            messages=[
                LLMMessage(
                    role=Role.assistant,
                    content="Answer",
                    reasoning_content="Thinking...",
                    reasoning_payloads=[
                        {"type": "reasoning", "encrypted_content": "enc:abc"}
                    ],
                )
            ],
            temperature=0.2,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(request.body)

        assert payload["messages"][0]["reasoning_content"] == "Thinking..."
        assert "reasoning_payloads" not in payload["messages"][0]

    def test_prepare_request_excludes_user_display_content_from_completions_payload(
        self,
    ):
        adapter = OpenAIAdapter()
        provider = ProviderConfig(
            name="test",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
        )

        request = adapter.prepare_request(
            model_name="test-model",
            messages=[
                LLMMessage(
                    role=Role.user,
                    content="Look at app.ts",
                    user_display_content=UserDisplayContent(
                        version="1.0.0",
                        host="mistral-vscode",
                        content=[
                            {"type": "text", "text": "Look at "},
                            {
                                "type": "workspace_mention",
                                "kind": "file",
                                "uri": "file:///repo/src/app.ts",
                                "name": "app.ts",
                            },
                        ],
                    ),
                )
            ],
            temperature=0.2,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(request.body)

        assert payload["messages"][0] == {"role": "user", "content": "Look at app.ts"}

    def test_prepare_request_excludes_context_boundary_from_completions_payload(self):
        adapter = OpenAIAdapter()
        provider = ProviderConfig(
            name="test",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
        )

        request = adapter.prepare_request(
            model_name="test-model",
            messages=[
                LLMMessage(
                    role=Role.user,
                    content="Compacted context",
                    injected=True,
                    context_boundary="compaction",
                )
            ],
            temperature=0.2,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(request.body)

        assert payload["messages"][0] == {
            "role": "user",
            "content": "Compacted context",
        }

    @pytest.mark.asyncio
    async def test_complete_with_custom_reasoning_field_name(self):
        base_url = "https://api.example.com"
        json_response = {
            "id": "fake_id",
            "created": 1234567890,
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 42.",
                        "reasoning": "Let me think step by step...",
                    },
                }
            ],
        }

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, json=json_response)
            )
            provider = ProviderConfig(
                name="test",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
                reasoning_field_name="reasoning",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(name="test-model", provider="test", alias="test")
            messages = [LLMMessage(role=Role.user, content="What is the answer?")]

            result = await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert result.message.content == "The answer is 42."
            assert result.message.reasoning_content == "Let me think step by step..."

    @pytest.mark.asyncio
    async def test_streaming_with_custom_reasoning_field_name(self):
        base_url = "https://api.example.com"
        chunks = [
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{"role":"assistant","reasoning":"Thinking..."},"finish_reason":null}]}',
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{"content":"Answer"},"finish_reason":null}]}',
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
            b"data: [DONE]",
        ]

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="test",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
                reasoning_field_name="reasoning",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(name="test-model", provider="test", alias="test")
            messages = [LLMMessage(role=Role.user, content="Stream please")]

            results = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(chunk)

            assert results[0].message.reasoning_content == "Thinking..."
            assert results[1].message.content == "Answer"


def _thinking_chunk(text: str) -> dict[str, object]:
    return {
        "type": "thinking",
        "thinking": [{"type": "text", "text": text}],
        "closed": True,
    }


class TestOpenAIAdapterThinkingChunks:
    """Mistral-compatible content lists parsed through the generic OpenAI style."""

    @pytest.fixture
    def provider(self) -> ProviderConfig:
        return ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
        )

    def test_streaming_delta_splits_thinking_and_text(
        self, provider: ProviderConfig
    ) -> None:
        data = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": [
                            _thinking_chunk("Let me think..."),
                            {"type": "text", "text": "The answer"},
                            {"type": "text", "text": " is 42."},
                        ]
                    },
                }
            ]
        }

        chunk = OpenAIAdapter().parse_response(data, provider)

        assert chunk.message.role == Role.assistant
        assert chunk.message.content == "The answer is 42."
        assert chunk.message.reasoning_content == "Let me think..."

    def test_streaming_thinking_only_delta(self, provider: ProviderConfig) -> None:
        data = {
            "choices": [{"index": 0, "delta": {"content": [_thinking_chunk("Hmm...")]}}]
        }

        chunk = OpenAIAdapter().parse_response(data, provider)

        assert chunk.message.content == ""
        assert chunk.message.reasoning_content == "Hmm..."

    def test_non_streamed_message_splits_thinking_and_text(
        self, provider: ProviderConfig
    ) -> None:
        data = {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": [
                            _thinking_chunk("Step one. "),
                            _thinking_chunk("Step two."),
                            {"type": "text", "text": "Done."},
                        ],
                    },
                }
            ]
        }

        chunk = OpenAIAdapter().parse_response(data, provider)

        assert chunk.message.content == "Done."
        assert chunk.message.reasoning_content == "Step one. Step two."

    def test_custom_reasoning_field_is_not_overwritten(self) -> None:
        provider = ProviderConfig(
            name="test",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
            reasoning_field_name="reasoning",
        )
        data = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "reasoning": "From the field.",
                        "content": [
                            _thinking_chunk("From the chunk."),
                            {"type": "text", "text": "Answer"},
                        ],
                    },
                }
            ]
        }

        chunk = OpenAIAdapter().parse_response(data, provider)

        assert chunk.message.content == "Answer"
        assert chunk.message.reasoning_content == "From the field."

    def test_custom_reasoning_field_absent_uses_thinking_chunks(self) -> None:
        provider = ProviderConfig(
            name="test",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
            reasoning_field_name="reasoning",
        )
        data = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": [
                            _thinking_chunk("From the chunk."),
                            {"type": "text", "text": "Answer"},
                        ]
                    },
                }
            ]
        }

        chunk = OpenAIAdapter().parse_response(data, provider)

        assert chunk.message.content == "Answer"
        assert chunk.message.reasoning_content == "From the chunk."

    @pytest.mark.asyncio
    async def test_streaming_backend_never_emits_chunk_repr(self) -> None:
        base_url = "https://api.example.com"
        thinking = json.dumps({
            "content": [_thinking_chunk("Let me think...")],
            "role": "assistant",
        })
        text = json.dumps({"content": [{"type": "text", "text": "Answer"}]})
        chunks = [
            f'data: {{"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{{"index":0,"delta":{thinking},"finish_reason":null}}]}}'.encode(),
            f'data: {{"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{{"index":0,"delta":{text},"finish_reason":null}}]}}'.encode(),
            b'data: {"id":"id1","object":"chat.completion.chunk","created":123,"model":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
            b"data: [DONE]",
        ]

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="mistral", api_base=f"{base_url}/v1", api_key_env_var="API_KEY"
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(name="test-model", provider="mistral", alias="test")
            messages = [LLMMessage(role=Role.user, content="Stream please")]

            results = [
                chunk
                async for chunk in backend.complete_streaming(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                )
            ]

            assert results[0].message.content == ""
            assert results[0].message.reasoning_content == "Let me think..."
            assert results[1].message.content == "Answer"
            assert results[1].message.reasoning_content is None


class TestMistralReasoningFieldNameValidation:
    def test_mistral_backend_rejects_custom_reasoning_field_name(self):
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            reasoning_field_name="reasoning",
        )

        with pytest.raises(ValueError) as exc_info:
            MistralBackend(provider=provider)

        assert "does not support custom reasoning_field_name" in str(exc_info.value)
        assert "reasoning" in str(exc_info.value)

    def test_mistral_backend_accepts_default_reasoning_field_name(self):
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            reasoning_field_name="reasoning_content",
        )

        backend = MistralBackend(provider=provider)
        assert backend is not None
