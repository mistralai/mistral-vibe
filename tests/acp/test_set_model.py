from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, VibeConfig
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def acp_agent_loop(backend) -> VibeAcpAgentLoop:
    config = build_test_vibe_config(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest",
                provider="mistral",
                alias="devstral-latest",
                input_price=0.4,
                output_price=2.0,
            ),
            ModelConfig(
                name="devstral-small",
                provider="mistral",
                alias="devstral-small",
                input_price=0.1,
                output_price=0.3,
            ),
        ],
    )

    VibeConfig.dump_config(config.model_dump())

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()
            try:
                active_model = config.get_active_model()
                self.stats.input_price_per_million = active_model.input_price
                self.stats.output_price_per_million = active_model.output_price
            except ValueError:
                pass

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()

    return _create_acp_agent()


class TestACPSetModel:
    @pytest.mark.asyncio
    async def test_set_model_success(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None
        assert acp_session.agent_loop.config.active_model == "devstral-latest"

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="devstral-small"
        )

        assert response is not None
        assert acp_session.agent_loop.config.active_model == "devstral-small"

    @pytest.mark.asyncio
    async def test_set_model_invalid_model_returns_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None
        initial_model = acp_session.agent_loop.config.active_model

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="non-existent-model"
        )

        assert response is None
        assert acp_session.agent_loop.config.active_model == initial_model

    @pytest.mark.asyncio
    async def test_set_model_to_same_model(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        initial_model = "devstral-latest"
        assert acp_session is not None
        assert acp_session.agent_loop.config.active_model == initial_model

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id=initial_model
        )

        assert response is not None
        assert acp_session.agent_loop.config.active_model == initial_model

    @pytest.mark.asyncio
    async def test_set_model_saves_to_config(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        with patch("vibe.acp.acp_agent_loop.VibeConfig.save_updates") as mock_save:
            response = await acp_agent_loop.set_session_model(
                session_id=session_id, model_id="devstral-small"
            )

            assert response is not None
            mock_save.assert_called_once_with({"active_model": "devstral-small"})

    @pytest.mark.asyncio
    async def test_set_model_does_not_save_on_invalid_model(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        with patch("vibe.acp.acp_agent_loop.VibeConfig.save_updates") as mock_save:
            response = await acp_agent_loop.set_session_model(
                session_id=session_id, model_id="non-existent-model"
            )

            assert response is None
            mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_model_with_empty_string(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_model = acp_session.agent_loop.config.active_model

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id=""
        )

        assert response is None
        assert acp_session.agent_loop.config.active_model == initial_model

    @pytest.mark.asyncio
    async def test_set_model_updates_active_model(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None
        assert (
            acp_session.agent_loop.config.get_active_model().alias == "devstral-latest"
        )

        await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="devstral-small"
        )

        assert (
            acp_session.agent_loop.config.get_active_model().alias == "devstral-small"
        )

    @pytest.mark.asyncio
    async def test_set_model_calls_reload_with_initial_messages(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        with patch.object(
            acp_session.agent_loop, "reload_with_initial_messages"
        ) as mock_reload:
            response = await acp_agent_loop.set_session_model(
                session_id=session_id, model_id="devstral-small"
            )

            assert response is not None
            mock_reload.assert_called_once()
            call_args = mock_reload.call_args
            assert call_args.kwargs["base_config"] is not None
            assert call_args.kwargs["base_config"].active_model == "devstral-small"

    @pytest.mark.asyncio
    async def test_set_model_preserves_conversation_history(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        user_msg = LLMMessage(role=Role.user, content="Hello")
        assistant_msg = LLMMessage(role=Role.assistant, content="Hi there!")
        acp_session.agent_loop.messages.append(user_msg)
        acp_session.agent_loop.messages.append(assistant_msg)

        assert len(acp_session.agent_loop.messages) == 3

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="devstral-small"
        )

        assert response is not None
        assert len(acp_session.agent_loop.messages) == 3
        assert acp_session.agent_loop.messages[0].role == Role.system
        assert acp_session.agent_loop.messages[1].content == "Hello"
        assert acp_session.agent_loop.messages[2].content == "Hi there!"

    @pytest.mark.asyncio
    async def test_set_model_resets_stats_with_new_model_pricing(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_model = acp_session.agent_loop.config.get_active_model()
        initial_input_price = initial_model.input_price
        initial_output_price = initial_model.output_price

        initial_stats_input = acp_session.agent_loop.stats.input_price_per_million
        initial_stats_output = acp_session.agent_loop.stats.output_price_per_million

        assert (
            acp_session.agent_loop.stats.input_price_per_million == initial_input_price
        )
        assert (
            acp_session.agent_loop.stats.output_price_per_million
            == initial_output_price
        )

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="devstral-small"
        )

        assert response is not None

        new_model = acp_session.agent_loop.config.get_active_model()
        new_input_price = new_model.input_price
        new_output_price = new_model.output_price

        assert new_input_price != initial_input_price
        assert new_output_price != initial_output_price

        assert acp_session.agent_loop.stats.input_price_per_million == new_input_price
        assert acp_session.agent_loop.stats.output_price_per_million == new_output_price

        assert (
            acp_session.agent_loop.stats.input_price_per_million != initial_stats_input
        )
        assert (
            acp_session.agent_loop.stats.output_price_per_million
            != initial_stats_output
        )
