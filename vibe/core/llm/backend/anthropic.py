from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.base import APIAdapter, PreparedRequest
from vibe.core.types import (
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
    ToolCall,
)


class AnthropicMapper:
    """Shared mapper for converting messages to/from Anthropic API format."""

    def prepare_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system_prompt: str | None = None
        converted: list[dict[str, Any]] = []

        for msg in messages:
            match msg.role:
                case Role.system:
                    system_prompt = msg.content or ""
                case Role.user:
                    user_content: list[dict[str, Any]] = []
                    if msg.content:
                        user_content.append({"type": "text", "text": msg.content})
                    converted.append({"role": "user", "content": user_content or ""})
                case Role.assistant:
                    converted.append(self._convert_assistant_message(msg))
                case Role.tool:
                    self._append_tool_result(converted, msg)

        return system_prompt, converted

    def _sanitize_tool_call_id(self, tool_id: str | None) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id or "")

    def _convert_assistant_message(self, msg: LLMMessage) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if msg.reasoning_content:
            block: dict[str, Any] = {
                "type": "thinking",
                "thinking": msg.reasoning_content,
            }
            if msg.reasoning_signature:
                block["signature"] = msg.reasoning_signature
            content.append(block)
        if msg.content:
            content.append({"type": "text", "text": msg.content})
        if msg.tool_calls:
            for tc in msg.tool_calls:
                content.append(self._convert_tool_call(tc))
        return {"role": "assistant", "content": content if content else ""}

    def _convert_tool_call(self, tc: ToolCall) -> dict[str, Any]:
        try:
            tool_input = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            tool_input = {}
        return {
            "type": "tool_use",
            "id": self._sanitize_tool_call_id(tc.id),
            "name": tc.function.name,
            "input": tool_input,
        }

    def _append_tool_result(
        self, converted: list[dict[str, Any]], msg: LLMMessage
    ) -> None:
        tool_result = {
            "type": "tool_result",
            "tool_use_id": self._sanitize_tool_call_id(msg.tool_call_id),
            "content": msg.content or "",
        }

        if not converted or converted[-1]["role"] != "user":
            converted.append({"role": "user", "content": [tool_result]})
            return

        existing_content = converted[-1]["content"]
        if isinstance(existing_content, str):
            converted[-1]["content"] = [
                {"type": "text", "text": existing_content},
                tool_result,
            ]
        else:
            converted[-1]["content"].append(tool_result)

    def prepare_tools(
        self, tools: list[AvailableTool] | None
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "name": tool.function.name,
                "description": tool.function.description,
                "input_schema": tool.function.parameters,
            }
            for tool in tools
        ]

    def prepare_tool_choice(
        self, tool_choice: StrToolChoice | AvailableTool | None
    ) -> dict[str, Any] | None:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, str):
            match tool_choice:
                case "none":
                    return {"type": "none"}
                case "auto":
                    return {"type": "auto"}
                case "any" | "required":
                    return {"type": "any"}
                case _:
                    return None
        return {"type": "tool", "name": tool_choice.function.name}

    def parse_response(self, data: dict[str, Any]) -> LLMChunk:
        content_blocks = data.get("content", [])
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        signature_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for idx, block in enumerate(content_blocks):
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                thinking_parts.append(block.get("thinking", ""))
                if "signature" in block:
                    signature_parts.append(block["signature"])
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id"),
                        index=idx,
                        function=FunctionCall(
                            name=block.get("name"),
                            arguments=json.dumps(block.get("input", {})),
                        ),
                    )
                )

        usage_data = data.get("usage", {})
        # Total input tokens = input_tokens + cache_creation + cache_read
        total_input_tokens = (
            usage_data.get("input_tokens", 0)
            + usage_data.get("cache_creation_input_tokens", 0)
            + usage_data.get("cache_read_input_tokens", 0)
        )
        usage = LLMUsage(
            prompt_tokens=total_input_tokens,
            completion_tokens=usage_data.get("output_tokens", 0),
        )

        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content="".join(text_parts) or None,
                reasoning_content="".join(thinking_parts) or None,
                reasoning_signature="".join(signature_parts) or None,
                tool_calls=tool_calls if tool_calls else None,
            ),
            usage=usage,
        )

    def parse_streaming_event(
        self, event_type: str, data: dict[str, Any], current_index: int
    ) -> tuple[LLMChunk | None, int]:
        handler = {
            "content_block_start": self._handle_block_start,
            "content_block_delta": self._handle_block_delta,
            "message_delta": self._handle_message_delta,
            "message_start": self._handle_message_start,
        }.get(event_type)
        if handler is None:
            return None, current_index
        return handler(data, current_index)

    def _handle_block_start(
        self, data: dict[str, Any], current_index: int
    ) -> tuple[LLMChunk | None, int]:
        block = data.get("content_block", {})
        idx = data.get("index", current_index)

        match block.get("type"):
            case "tool_use":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        tool_calls=[
                            ToolCall(
                                id=block.get("id"),
                                index=idx,
                                function=FunctionCall(
                                    name=block.get("name"), arguments=""
                                ),
                            )
                        ],
                    )
                )
                return chunk, idx
            case "thinking":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant, reasoning_content=block.get("thinking", "")
                    )
                )
                return chunk, idx
            case _:
                return None, idx

    def _handle_block_delta(
        self, data: dict[str, Any], current_index: int
    ) -> tuple[LLMChunk | None, int]:
        delta = data.get("delta", {})
        idx = data.get("index", current_index)

        match delta.get("type"):
            case "text_delta":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant, content=delta.get("text", "")
                    )
                )
            case "thinking_delta":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant, reasoning_content=delta.get("thinking", "")
                    )
                )
            case "signature_delta":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        reasoning_signature=delta.get("signature", ""),
                    )
                )
            case "input_json_delta":
                chunk = LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        tool_calls=[
                            ToolCall(
                                index=idx,
                                function=FunctionCall(
                                    arguments=delta.get("partial_json", "")
                                ),
                            )
                        ],
                    )
                )
            case _:
                chunk = None
        return chunk, idx

    def _handle_message_delta(
        self, data: dict[str, Any], current_index: int
    ) -> tuple[LLMChunk | None, int]:
        usage_data = data.get("usage", {})
        if not usage_data:
            return None, current_index
        chunk = LLMChunk(
            message=LLMMessage(role=Role.assistant),
            usage=LLMUsage(
                prompt_tokens=0, completion_tokens=usage_data.get("output_tokens", 0)
            ),
        )
        return chunk, current_index

    def _handle_message_start(
        self, data: dict[str, Any], current_index: int
    ) -> tuple[LLMChunk | None, int]:
        message = data.get("message", {})
        usage_data = message.get("usage", {})
        if not usage_data:
            return None, current_index
        # Total input tokens = input_tokens + cache_creation + cache_read
        total_input_tokens = (
            usage_data.get("input_tokens", 0)
            + usage_data.get("cache_creation_input_tokens", 0)
            + usage_data.get("cache_read_input_tokens", 0)
        )
        chunk = LLMChunk(
            message=LLMMessage(role=Role.assistant),
            usage=LLMUsage(prompt_tokens=total_input_tokens, completion_tokens=0),
        )
        return chunk, current_index


