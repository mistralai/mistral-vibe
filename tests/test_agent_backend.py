from __future__ import annotations

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(session_logging=SessionLoggingConfig(enabled=False))


@pytest.mark.asyncio
async def test_passes_x_affinity_header_when_asking_an_answer(vibe_config: VibeConfig):
    backend = FakeBackend([mock_llm_chunk(content="Response", finish_reason="stop")])
    agent = Agent(vibe_config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_extra_headers) > 0
    headers = backend.requests_extra_headers[0]
    assert headers is not None
    assert "x-affinity" in headers
    assert headers["x-affinity"] == agent.session_id


@pytest.mark.asyncio
async def test_passes_x_affinity_header_when_asking_an_answer_streaming(
    vibe_config: VibeConfig,
):
    backend = FakeBackend([mock_llm_chunk(content="Response", finish_reason="stop")])
    agent = Agent(vibe_config, backend=backend, enable_streaming=True)

    [_ async for _ in agent.act("Hello")]

    assert len(backend.requests_extra_headers) > 0
    headers = backend.requests_extra_headers[0]
    assert headers is not None
    assert "x-affinity" in headers
    assert headers["x-affinity"] == agent.session_id


@pytest.mark.asyncio
async def test_updates_tokens_stats_based_on_backend_response(vibe_config: VibeConfig):
    chunk = mock_llm_chunk(
        content="Response",
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=50,
    )
    backend = FakeBackend([chunk])
    agent = Agent(vibe_config, backend=backend)

    [_ async for _ in agent.act("Hello")]

    assert agent.stats.context_tokens == 150


@pytest.mark.asyncio
async def test_updates_tokens_stats_based_on_backend_response_streaming(
    vibe_config: VibeConfig,
):
    final_chunk = mock_llm_chunk(
        content="Complete",
        finish_reason="stop",
        prompt_tokens=200,
        completion_tokens=75,
    )
    backend = FakeBackend([final_chunk])
    agent = Agent(vibe_config, backend=backend, enable_streaming=True)

    [_ async for _ in agent.act("Hello")]

    assert agent.stats.context_tokens == 275
