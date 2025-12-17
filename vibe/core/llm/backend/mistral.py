from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import os
import re
import time
import types
from typing import TYPE_CHECKING, cast

import httpx
import mistralai

from vibe.core.llm.exceptions import BackendErrorBuilder
from vibe.core.types import (
    AvailableTool,
    Content,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
    ToolCall,
)

try:
    from vibe.core.observability.metrics import (
        get_metrics_manager,
        GENAI_CLIENT_TOKEN_USAGE,
        GENAI_CLIENT_OPERATION_DURATION,
    )
    from vibe.core.observability.semconv import (
        ATTR_GEN_AI_OPERATION_NAME,
        ATTR_GEN_AI_RESPONSE_MODEL,
        ATTR_GEN_AI_SYSTEM,
        ATTR_GEN_AI_TOKEN_TYPE,
        OP_CHAT,
        SYSTEM_MISTRAL,
        TOKEN_TYPE_INPUT,
        TOKEN_TYPE_OUTPUT,
    )

    _METRICS_AVAILABLE = True
except ImportError:
    get_metrics_manager = None
    GENAI_CLIENT_TOKEN_USAGE = None
    GENAI_CLIENT_OPERATION_DURATION = None
    ATTR_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
    ATTR_GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
    ATTR_GEN_AI_SYSTEM = "gen_ai.system"
    ATTR_GEN_AI_TOKEN_TYPE = "gen_ai.token.type"
    OP_CHAT = "chat"
    SYSTEM_MISTRAL = "mistral"
    TOKEN_TYPE_INPUT = "input"
    TOKEN_TYPE_OUTPUT = "output"
    _METRICS_AVAILABLE = False


def _record_genai_metrics(
    duration_s: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model_name: str = "",
) -> None:
    """Record GenAI metrics following OTel GenAI semantic conventions.
    
    Args:
        duration_s: Duration in seconds (OTel convention)
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens  
        model_name: Model name for attributes
    """
    if not _METRICS_AVAILABLE or get_metrics_manager is None:
        return
    metrics = get_metrics_manager()
    
    base_attrs = {
        ATTR_GEN_AI_OPERATION_NAME: OP_CHAT,
        ATTR_GEN_AI_SYSTEM: SYSTEM_MISTRAL,
    }
    if model_name:
        base_attrs[ATTR_GEN_AI_RESPONSE_MODEL] = model_name
    
    # Record input tokens with token type attribute
    if prompt_tokens > 0:
        input_attrs = {**base_attrs, ATTR_GEN_AI_TOKEN_TYPE: TOKEN_TYPE_INPUT}
        metrics.get_counter(GENAI_CLIENT_TOKEN_USAGE).add(prompt_tokens, input_attrs)
    
    # Record output tokens with token type attribute
    if completion_tokens > 0:
        output_attrs = {**base_attrs, ATTR_GEN_AI_TOKEN_TYPE: TOKEN_TYPE_OUTPUT}
        metrics.get_counter(GENAI_CLIENT_TOKEN_USAGE).add(completion_tokens, output_attrs)
    
    # Record duration in seconds
    metrics.get_histogram(GENAI_CLIENT_OPERATION_DURATION).record(duration_s, base_attrs)


if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig


class MistralMapper:
    def prepare_message(self, msg: LLMMessage) -> mistralai.Messages:
        match msg.role:
            case Role.system:
                return mistralai.SystemMessage(role="system", content=msg.content or "")
            case Role.user:
                return mistralai.UserMessage(role="user", content=msg.content)
            case Role.assistant:
                return mistralai.AssistantMessage(
                    role="assistant",
                    content=msg.content,
                    tool_calls=[
                        mistralai.ToolCall(
                            function=mistralai.FunctionCall(
                                name=tc.function.name or "",
                                arguments=tc.function.arguments or "",
                            ),
                            id=tc.id,
                            type=tc.type,
                            index=tc.index,
                        )
                        for tc in msg.tool_calls or []
                    ],
                )
            case Role.tool:
                return mistralai.ToolMessage(
                    role="tool",
                    content=msg.content,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )

    def prepare_tool(self, tool: AvailableTool) -> mistralai.Tool:
        return mistralai.Tool(
            type="function",
            function=mistralai.Function(
                name=tool.function.name,
                description=tool.function.description,
                parameters=tool.function.parameters,
            ),
        )

    def prepare_tool_choice(
        self, tool_choice: StrToolChoice | AvailableTool
    ) -> mistralai.ChatCompletionStreamRequestToolChoice:
        if isinstance(tool_choice, str):
            return cast(mistralai.ToolChoiceEnum, tool_choice)

        return mistralai.ToolChoice(
            type="function",
            function=mistralai.FunctionName(name=tool_choice.function.name),
        )

    def parse_content(self, content: mistralai.AssistantMessageContent) -> Content:
        if isinstance(content, str):
            return content

        concat_content = ""
        for chunk in content:
            if isinstance(chunk, mistralai.FileChunk):
                continue
            match chunk.type:
                case "text":
                    concat_content += chunk.text
                case _:
                    pass
        return concat_content

    def parse_tool_calls(self, tool_calls: list[mistralai.ToolCall]) -> list[ToolCall]:
        return [
            ToolCall(
                id=tool_call.id,
                function=FunctionCall(
                    name=tool_call.function.name,
                    arguments=tool_call.function.arguments
                    if isinstance(tool_call.function.arguments, str)
                    else json.dumps(tool_call.function.arguments),
                ),
                index=tool_call.index,
            )
            for tool_call in tool_calls
        ]


