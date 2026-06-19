from __future__ import annotations

from collections.abc import Iterator
import json
from typing import Any

import httpx
import pytest
import respx

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.constants import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MESSAGES_PATH,
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
    OPENAI_BASE_URL,
    OPENAI_RESPONSES_PATH,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.types import AssistantEvent, Backend, BaseEvent


def assistant_text(events: list[BaseEvent]) -> str:
    return "".join(
        e.content for e in events if isinstance(e, AssistantEvent) and e.content
    ).strip()


@pytest.fixture(autouse=True)
def _git_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Restrict git to the local file protocol so no test can fetch/push over the
    # network, even if a remote is misconfigured to a real URL.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")


def e2e_config(**overrides: Any) -> VibeConfig:
    # Defaults give the mistral provider/model (api.mistral.ai, the respx-mocked
    # base). The default model reasons, but that only adds reasoning_effort to the
    # request, which the mocked responses ignore.
    # The e2e harness mocks the connector bootstrap endpoint via respx, so
    # connector discovery is enabled (and fast) here unlike the unit-test default.
    return build_test_vibe_config(
        enabled_tools=overrides.pop("enabled_tools", []),
        enable_connectors=overrides.pop("enable_connectors", True),
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
        **overrides,
    )


def _generic_e2e_config(
    *, name: str, api_base: str, api_style: str, model: str, **overrides: Any
) -> VibeConfig:
    provider = ProviderConfig(
        name=name,
        api_base=api_base,
        api_key_env_var=f"{name.upper()}_API_KEY",
        api_style=api_style,
        backend=Backend.GENERIC,
    )
    models = [ModelConfig(name=model, provider=name, alias=name)]
    return e2e_config(
        active_model=name, models=models, providers=[provider], **overrides
    )


def anthropic_e2e_config(**overrides: Any) -> VibeConfig:
    return _generic_e2e_config(
        name="anthropic",
        api_base=ANTHROPIC_BASE_URL,
        api_style="anthropic",
        model="claude-test",
        **overrides,
    )


def openai_responses_e2e_config(**overrides: Any) -> VibeConfig:
    return _generic_e2e_config(
        name="openai",
        api_base=f"{OPENAI_BASE_URL}/v1",
        api_style="openai-responses",
        model="gpt-test",
        **overrides,
    )


@pytest.fixture
def mock_mistral() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=MISTRAL_BASE_URL, assert_all_called=False) as router:
        router.get(CONNECTORS_BOOTSTRAP_PATH).mock(
            return_value=httpx.Response(200, json={"connectors": []})
        )
        yield router


def build_e2e_agent_loop(
    *, config: VibeConfig | None = None, enable_streaming: bool = False, **kwargs: Any
) -> AgentLoop:
    resolved_config = config or e2e_config()
    provider = resolved_config.providers[0]
    # todo now we use build_test_agent_loop for the fake mcp registry.
    # Maybe mock http tool calls and instantiate a real registry later for more coverage
    return build_test_agent_loop(
        config=resolved_config,
        agent_name=kwargs.pop("agent_name", BuiltinAgentName.AUTO_APPROVE),
        backend=BACKEND_FACTORY[provider.backend](provider=provider),
        enable_streaming=enable_streaming,
        **kwargs,
    )


class ProviderAPI:
    """Stubs a provider's completion wire; the AgentLoop stays the subject."""

    def __init__(self, router: respx.MockRouter, path: str) -> None:
        self.route = router.post(path)

    def reply(self, *completions: dict[str, Any]) -> None:
        responses = [httpx.Response(200, json=c) for c in completions]
        if len(responses) == 1:
            self.route.mock(return_value=responses[0])
        else:
            self.route.mock(side_effect=responses)

    def reply_stream(self, chunks: list[bytes]) -> None:
        self.route.mock(return_value=self._stream_response(chunks))

    def reply_streams(self, *chunk_lists: list[bytes]) -> None:
        self.route.mock(
            side_effect=[self._stream_response(chunks) for chunks in chunk_lists]
        )

    @staticmethod
    def _stream_response(chunks: list[bytes]) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
            headers={"Content-Type": "text/event-stream"},
        )

    @property
    def request_json(self) -> dict[str, Any]:
        return json.loads(self.route.calls.last.request.content)


class MistralAPI(ProviderAPI):
    def __init__(self, router: respx.MockRouter) -> None:
        super().__init__(router, CHAT_COMPLETIONS_PATH)


@pytest.fixture
def mistral_api(mock_mistral: respx.MockRouter) -> MistralAPI:
    return MistralAPI(mock_mistral)


@pytest.fixture
def mock_anthropic() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=ANTHROPIC_BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def anthropic_api(mock_anthropic: respx.MockRouter) -> ProviderAPI:
    return ProviderAPI(mock_anthropic, ANTHROPIC_MESSAGES_PATH)


@pytest.fixture
def mock_openai() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=OPENAI_BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def openai_responses_api(mock_openai: respx.MockRouter) -> ProviderAPI:
    return ProviderAPI(mock_openai, OPENAI_RESPONSES_PATH)
