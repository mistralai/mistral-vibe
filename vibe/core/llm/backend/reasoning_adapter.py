from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any, ClassVar

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.base import APIAdapter, PreparedRequest
from vibe.core.llm.message_utils import merge_consecutive_user_messages
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


class ReasoningAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def _convert_message(self, msg: LLMMessage) -> dict[str, Any]:
        match msg.role:
            case Role.system:
                return {"role": "system", "content": msg.content or ""}
            case Role.user:
                return {"role": "user", "content": msg.content or ""}
            case Role.assistant:
                return self._convert_assistant_message(msg)
            case Role.tool:
                result: dict[str, Any] = {
                    "role": "tool",
                    "content": msg.content or "",
                    "tool_call_id": msg.tool_call_id,
                }
                if msg.name:
                    result["name"] = msg.name
                return result

    def _convert_assistant_message(self, msg: LLMMessage) -> dict[str, Any]:
        result: dict[str, Any] = {"role": "assistant"}

        if msg.reasoning_content:
            content: list[dict[str, Any]] = [
                {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": msg.reasoning_content}],
                },
                {"type": "text", "text": msg.content or ""},
            ]
            result["content"] = content
        else:
            result["content"] = msg.content or ""

        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name or "",
                        "arguments": tc.function.arguments or "",
                    },
                    **({"index": tc.index} if tc.index is not None else {}),
                }
                for tc in msg.tool_calls
            ]

        return result

    def _build_payload(
        self,
        *,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        thinking: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
        }

        if thinking != "off":
            payload["reasoning_effort"] = thinking

        if tools:
            payload["tools"] = [tool.model_dump(exclude_none=True) for tool in tools]

        if tool_choice:
            payload["tool_choice"] = (
                tool_choice
                if isinstance(tool_choice, str)
                else tool_choice.model_dump()
            )

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return payload

    def prepare_request(  # noqa: PLR0913
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest:
        merged_messages = merge_consecutive_user_messages(messages)
        converted_messages = [self._convert_message(msg) for msg in merged_messages]

        payload = self._build_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
        )

        if enable_streaming:
            payload["stream"] = True
            payload["stream_options"] = {
                "include_usage": True,
                "stream_tool_calls": True,
            }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return PreparedRequest(self.endpoint, headers, body)

    @staticmethod
    def _parse_content_blocks(
        content: str | list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        if isinstance(content, str):
            return content or None, None

        text_parts: list[str] = []
        thinking_parts: list[str] = []

        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                for inner in block.get("thinking", []):
                    if isinstance(inner, dict) and inner.get("type") == "text":
                        thinking_parts.append(inner.get("text", ""))
                    elif isinstance(inner, str):
                        thinking_parts.append(inner)

        return ("".join(text_parts) or None, "".join(thinking_parts) or None)

    @staticmethod
    def _parse_tool_calls(
        tool_calls: list[dict[str, Any]] | None,
    ) -> list[ToolCall] | None:
        if not tool_calls:
            return None
        return [
            ToolCall(
                id=tc.get("id"),
                index=tc.get("index"),
                function=FunctionCall(
                    name=tc.get("function", {}).get("name"),
                    arguments=tc.get("function", {}).get("arguments", ""),
                ),
            )
            for tc in tool_calls
        ]

    def _parse_message_dict(self, msg_dict: dict[str, Any]) -> LLMMessage:
        content = msg_dict.get("content")
        text_content: str | None = None
        reasoning_content: str | None = None

        if content is not None:
            text_content, reasoning_content = self._parse_content_blocks(content)

        return LLMMessage(
            role=Role.assistant,
            content=text_content,
            reasoning_content=reasoning_content,
            tool_calls=self._parse_tool_calls(msg_dict.get("tool_calls")),
        )

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig
    ) -> LLMChunk:
        message: LLMMessage | None = None

        if data.get("choices"):
            choice = data["choices"][0]
            if "message" in choice:
                message = self._parse_message_dict(choice["message"])
            elif "delta" in choice:
                message = self._parse_message_dict(choice["delta"])

        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        usage_data = data.get("usage") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMChunk(message=message, usage=usage)
