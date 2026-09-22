"""Vertex AI (Anthropic) adapter.

``google.auth`` is not a hard dependency of the Runtime, so this module is only
imported when a provider selects the ``vertex-anthropic`` api style.
"""

# google-auth is an optional, lazily-imported dependency for the Vertex path.
# pyright: reportMissingImports=false

import json
import threading
from collections.abc import Sequence
from typing import Any, ClassVar

import google.auth
import google.auth.credentials
from google.auth.transport.requests import Request

from mistralai_vibe_local_harness.vibe.adapters.generic._anthropic import AnthropicAdapter
from mistralai_vibe_local_harness.vibe.adapters.generic._base import PreparedRequest
from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    AvailableTool,
    LLMMessage,
    StrToolChoice,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView


def build_vertex_base_url(region: str) -> str:
    if region == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{region}-aiplatform.googleapis.com"


def build_vertex_endpoint(region: str, project_id: str, model: str, streaming: bool = False) -> str:
    action = "streamRawPredict" if streaming else "rawPredict"
    return (
        f"/v1/projects/{project_id}/locations/{region}/publishers/anthropic/models/{model}:{action}"
    )


class VertexCredentials:
    def __init__(self) -> None:
        self._credentials: google.auth.credentials.Credentials | None = None
        self._lock = threading.Lock()

    @property
    def access_token(self) -> str:
        with self._lock:
            creds = self._credentials
            if creds is None:
                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                self._credentials = creds
            if not creds.valid:
                creds.refresh(Request())
            if creds.token is None:
                raise RuntimeError("Vertex AI credential refresh did not produce a token")
            return creds.token


# The adapter is built per request, so the access token is cached out here to keep
# credential refreshes process-wide rather than once per call.
_CREDENTIALS = VertexCredentials()


class VertexAnthropicAdapter(AnthropicAdapter):
    """Vertex AI adapter — inherits all streaming/parsing from AnthropicAdapter."""

    endpoint: ClassVar[str] = ""
    BETA_FEATURES: ClassVar[str] = ""

    def __init__(self) -> None:
        super().__init__()
        self.credentials = _CREDENTIALS

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
        project_id = provider.project_id
        region = provider.region

        if not project_id:
            raise ValueError("project_id is required in provider config for Vertex AI")
        if not region:
            raise ValueError("region is required in provider config for Vertex AI")

        system_prompt, converted_messages = self._mapper.prepare_messages(messages)
        converted_tools = self._mapper.prepare_tools(tools)
        converted_tool_choice = self._mapper.prepare_tool_choice(tool_choice)

        payload: dict[str, Any] = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": converted_messages,
        }
        self._apply_thinking_config(
            payload,
            messages=converted_messages,
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

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.credentials.access_token}",
            "anthropic-beta": self.BETA_FEATURES,
        }

        endpoint = build_vertex_endpoint(region, project_id, model_name, streaming=enable_streaming)
        base_url = build_vertex_base_url(region)

        body = json.dumps(payload).encode("utf-8")
        return PreparedRequest(endpoint, headers, body, base_url=base_url)


__all__ = [
    "VertexAnthropicAdapter",
    "VertexCredentials",
    "build_vertex_base_url",
    "build_vertex_endpoint",
]
