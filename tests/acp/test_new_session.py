from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig


@pytest.fixture
def acp_agent_loop(backend) -> VibeAcpAgentLoop:
    config = build_test_vibe_config(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest", provider="mistral", alias="devstral-latest"
            ),
            ModelConfig(
                name="devstral-small", provider="mistral", alias="devstral-small"
            ),
        ],
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()

    return _create_acp_agent()


class TestACPNewSession:
    @pytest.mark.asyncio
    async def test_new_session_response_structure(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        assert session_response.session_id is not None
        acp_session = next(
            (
                s
                for s in acp_agent_loop.sessions.values()
                if s.id == session_response.session_id
            ),
            None,
        )
        assert acp_session is not None
        assert (
            acp_session.agent_loop.session_logger.session_id
            == session_response.session_id
        )

        assert session_response.session_id == acp_session.agent_loop.session_id

        assert session_response.models is not None
        assert session_response.models.current_model_id is not None
        assert session_response.models.available_models is not None
        assert len(session_response.models.available_models) == 2

        assert session_response.models.current_model_id == "devstral-latest"
        assert session_response.models.available_models[0].model_id == "devstral-latest"
        assert session_response.models.available_models[0].name == "devstral-latest"
        assert session_response.models.available_models[1].model_id == "devstral-small"
        assert session_response.models.available_models[1].name == "devstral-small"

        assert session_response.modes is not None
        assert session_response.modes.current_mode_id is not None
        assert session_response.modes.available_modes is not None
        assert len(session_response.modes.available_modes) == 4

        assert session_response.modes.current_mode_id == BuiltinAgentName.DEFAULT
        # Check that all primary agents are available (order may vary)
        mode_ids = {m.id for m in session_response.modes.available_modes}
        assert mode_ids == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }

    @pytest.mark.skip(reason="TODO: Fix this test")
    @pytest.mark.asyncio
    async def test_new_session_preserves_model_after_set_model(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        assert session_response.models is not None
        assert session_response.models.current_model_id == "devstral-latest"

        response = await acp_agent_loop.set_session_model(
            session_id=session_id, model_id="devstral-small"
        )
        assert response is not None

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        assert session_response.models is not None
        assert session_response.models.current_model_id == "devstral-small"
