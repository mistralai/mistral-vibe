from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from vibe.core.llm.backend.base import PreparedRequest
from vibe.core.llm.backend.openai import OpenAIAdapter
from vibe.core.llm.message_utils import merge_consecutive_user_messages
from vibe.core.types import AvailableTool, LLMMessage, StrToolChoice

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig


def build_azure_base_url(resource_name: str) -> str:
    return f"https://{resource_name}.openai.azure.com"


def build_azure_endpoint(model_name: str, api_version: str) -> str:
    return (
        f"/openai/deployments/{model_name}/chat/completions?api-version={api_version}"
    )


class AzureOpenAIAdapter(OpenAIAdapter):
    endpoint: ClassVar[str] = ""

    def build_payload(
        self,
        model_name: str,
        converted_messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> dict[str, Any]:
        payload = super().build_payload(
            model_name, converted_messages, temperature, tools, max_tokens, tool_choice
        )
        payload.pop("model")
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
        resource_name = provider.resource_name
        api_version = provider.api_version

        if not resource_name:
            raise ValueError(
                "resource_name is required in provider config for Azure OpenAI"
            )
        if not api_version:
            raise ValueError(
                "api_version is required in provider config for Azure OpenAI"
            )

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
        endpoint = build_azure_endpoint(model_name, api_version)
        base_url = build_azure_base_url(resource_name)

        return PreparedRequest(endpoint, headers, body, base_url=base_url)
