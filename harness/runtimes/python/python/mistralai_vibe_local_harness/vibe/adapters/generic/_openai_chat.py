"""OpenAI-compatible chat/completions adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from mistralai_vibe_local_harness.vibe.adapters.generic._base import (
    APIAdapter,
    PreparedRequest,
    build_chat_payload,
    finalize_chat_request,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._image import to_data_uri
from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StopInfo,
    StrToolChoice,
    ToolCall,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView


class OpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def _reasoning_to_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and "reasoning_content" in msg_dict:
            msg_dict[field_name] = msg_dict.pop("reasoning_content")
        return msg_dict

    def _reasoning_from_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and field_name in msg_dict:
            msg_dict["reasoning_content"] = msg_dict.pop(field_name)
        return msg_dict

    def _user_with_images_to_parts(
        self, msg_dict: dict[str, Any], source: LLMMessage
    ) -> dict[str, Any]:
        if source.role != Role.user or not source.images:
            return msg_dict
        parts: list[dict[str, Any]] = []
        text = msg_dict.get("content")
        if isinstance(text, str) and text:
            parts.append({"type": "text", "text": text})
        parts.extend(
            {"type": "image_url", "image_url": {"url": to_data_uri(att)}}
            for att in source.images
        )
        msg_dict["content"] = parts
        return msg_dict

    def _convert_messages(
        self, messages: Sequence[LLMMessage], provider: ProviderView
    ) -> list[dict[str, Any]]:
        field_name = provider.reasoning_field_name
        return [
            self._user_with_images_to_parts(
                self._reasoning_to_api(
                    msg.model_dump(
                        exclude_none=True,
                        exclude={"reasoning_payloads": True, "images": True},
                    ),
                    field_name,
                ),
                msg,
            )
            for msg in messages
        ]

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderView,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest:
        converted_messages = self._convert_messages(messages, provider)

        payload = build_chat_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
        )

        stream_options: dict[str, Any] = {"include_usage": True}
        if provider.name == "mistral":
            stream_options["stream_tool_calls"] = True

        return finalize_chat_request(
            payload=payload,
            enable_streaming=enable_streaming,
            stream_options=stream_options,
            api_key=api_key,
            endpoint=self.endpoint,
        )

    def _parse_message(
        self, data: dict[str, Any], field_name: str
    ) -> LLMMessage | None:
        if data.get("choices"):
            choice = data["choices"][0]
            if "message" in choice:
                msg_dict = self._reasoning_from_api(choice["message"], field_name)
                return LLMMessage.model_validate(msg_dict)
            if "delta" in choice:
                msg_dict = self._reasoning_from_api(choice["delta"], field_name)
                if msg_dict.get("role") is None:
                    msg_dict["role"] = Role.assistant
                return LLMMessage.model_validate(msg_dict)
            raise ValueError("Invalid response data: missing message or delta")

        if "message" in data:
            msg_dict = self._reasoning_from_api(data["message"], field_name)
            return LLMMessage.model_validate(msg_dict)
        if "delta" in data:
            msg_dict = self._reasoning_from_api(data["delta"], field_name)
            if msg_dict.get("role") is None:
                msg_dict["role"] = Role.assistant
            return LLMMessage.model_validate(msg_dict)

        return None

    def parse_response(self, data: dict[str, Any], provider: ProviderView) -> LLMChunk:
        message = self._parse_message(data, provider.reasoning_field_name)
        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        usage_data = data.get("usage") or {}
        prompt_details = usage_data.get("prompt_tokens_details") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            cached_tokens=prompt_details.get("cached_tokens", 0),
        )
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        stop = (
            StopInfo(reason=str(finish_reason)) if finish_reason is not None else None
        )

        return LLMChunk(message=message, usage=usage, stop=stop)


class ReasoningAdapter(APIAdapter):
    """OpenAI-compatible adapter for providers that emit structured thinking blocks."""

    endpoint: ClassVar[str] = "/chat/completions"

    def _convert_message(self, msg: LLMMessage) -> dict[str, Any]:
        match msg.role:
            case Role.system:
                return {"role": "system", "content": msg.content or ""}
            case Role.user:
                if msg.images:
                    parts: list[dict[str, Any]] = []
                    if msg.content:
                        parts.append({"type": "text", "text": msg.content})
                    parts.extend(
                        {"type": "image_url", "image_url": {"url": to_data_uri(att)}}
                        for att in msg.images
                    )
                    return {"role": "user", "content": parts}
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
                }
            ]
            if msg.content:
                content.append({"type": "text", "text": msg.content})
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

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderView,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest:
        converted_messages = [self._convert_message(msg) for msg in messages]

        payload = build_chat_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
        )

        return finalize_chat_request(
            payload=payload,
            enable_streaming=enable_streaming,
            stream_options={"include_usage": True, "stream_tool_calls": True},
            api_key=api_key,
            endpoint=self.endpoint,
        )

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

    def parse_response(self, data: dict[str, Any], provider: ProviderView) -> LLMChunk:
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
        prompt_details = usage_data.get("prompt_tokens_details") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            cached_tokens=prompt_details.get("cached_tokens", 0),
        )
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        stop = (
            StopInfo(reason=str(finish_reason)) if finish_reason is not None else None
        )

        return LLMChunk(message=message, usage=usage, stop=stop)


__all__ = ["OpenAIAdapter", "ReasoningAdapter"]