STREAMING_EVENT_TYPES = {
    "message_start",
    "message_delta",
    "message_stop",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "ping",
    "error",
}


class AnthropicAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/v1/messages"
    API_VERSION = "2023-06-01"
    BETA_FEATURES = (
        "interleaved-thinking-2025-05-14,"
        "fine-grained-tool-streaming-2025-05-14,"
        "prompt-caching-2024-07-31"
    )
    THINKING_BUDGETS: ClassVar[dict[str, int]] = {
        "low": 1024,
        "medium": 10_000,
        "high": 32_000,
    }
    DEFAULT_ADAPTIVE_MAX_TOKENS: ClassVar[int] = 32_768
    DEFAULT_MAX_TOKENS = 8192

    def __init__(self) -> None:
        self._mapper = AnthropicMapper()
        self._current_index: int = 0

    @staticmethod
    def _has_thinking_content(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "thinking":
                    return True
        return False

    @staticmethod
    def _build_system_blocks(system_prompt: str | None) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if system_prompt:
            blocks.append({
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            })
        return blocks

    @staticmethod
    def _add_cache_control_to_last_user_message(messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        last_message = messages[-1]
        if last_message.get("role") != "user":
            return
        content = last_message.get("content")
        if not isinstance(content, list) or not content:
            return
        last_block = content[-1]
        if last_block.get("type") in {"text", "image", "tool_result"}:
            last_block["cache_control"] = {"type": "ephemeral"}

    @staticmethod
    def _is_adaptive_model(model_name: str) -> bool:
        return "opus-4-6" in model_name

    def _apply_thinking_config(
        self,
        payload: dict[str, Any],
        *,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        thinking: str,
    ) -> None:
        has_thinking = self._has_thinking_content(messages)
        thinking_level = thinking

        if thinking_level == "off" and not has_thinking:
            payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            else:
                payload["max_tokens"] = self.DEFAULT_MAX_TOKENS
            return

        # Resolve effective level: use config, or fallback to "medium" when
        # forced by thinking content in history
        effective_level = thinking_level if thinking_level != "off" else "medium"

        if self._is_adaptive_model(model_name):
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": effective_level}
            default_max = self.DEFAULT_ADAPTIVE_MAX_TOKENS
        else:
            budget = self.THINKING_BUDGETS[effective_level]
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            default_max = budget + self.DEFAULT_MAX_TOKENS

        payload["temperature"] = 1
        payload["max_tokens"] = max_tokens if max_tokens is not None else default_max

    def _build_payload(
        self,
        *,
        model_name: str,
        system_prompt: str | None,
        messages: list[dict[str, Any]],
        temperature: float,
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        tool_choice: dict[str, Any] | None,
        stream: bool,
        thinking: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model_name, "messages": messages}

        self._apply_thinking_config(
            payload,
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
        )

        if system_blocks := self._build_system_blocks(system_prompt):
            payload["system"] = system_blocks

        if tools:
            payload["tools"] = tools

        if tool_choice:
            payload["tool_choice"] = tool_choice

        if stream:
            payload["stream"] = True

        self._add_cache_control_to_last_user_message(messages)

        return payload

    def prepare_request(  # noqa: PLR0913
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest:
        system_prompt, converted_messages = self._mapper.prepare_messages(messages)
        converted_tools = self._mapper.prepare_tools(tools)
        converted_tool_choice = self._mapper.prepare_tool_choice(tool_choice)

        payload = self._build_payload(
            model_name=model_name,
            system_prompt=system_prompt,
            messages=converted_messages,
            temperature=temperature,
            tools=converted_tools,
            max_tokens=max_tokens,
            tool_choice=converted_tool_choice,
            stream=enable_streaming,
            thinking=thinking,
        )

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.API_VERSION,
            "anthropic-beta": self.BETA_FEATURES,
        }

        if api_key:
            headers["x-api-key"] = api_key

        body = json.dumps(payload).encode("utf-8")
        return PreparedRequest(self.endpoint, headers, body)

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig | None = None
    ) -> LLMChunk:
        event_type = data.get("type")
        if event_type in STREAMING_EVENT_TYPES:
            return self._parse_streaming_event(data)
        return self._mapper.parse_response(data)

    def _parse_streaming_event(self, data: dict[str, Any]) -> LLMChunk:
        event_type = data.get("type", "")
        empty_chunk = LLMChunk(message=LLMMessage(role=Role.assistant, content=None))

        if event_type == "message_start":
            self._current_index = 0
            return self._parse_message_start(data)
        if event_type == "content_block_start":
            return self._parse_content_block_start(data) or empty_chunk
        if event_type == "content_block_delta":
            return self._parse_content_block_delta(data)
        if event_type == "content_block_stop":
            return self._parse_content_block_stop(data)
        if event_type == "message_delta":
            return self._parse_message_delta(data)
        if event_type == "error":
            error = data.get("error", {})
            error_type = error.get("type", "unknown_error")
            error_message = error.get("message", "Unknown streaming error")
            raise RuntimeError(
                f"Anthropic stream error ({error_type}): {error_message}"
            )
        return empty_chunk

    def _parse_message_start(self, data: dict[str, Any]) -> LLMChunk:
        message = data.get("message", {})
        usage_data = message.get("usage", {})
        if not usage_data:
            return LLMChunk(message=LLMMessage(role=Role.assistant, content=None))
        total_input_tokens = (
            usage_data.get("input_tokens", 0)
            + usage_data.get("cache_creation_input_tokens", 0)
            + usage_data.get("cache_read_input_tokens", 0)
        )
        return LLMChunk(
            message=LLMMessage(role=Role.assistant, content=None),
            usage=LLMUsage(prompt_tokens=total_input_tokens, completion_tokens=0),
        )

    def _parse_content_block_start(self, data: dict[str, Any]) -> LLMChunk | None:
        content_block = data.get("content_block", {})
        index = data.get("index", 0)
        block_type = content_block.get("type")

        if block_type == "thinking":
            return LLMChunk(
                message=LLMMessage(
                    role=Role.assistant,
                    reasoning_content=content_block.get("thinking", ""),
                )
            )
        if block_type == "redacted_thinking":
            return None
        if block_type == "tool_use":
            return LLMChunk(
                message=LLMMessage(
                    role=Role.assistant,
                    tool_calls=[
                        ToolCall(
                            index=index,
                            id=content_block.get("id"),
                            function=FunctionCall(
                                name=content_block.get("name"), arguments=""
                            ),
                        )
                    ],
                )
            )
        return None

    def _parse_content_block_delta(self, data: dict[str, Any]) -> LLMChunk:
        delta = data.get("delta", {})
        delta_type = delta.get("type", "")
        index = data.get("index", 0)

        match delta_type:
            case "text_delta":
                return LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant, content=delta.get("text", "")
                    )
                )
            case "thinking_delta":
                return LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant, reasoning_content=delta.get("thinking", "")
                    )
                )
            case "signature_delta":
                return LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        reasoning_signature=delta.get("signature", ""),
                    )
                )
            case "input_json_delta":
                return LLMChunk(
                    message=LLMMessage(
                        role=Role.assistant,
                        tool_calls=[
                            ToolCall(
                                index=index,
                                function=FunctionCall(
                                    arguments=delta.get("partial_json", "")
                                ),
                            )
                        ],
                    )
                )
            case _:
                return LLMChunk(message=LLMMessage(role=Role.assistant, content=None))

    def _parse_content_block_stop(self, _data: dict[str, Any]) -> LLMChunk:
        return LLMChunk(message=LLMMessage(role=Role.assistant, content=None))

    def _parse_message_delta(self, data: dict[str, Any]) -> LLMChunk:
        usage_data = data.get("usage", {})
        if not usage_data:
            return LLMChunk(message=LLMMessage(role=Role.assistant, content=None))
        return LLMChunk(
            message=LLMMessage(role=Role.assistant, content=None),
            usage=LLMUsage(
                prompt_tokens=0, completion_tokens=usage_data.get("output_tokens", 0)
            ),
        )
