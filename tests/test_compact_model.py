from __future__ import annotations

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import (
    ModelConfig,
    SessionLoggingConfig,
    VibeConfig,
)


class TestGetCompactModel:
    def test_returns_active_model_when_compact_model_empty(self) -> None:
        cfg = VibeConfig(
            compact_model="",
            active_model="devstral-2",
            models=[
                ModelConfig(
                    name="devstral-latest", provider="mistral", alias="devstral-2"
                ),
            ],
        )
        result = cfg.get_compact_model()
        assert result.alias == "devstral-2"

    def test_returns_active_model_when_compact_model_not_set(self) -> None:
        cfg = VibeConfig(
            active_model="devstral-2",
            models=[
                ModelConfig(
                    name="devstral-latest", provider="mistral", alias="devstral-2"
                ),
            ],
        )
        result = cfg.get_compact_model()
        assert result.alias == "devstral-2"

    def test_returns_compact_model_when_set(self) -> None:
        cfg = VibeConfig(
            compact_model="devstral-small",
            models=[
                ModelConfig(
                    name="devstral-small-latest",
                    provider="mistral",
                    alias="devstral-small",
                ),
                ModelConfig(
                    name="devstral-latest", provider="mistral", alias="devstral-2"
                ),
            ],
        )
        result = cfg.get_compact_model()
        assert result.alias == "devstral-small"
        assert result.name == "devstral-small-latest"

    def test_falls_back_to_active_model_when_compact_model_not_found(self) -> None:
        cfg = VibeConfig(
            compact_model="nonexistent-model",
            active_model="devstral-2",
            models=[
                ModelConfig(
                    name="devstral-latest", provider="mistral", alias="devstral-2"
                ),
            ],
        )
        result = cfg.get_compact_model()
        assert result.alias == "devstral-2"


class TestSelectCompactBackend:
    def test_returns_main_backend_when_compact_model_empty(self) -> None:
        backend = FakeBackend([mock_llm_chunk(content="test")])
        cfg = VibeConfig(session_logging=SessionLoggingConfig(enabled=False))
        agent = Agent(cfg, backend=backend)
        assert agent._compact_backend is agent.backend

    def test_creates_separate_backend_when_compact_model_set(self) -> None:
        backend = FakeBackend([mock_llm_chunk(content="test")])
        cfg = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            compact_model="devstral-small",
            models=[
                ModelConfig(
                    name="devstral-small-latest",
                    provider="mistral",
                    alias="devstral-small",
                ),
            ],
        )
        agent = Agent(cfg, backend=backend)
        assert agent._compact_backend is not agent.backend


class TestCompactUsesCompactModel:
    @pytest.mark.asyncio
    async def test_compact_uses_injected_backend(self) -> None:
        main_backend = FakeBackend([mock_llm_chunk(content="main")])
        compact_backend = FakeBackend([mock_llm_chunk(content="<summary>")])
        cfg = VibeConfig(session_logging=SessionLoggingConfig(enabled=False))
        agent = Agent(cfg, backend=main_backend)
        agent._compact_backend = compact_backend

        agent.messages.append(
            agent.messages[0].model_copy(update={"content": "user message"})
        )

        summary = await agent.compact()

        assert len(compact_backend.requests_messages) == 1
        assert len(main_backend.requests_messages) == 0

    @pytest.mark.asyncio
    async def test_compact_uses_compact_backend_when_configured(self) -> None:
        main_backend = FakeBackend([mock_llm_chunk(content="main")])
        compact_backend = FakeBackend([mock_llm_chunk(content="<compact_summary>")])

        cfg = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            compact_model="devstral-small",
            active_model="devstral-2",
            models=[
                ModelConfig(
                    name="devstral-small-latest",
                    provider="mistral",
                    alias="devstral-small",
                ),
                ModelConfig(
                    name="devstral-latest", provider="mistral", alias="devstral-2"
                ),
            ],
        )

        agent = Agent(cfg, backend=main_backend)
        agent._compact_backend = compact_backend

        agent.messages.append(
            agent.messages[0].model_copy(update={"content": "user message"})
        )

        summary = await agent.compact()

        assert len(compact_backend.requests_messages) == 1
        assert len(main_backend.requests_messages) == 0
        assert "<compact_summary>" in summary


class TestCompactFailure:
    @pytest.mark.asyncio
    async def test_compact_raises_when_compact_backend_fails(self) -> None:
        main_backend = FakeBackend(
            [], exception_to_raise=ConnectionError("server down")
        )

        cfg = VibeConfig(session_logging=SessionLoggingConfig(enabled=False))
        agent = Agent(cfg, backend=main_backend)

        agent.messages.append(
            agent.messages[0].model_copy(update={"content": "user message"})
        )

        with pytest.raises(RuntimeError, match="server down"):
            await agent.compact()

    @pytest.mark.asyncio
    async def test_compact_restores_messages_on_failure(self) -> None:
        """When compact fails, messages should be restored to original state."""
        compact_backend = FakeBackend(
            [], exception_to_raise=ConnectionError("compact server down")
        )

        cfg = VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            compact_model="devstral-small",
            models=[
                ModelConfig(
                    name="devstral-small-latest",
                    provider="mistral",
                    alias="devstral-small",
                ),
            ],
        )

        agent = Agent(cfg, backend=FakeBackend([mock_llm_chunk(content="main")]))
        agent._compact_backend = compact_backend

        # Add a user message to simulate conversation
        agent.messages.append(
            agent.messages[0].model_copy(update={"content": "user message"})
        )
        original_message_count = len(agent.messages)

        with pytest.raises(RuntimeError, match="compact server down"):
            await agent.compact()

        # Messages should be restored to original state
        assert len(agent.messages) == original_message_count
