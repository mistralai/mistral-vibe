"""
Test suite for GenericBackend streaming parser content-type handling.

This verifies the fix for: Streaming parser assumes SSE but Ollama/OpenAI-compatible
servers may return Content-Type: application/json

Tests cover:
1. SSE path with text/event-stream
2. JSON fallback with application/json
3. JSON fallback with application/json; charset=utf-8
4. Malformed JSON handling
5. SSE parsing: empty lines, comments, [DONE] signal
"""

import json
import pytest
import httpx
import respx
from typing import AsyncGenerator

from vibe.core.config import ProviderConfig, ModelConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.types import LLMMessage, Role


class TestGenericStreamingContentType:
    """Test content-type based branching in _make_streaming_request"""

    @pytest.fixture
    def provider(self) -> ProviderConfig:
        return ProviderConfig(
            name="test_provider",
            api_base="http://localhost:8000/v1",
            api_style="openai",
        )

    @pytest.fixture
    def model(self) -> ModelConfig:
        return ModelConfig(name="test-model", provider="test_provider")

    @pytest.fixture
    def messages(self) -> list[LLMMessage]:
        return [LLMMessage(role=Role.user, content="Hello")]

    # -- SSE PATH TESTS --

    @pytest.mark.asyncio
    async def test_sse_basic_streaming(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test standard SSE streaming with text/event-stream content-type"""
        sse_data = [
            b'data: {"id": "1", "choices": [{"delta": {"content": "Hello"}}]}\n\n',
            b'data: {"id": "2", "choices": [{"delta": {"content": " world"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            # Should receive 2 chunks (not [DONE])
            assert len(chunks) == 2
            assert chunks[0].message.content == "Hello"
            assert chunks[1].message.content == " world"

    @pytest.mark.asyncio
    async def test_sse_with_charset(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test SSE with text/event-stream; charset=utf-8"""
        sse_data = [
            b'data: {"id": "1", "choices": [{"delta": {"content": "Test"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream; charset=utf-8"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "Test"

    @pytest.mark.asyncio
    async def test_sse_ignores_empty_lines(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test SSE parser ignores empty lines"""
        sse_data = [
            b'\n',
            b'\n',
            b'data: {"id": "1", "choices": [{"delta": {"content": "Hi"}}]}\n\n',
            b'\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "Hi"

    @pytest.mark.asyncio
    async def test_sse_ignores_comment_lines(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test SSE parser ignores lines starting with :"""
        sse_data = [
            b': heartbeat\n\n',
            b'data: {"id": "1", "choices": [{"delta": {"content": "A"}}]}\n\n',
            b': comment\n\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "A"

    @pytest.mark.asyncio
    async def test_sse_ignores_non_data_fields(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test SSE parser ignores non-data SSE fields like event:, id:, retry:"""
        sse_data = [
            b'event: message\n',
            b'id: 123\n',
            b'retry: 5000\n',
            b'data: {"id": "1", "choices": [{"delta": {"content": "B"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "B"

    @pytest.mark.asyncio
    async def test_sse_stops_on_done(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test SSE parser stops when receiving [DONE]"""
        sse_data = [
            b'data: {"id": "1", "choices": [{"delta": {"content": "C"}}]}\n\n',
            b'data: [DONE]\n\n',
            b'data: {"id": "2", "choices": [{"delta": {"content": "should not appear"}}]}\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            # Should stop at [DONE], so only 1 chunk
            assert len(chunks) == 1
            assert chunks[0].message.content == "C"

    # -- JSON FALLBACK TESTS --

    @pytest.mark.asyncio
    async def test_json_fallback_application_json(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test JSON fallback when content-type is application/json"""
        json_response = json.dumps({
            "id": "1",
            "choices": [{"message": {"content": "JSON response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }).encode("utf-8")
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=json_response,
                    headers={"Content-Type": "application/json"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            # Should yield exactly 1 chunk from JSON body
            assert len(chunks) == 1
            assert chunks[0].message.content == "JSON response"

    @pytest.mark.asyncio
    async def test_json_fallback_with_charset(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test JSON fallback when content-type is application/json; charset=utf-8"""
        json_response = json.dumps({
            "id": "1",
            "choices": [{"message": {"content": "JSON with charset"}}],
        }).encode("utf-8")
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=json_response,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "JSON with charset"

    @pytest.mark.asyncio
    async def test_json_fallback_case_insensitive(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test JSON fallback with uppercase Content-Type"""
        json_response = json.dumps({
            "id": "1",
            "choices": [{"message": {"content": "uppercase"}}],
        }).encode("utf-8")
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=json_response,
                    headers={"Content-Type": "APPLICATION/JSON"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "uppercase"

    # -- MALFORMED INPUT TESTS --

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test that malformed JSON raises a deterministic error"""
        malformed_json = b'{"incomplete": '
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=malformed_json,
                    headers={"Content-Type": "application/json"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            
            with pytest.raises(json.JSONDecodeError):
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                ):
                    pass
            
            await backend.close()

    @pytest.mark.asyncio
    async def test_empty_body_raises(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test that empty response body raises ValueError"""
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=b"",
                    headers={"Content-Type": "application/json"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            
            with pytest.raises(ValueError, match="Expected response body but received empty body"):
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                ):
                    pass
            
            await backend.close()

    # -- MIXED HEADERS TESTS --

    @pytest.mark.asyncio
    async def test_mixed_headers_sse_priority(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test that text/event-stream is prioritized even with other params"""
        sse_data = [
            b'data: {"id": "1", "choices": [{"delta": {"content": "SSE wins"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"".join(sse_data)),
                    headers={"Content-Type": "text/event-stream; boundary=something"},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            # Should use SSE path, not JSON fallback
            assert len(chunks) == 1
            assert chunks[0].message.content == "SSE wins"

    @pytest.mark.asyncio
    async def test_mixed_headers_json_with_spaces(self, provider: ProviderConfig, model: ModelConfig, messages: list[LLMMessage]):
        """Test JSON fallback with content-type containing spaces"""
        json_response = json.dumps({
            "id": "1",
            "choices": [{"message": {"content": "spaces"}}],
        }).encode("utf-8")
        
        with respx.mock(base_url="http://localhost:8000") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    content=json_response,
                    headers={"Content-Type": " application/json ; charset=utf-8 "},
                )
            )
            
            backend = GenericBackend(provider=provider)
            chunks = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
            ):
                chunks.append(chunk)
            
            await backend.close()
            
            assert len(chunks) == 1
            assert chunks[0].message.content == "spaces"
