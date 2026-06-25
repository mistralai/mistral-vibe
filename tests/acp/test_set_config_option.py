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
from vibe.core.middleware import TurnLimitMiddleware


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
        assert len(response.config_options) == 3
        assert (
            acp_session.agent_loop.agent_profile.name == BuiltinAgentName.AUTO_APPROVE
        )
        assert acp_session.agent_loop.bypass_tool_permissions is True

        # Verify config_options reflect the new state
        mode_config = response.config_options[0]
        assert mode_config.id == "mode"
        assert mode_config.current_value == BuiltinAgentName.AUTO_APPROVE

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
    async def test_set_config_option_mode_to_chat(
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
            session_id=session_id, config_id="mode", value=BuiltinAgentName.CHAT
        )

        assert response is not None
        assert response.config_options is not None
        assert len(response.config_options) == 3
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.CHAT
        assert (
            acp_session.agent_loop.bypass_tool_permissions is True
        )  # Chat mode auto-approves read-only tools

        mode_config = response.config_options[0]
        assert mode_config.id == "mode"
        assert mode_config.current_value == BuiltinAgentName.CHAT

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
        assert len(response.config_options) == 3
        assert acp_session.agent_loop.config.active_model == "devstral-small"

        # Verify config_options reflect the new state
        model_config = response.config_options[1]
        assert model_config.id == "model"
        assert model_config.current_value == "devstral-small"

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


class TestACPSetConfigOptionThinking:
    @pytest.mark.asyncio
    async def test_set_config_option_thinking_success(
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
        assert acp_session.agent_loop.config.get_active_model().thinking == "off"

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="thinking", value="high"
        )

        assert response is not None
        assert response.config_options is not None
        assert len(response.config_options) == 3
        assert acp_session.agent_loop.config.get_active_model().thinking == "high"

        thinking_config = response.config_options[2]
        assert thinking_config.id == "thinking"
        assert thinking_config.current_value == "high"

    @pytest.mark.asyncio
    async def test_set_config_option_thinking_all_levels(
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

        for level in ["low", "medium", "high", "max", "off"]:
            response = await acp_agent_loop.set_config_option(
                session_id=session_id, config_id="thinking", value=level
            )
            assert response is not None
            assert acp_session.agent_loop.config.get_active_model().thinking == level

    @pytest.mark.asyncio
    async def test_set_config_option_thinking_invalid_returns_none(
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
            session_id=session_id, config_id="thinking", value="ultra"
        )

        assert response is None
        assert acp_session.agent_loop.config.get_active_model().thinking == "off"

    @pytest.mark.asyncio
    async def test_set_config_option_thinking_empty_string_returns_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="thinking", value=""
        )

        assert response is None


class TestACPSetConfigOptionMaxTurns:
    @pytest.mark.asyncio
    async def test_set_config_option_max_turns_success(
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
            session_id=session_id, config_id="max_turns", value="100"
        )

        assert response is not None
        assert acp_session.agent_loop._max_turns == 100
        turn_limits = [
            m
            for m in acp_session.agent_loop.middleware_pipeline.middlewares
            if isinstance(m, TurnLimitMiddleware)
        ]
        assert len(turn_limits) == 1
        assert turn_limits[0].max_turns == 100

    @pytest.mark.asyncio
    async def test_set_config_option_max_turns_string_value(
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
            session_id=session_id, config_id="max_turns", value="50"
        )

        assert response is not None
        assert acp_session.agent_loop._max_turns == 50
        turn_limits = [
            m
            for m in acp_session.agent_loop.middleware_pipeline.middlewares
            if isinstance(m, TurnLimitMiddleware)
        ]
        assert len(turn_limits) == 1
        assert turn_limits[0].max_turns == 50

    @pytest.mark.asyncio
    async def test_set_config_option_max_turns_invalid_string_returns_none(
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
        initial_max_turns = acp_session.agent_loop._max_turns

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_turns", value="abc"
        )

        assert response is None
        assert acp_session.agent_loop._max_turns == initial_max_turns

    @pytest.mark.asyncio
    async def test_set_config_option_max_turns_bool_returns_none(
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
        initial_max_turns = acp_session.agent_loop._max_turns

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_turns", value=True
        )

        assert response is None
        assert acp_session.agent_loop._max_turns == initial_max_turns

    @pytest.mark.asyncio
    async def test_set_config_option_max_turns_repeated_set(
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

        await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_turns", value="100"
        )
        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_turns", value="200"
        )

        assert response is not None
        assert acp_session.agent_loop._max_turns == 200
        turn_limits = [
            m
            for m in acp_session.agent_loop.middleware_pipeline.middlewares
            if isinstance(m, TurnLimitMiddleware)
        ]
        assert len(turn_limits) == 1
        assert turn_limits[0].max_turns == 200


class TestACPSetConfigOptionMaxTokens:
    @pytest.mark.asyncio
    async def test_set_config_option_max_tokens_success(
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
            session_id=session_id, config_id="max_tokens", value="8192"
        )

        assert response is not None
        assert acp_session.agent_loop._max_tokens == 8192

    @pytest.mark.asyncio
    async def test_set_config_option_max_tokens_invalid_string_returns_none(
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
        initial_max_tokens = acp_session.agent_loop._max_tokens

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_tokens", value="abc"
        )

        assert response is None
        assert acp_session.agent_loop._max_tokens == initial_max_tokens

    @pytest.mark.asyncio
    async def test_set_config_option_max_tokens_bool_returns_none(
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
        initial_max_tokens = acp_session.agent_loop._max_tokens

        response = await acp_agent_loop.set_config_option(
            session_id=session_id, config_id="max_tokens", value=True
        )

        assert response is None
        assert acp_session.agent_loop._max_tokens == initial_max_tokens
