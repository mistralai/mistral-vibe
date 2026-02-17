from __future__ import annotations

import json
from typing import Any, ClassVar

import google.auth
from google.auth.transport.requests import Request

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.anthropic import AnthropicAdapter
from vibe.core.llm.backend.base import PreparedRequest
from vibe.core.types import AvailableTool, LLMMessage, StrToolChoice


def get_vertex_access_token() -> str:

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return credentials.token


def build_vertex_base_url(region: str) -> str:
    if region == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{region}-aiplatform.googleapis.com"


def build_vertex_endpoint(
    region: str, project_id: str, model: str, streaming: bool = False
) -> str:
    action = "streamRawPredict" if streaming else "rawPredict"
    return (
        f"/v1/projects/{project_id}/locations/{region}/"
        f"publishers/anthropic/models/{model}:{action}"
    )


class VertexAnthropicAdapter(AnthropicAdapter):
    """Vertex AI adapter — inherits all streaming/parsing from AnthropicAdapter."""

    endpoint: ClassVar[str] = ""
    # Vertex AI doesn't support beta features
    BETA_FEATURES: ClassVar[str] = ""

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
        project_id = provider.project_id
        region = provider.region

        if not project_id:
            raise ValueError("project_id is required in provider config for Vertex AI")
        if not region:
            raise ValueError("region is required in provider config for Vertex AI")

        system_prompt, converted_messages = self._mapper.prepare_messages(messages)
        converted_tools = self._mapper.prepare_tools(tools)
        converted_tool_choice = self._mapper.prepare_tool_choice(tool_choice)

        # Build vertex-specific payload (no "model" key, uses anthropic_version)
        payload: dict[str, Any] = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": converted_messages,
        }
        self._apply_thinking_config(
            payload,
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
        )

        if system_blocks := self._build_system_blocks(system_prompt):
            payload["system"] = system_blocks

        if converted_tools:
            payload["tools"] = converted_tools

        if converted_tool_choice:
            payload["tool_choice"] = converted_tool_choice

        if enable_streaming:
            payload["stream"] = True

        self._add_cache_control_to_last_user_message(converted_messages)

        access_token = get_vertex_access_token()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": self.BETA_FEATURES,
        }

        endpoint = build_vertex_endpoint(
            region, project_id, model_name, streaming=enable_streaming
        )
        base_url = build_vertex_base_url(region)

        body = json.dumps(payload).encode("utf-8")
        return PreparedRequest(endpoint, headers, body, base_url=base_url)
