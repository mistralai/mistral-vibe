from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from vibe.core.llm.backend.base import APIAdapter, PreparedRequest
from vibe.core.llm.message_utils import merge_consecutive_user_messages
from vibe.core.types import (
    AvailableTool,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
)

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig


class AzureOpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def build_payload(
        self,
        model_name: str,
        converted_messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> dict[str, Any]:
        payload = {
            "model": model_name,
            "messages": converted_messages,
            "temperature": temperature,
        }

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

    def build_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

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
        merged_messages = merge_consecutive_user_messages(messages)
        field_name = provider.reasoning_field_name
        converted_messages = [
            self._reasoning_to_api(
                msg.model_dump(exclude_none=True, exclude={"message_id"}), field_name
            )
            for msg in merged_messages
        ]

        payload = self.build_payload(
            model_name, converted_messages, temperature, tools, max_tokens, tool_choice
        )

        if enable_streaming:
            payload["stream"] = True
            stream_options = {"include_usage": True}
            if provider.name == "mistral":
                stream_options["stream_tool_calls"] = True
            payload["stream_options"] = stream_options

        headers = self.build_headers(api_key)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"/deployments/{model_name}{self.endpoint}?api-version={provider.api_version}"

        return PreparedRequest(url, headers, body)

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
                return LLMMessage.model_validate(msg_dict)
            raise ValueError("Invalid response data: missing message or delta")

        if "message" in data:
            msg_dict = self._reasoning_from_api(data["message"], field_name)
            return LLMMessage.model_validate(msg_dict)
        if "delta" in data:
            msg_dict = self._reasoning_from_api(data["delta"], field_name)
            return LLMMessage.model_validate(msg_dict)

        return None

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig
    ) -> LLMChunk:
        message = self._parse_message(data, provider.reasoning_field_name)
        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        usage_data = data.get("usage") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMChunk(message=message, usage=usage)
