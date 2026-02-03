from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


@pytest.fixture
def backend() -> FakeBackend:
    backend = FakeBackend(
        LLMChunk(
            message=LLMMessage(role=Role.assistant, content="Hi"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )
    )
    return backend


def _create_acp_agent() -> VibeAcpAgentLoop:
    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()

    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent  # pyright: ignore[reportReturnType]


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgentLoop:
    class PatchedAgent(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs, backend=backend)

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgent).start()
    return _create_acp_agent()
