from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, VibeConfig


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


class TestACPSetConfigOptionMode:
    @pytest.mark.asyncio
    async def test_set_config_option_mode_to_auto_approve(
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
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="mode", value=BuiltinAgentName.AUTO_APPROVE
        )

        assert response is not None
        assert response.config_options is not None
        assert len(response.config_options) == 2
        assert (
            acp_session.agent_loop.agent_profile.name == BuiltinAgentName.AUTO_APPROVE
        )
        assert acp_session.agent_loop.auto_approve is True

        # Verify config_options reflect the new state
        mode_config = response.config_options[0]
        assert mode_config.root.id == "mode"
        assert mode_config.root.current_value == BuiltinAgentName.AUTO_APPROVE

    @pytest.mark.asyncio
    async def test_set_config_option_mode_to_plan(
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

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="mode", value=BuiltinAgentName.PLAN
        )

        assert response is not None
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.PLAN

    @pytest.mark.asyncio
    async def test_set_config_option_mode_invalid_returns_none(
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
        initial_mode = acp_session.agent_loop.agent_profile.name

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="mode", value="invalid-mode"
        )

        assert response is None
        assert acp_session.agent_loop.agent_profile.name == initial_mode

    @pytest.mark.asyncio
    async def test_set_config_option_mode_empty_string_returns_none(
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
        initial_mode = acp_session.agent_loop.agent_profile.name

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="mode", value=""
        )

        assert response is None
        assert acp_session.agent_loop.agent_profile.name == initial_mode


class TestACPSetConfigOptionModel:
    @pytest.mark.asyncio
    async def test_set_config_option_model_success(
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
        assert acp_session.agent_loop.config.active_model == "devstral-latest"

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="model", value="devstral-small"
        )

        assert response is not None
        assert response.config_options is not None
        assert len(response.config_options) == 2
        assert acp_session.agent_loop.config.active_model == "devstral-small"

        # Verify config_options reflect the new state
        model_config = response.config_options[1]
        assert model_config.root.id == "model"
        assert model_config.root.current_value == "devstral-small"

    @pytest.mark.asyncio
    async def test_set_config_option_model_invalid_returns_none(
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

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="model", value="non-existent-model"
        )

        assert response is None
        assert acp_session.agent_loop.config.active_model == initial_model

    @pytest.mark.asyncio
    async def test_set_config_option_model_empty_string_returns_none(
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

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="model", value=""
        )

        assert response is None
        assert acp_session.agent_loop.config.active_model == initial_model

    @pytest.mark.asyncio
    async def test_set_config_option_model_saves_to_config(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        with patch("vibe.acp.acp_agent_loop.VibeConfig.save_updates") as mock_save:
            response = await acp_agent_loop.set_config_option(
                session_id=session_id, config_id="model", value="devstral-small"
            )

            assert response is not None
            mock_save.assert_called_once_with({"active_model": "devstral-small"})

    @pytest.mark.asyncio
    async def test_set_config_option_model_does_not_save_on_invalid(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        with patch("vibe.acp.acp_agent_loop.VibeConfig.save_updates") as mock_save:
            response = await acp_agent_loop.set_config_option(
                session_id=session_id, config_id="model", value="non-existent-model"
            )

            assert response is None
            mock_save.assert_not_called()


class TestACPSetConfigOptionInvalidConfigId:
    @pytest.mark.asyncio
    async def test_set_config_option_invalid_config_id_returns_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="invalid_config", value="some_value"
        )

        assert response is None

    @pytest.mark.asyncio
    async def test_set_config_option_empty_config_id_returns_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="", value="some_value"
        )

        assert response is None