class MistralBackend:
    def __init__(self, provider: ProviderConfig, timeout: float = 720.0) -> None:
        self._client: mistralai.Mistral | None = None
        self._provider = provider
        self._mapper = MistralMapper()
        self._api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        # Mistral SDK takes server URL without api version as input
        url_pattern = r"(https?://[^/]+)(/v.*)"
        match = re.match(url_pattern, self._provider.api_base)
        if not match:
            raise ValueError(
                f"Invalid API base URL: {self._provider.api_base}. "
                "Expected format: <server_url>/v<api_version>"
            )
        self._server_url = match.group(1)
        self._timeout = timeout

    async def __aenter__(self) -> MistralBackend:
        self._client = mistralai.Mistral(
            api_key=self._api_key,
            server_url=self._server_url,
            timeout_ms=int(self._timeout * 1000),
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(
                exc_type=exc_type, exc_val=exc_val, exc_tb=exc_tb
            )

    def _get_client(self) -> mistralai.Mistral:
        if self._client is None:
            self._client = mistralai.Mistral(
                api_key=self._api_key, server_url=self._server_url
            )
        return self._client

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
    ) -> LLMChunk:
        start_time = time.time()
        try:
            response = await self._get_client().chat.complete_async(
                model=model.name,
                messages=[self._mapper.prepare_message(msg) for msg in messages],
                temperature=temperature,
                tools=[self._mapper.prepare_tool(tool) for tool in tools]
                if tools
                else None,
                max_tokens=max_tokens,
                tool_choice=self._mapper.prepare_tool_choice(tool_choice)
                if tool_choice
                else None,
                http_headers=extra_headers,
                stream=False,
            )

            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0
            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, prompt_tokens, completion_tokens, model.name)

            return LLMChunk(
                message=LLMMessage(
                    role=Role.assistant,
                    content=self._mapper.parse_content(
                        response.choices[0].message.content
                    )
                    if response.choices[0].message.content
                    else "",
                    tool_calls=self._mapper.parse_tool_calls(
                        response.choices[0].message.tool_calls
                    )
                    if response.choices[0].message.tool_calls
                    else None,
                ),
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                finish_reason=response.choices[0].finish_reason,
            )

        except mistralai.SDKError as e:
            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, 0, 0, model.name)
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=self._server_url,
                response=e.raw_response,
                headers=dict(e.raw_response.headers.items()),
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, 0, 0, model.name)
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=self._server_url,
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
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
    ) -> AsyncGenerator[LLMChunk, None]:
        start_time = time.time()
        last_prompt_tokens = 0
        last_completion_tokens = 0
        try:
            async for chunk in await self._get_client().chat.stream_async(
                model=model.name,
                messages=[self._mapper.prepare_message(msg) for msg in messages],
                temperature=temperature,
                tools=[self._mapper.prepare_tool(tool) for tool in tools]
                if tools
                else None,
                max_tokens=max_tokens,
                tool_choice=self._mapper.prepare_tool_choice(tool_choice)
                if tool_choice
                else None,
                http_headers=extra_headers,
            ):
                if chunk.data.usage:
                    last_prompt_tokens = chunk.data.usage.prompt_tokens or 0
                    last_completion_tokens = chunk.data.usage.completion_tokens or 0
                yield LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        content=self._mapper.parse_content(
                            chunk.data.choices[0].delta.content
                        )
                        if chunk.data.choices[0].delta.content
                        else "",
                        tool_calls=self._mapper.parse_tool_calls(
                            chunk.data.choices[0].delta.tool_calls
                        )
                        if chunk.data.choices[0].delta.tool_calls
                        else None,
                    ),
                    usage=LLMUsage(
                        prompt_tokens=last_prompt_tokens,
                        completion_tokens=last_completion_tokens,
                    ),
                    finish_reason=chunk.data.choices[0].finish_reason,
                )

            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, last_prompt_tokens, last_completion_tokens, model.name)

        except mistralai.SDKError as e:
            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, last_prompt_tokens, last_completion_tokens, model.name)
            raise BackendErrorBuilder.build_http_error(
                provider=self._provider.name,
                endpoint=self._server_url,
                response=e.raw_response,
                headers=dict(e.raw_response.headers.items()),
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e
        except httpx.RequestError as e:
            duration_s = time.time() - start_time
            _record_genai_metrics(duration_s, last_prompt_tokens, last_completion_tokens, model.name)
            raise BackendErrorBuilder.build_request_error(
                provider=self._provider.name,
                endpoint=self._server_url,
                error=e,
                model=model.name,
                messages=messages,
                temperature=temperature,
                has_tools=bool(tools),
                tool_choice=tool_choice,
            ) from e

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
        result = await self.complete(
            model=model,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=1,
            tool_choice=tool_choice,
            extra_headers=extra_headers,
        )
        assert result.usage is not None, (
            "Usage should be present in non-streaming completions"
        )

        return result.usage.prompt_tokens
