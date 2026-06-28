from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx

from tests.agent_loop.e2e.providers import MistralAPI
from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY


@pytest.fixture(autouse=True)
def _git_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Restrict git to the local file protocol so no test can fetch/push over the
    # network, even if a remote is misconfigured to a real URL.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")


@pytest.fixture
def mock_mistral() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=MistralAPI.base_url, assert_all_called=False) as router:
        yield router


@pytest.fixture
def mistral_api(mock_mistral: respx.MockRouter) -> MistralAPI:
    api = MistralAPI()
    api.setup_router(mock_mistral)
    return api


def build_e2e_agent_loop(
    *, config: VibeConfig | None = None, enable_streaming: bool = False, **kwargs: Any
) -> AgentLoop:
    resolved_config = config or build_test_vibe_config()
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
