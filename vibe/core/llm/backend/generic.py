from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import os
import types
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx

from vibe.core.llm.backend.anthropic import AnthropicAdapter
from vibe.core.llm.backend.azure import AzureOpenAIAdapter
from vibe.core.llm.backend.base import APIAdapter
from vibe.core.llm.backend.openai import OpenAIAdapter
from vibe.core.llm.backend.vertex import VertexAnthropicAdapter
from vibe.core.llm.exceptions import BackendErrorBuilder
from vibe.core.types import AvailableTool, LLMChunk, LLMMessage, Role, StrToolChoice
from vibe.core.utils import async_generator_retry, async_retry

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig

ADAPTERS: dict[str, APIAdapter] = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "azure-openai": AzureOpenAIAdapter(),
    "vertex-anthropic": VertexAnthropicAdapter(),
}


class GenericBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        provider: ProviderConfig,
        timeout: float = 720.0,
    ) -> None:
        """Initialize the backend.

        Args:
            client: Optional httpx client to use. If not provided, one will be created.
        """
        self._client = client
        self._owns_client = client is None
        self._provider = provider
        self._timeout = timeout

    async def __aenter__(self) -> GenericBackend:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
            self._owns_client = True
        return self._client

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> LLMChunk:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = ADAPTERS[api_style]

        req = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=False,
            provider=self._provider,
            api_key=api_key,
            thinking=model.thinking,
        )

        headers = req.headers
        if extra_headers:
            headers.update(extra_headers)

        base = req.base_url or self._provider.api_base
        url = f"{base}{req.endpoint}"

        try:
            res_data, _ = await self._make_request(url, req.body, headers)
            return adapter.parse_response(res_data, self._provider)

        except httpx.HTTPStatusError as e:
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=url,
                response=e.response,
                headers=e.response.headers,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=url,
                error=e,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = ADAPTERS[api_style]

        req = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=True,
            provider=self._provider,
            api_key=api_key,
            thinking=model.thinking,
        )

        headers = req.headers
        if extra_headers:
            headers.update(extra_headers)

        base = req.base_url or self._provider.api_base
        url = f"{base}{req.endpoint}"

        try:
            async for res_data in self._make_streaming_request(url, req.body, headers):
                yield adapter.parse_response(res_data, self._provider)

        except httpx.HTTPStatusError as e:
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=url,
                response=e.response,
                headers=e.response.headers,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=url,
                error=e,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e

    class HTTPResponse(NamedTuple):
        data: dict[str, Any]
        headers: dict[str, str]

    @async_retry(tries=3)
    async def _make_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> HTTPResponse:
        client = self._get_client()
        response = await client.post(url, content=data, headers=headers)
        response.raise_for_status()

        response_headers = dict(response.headers.items())
        response_body = response.json()
        return self.HTTPResponse(response_body, response_headers)

    @async_generator_retry(tries=3)
    async def _make_streaming_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> AsyncGenerator[dict[str, Any]]:
        client = self._get_client()
        async with client.stream(
            method="POST", url=url, content=data, headers=headers
        ) as response:
            if not response.is_success:
                await response.aread()
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip() == "":
                    continue

                DELIM_CHAR = ":"
                if f"{DELIM_CHAR} " not in line:
                    raise ValueError(
                        f"Stream chunk improperly formatted. "
                        f"Expected `key{DELIM_CHAR} value`, received `{line}`"
                    )
                delim_index = line.find(DELIM_CHAR)
                key = line[0:delim_index]
                value = line[delim_index + 2 :]

                if key != "data":
                    # This might be the case with openrouter, so we just ignore it
                    continue
                if value == "[DONE]":
                    return
                yield json.loads(value.strip())

    async def count_tokens(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        tools: list[AvailableTool] | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> int:
        probe_messages = list(messages)
        if not probe_messages or probe_messages[-1].role != Role.user:
            probe_messages.append(LLMMessage(role=Role.user, content=""))

        result = await self.complete(
            model=model,
            messages=probe_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=16,  # Minimal amount for openrouter with openai models
            tool_choice=tool_choice,
            extra_headers=extra_headers,
        )
        if result.usage is None:
            raise ValueError("Missing usage in non streaming completion")

        return result.usage.prompt_tokens

    async def close(self) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
