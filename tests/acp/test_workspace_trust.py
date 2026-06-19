from __future__ import annotations

from pathlib import Path

import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError, SessionNotFoundError
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig
from vibe.core.trusted_folders import trusted_folders_manager


async def _system_prompt(acp_agent_loop: VibeAcpAgentLoop, session_id: str) -> str:
    session = acp_agent_loop.sessions[session_id]
    await session.agent_loop.wait_until_ready()
    return session.agent_loop.messages[0].content or ""


@pytest.fixture
def acp_agent_loop(
    backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> VibeAcpAgentLoop:
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

    monkeypatch.setattr("vibe.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    return _create_acp_agent()


class TestWorkspaceTrustExtMethods:
    @pytest.mark.asyncio
    async def test_workspace_trust_status_returns_details_even_after_decline(
        self, acp_agent_loop: VibeAcpAgentLoop, tmp_working_directory: Path
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Trust me later", encoding="utf-8"
        )
        trusted_folders_manager.add_untrusted(tmp_working_directory)

        response = await acp_agent_loop.ext_method(
            "trust/status", {"cwd": str(tmp_working_directory)}
        )

        assert response == {
            "trust_status": "untrusted",
            "details": {
                "cwd": str(tmp_working_directory.resolve()),
                "repoRoot": None,
                "ignoredFiles": ["AGENTS.md"],
                "availableDecisions": ["trust_cwd", "trust_session", "decline"],
            },
        }

    @pytest.mark.asyncio
    async def test_workspace_trust_decision_trusts_session_and_reloads_session(
        self, acp_agent_loop: VibeAcpAgentLoop, tmp_working_directory: Path
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Reloaded session instructions", encoding="utf-8"
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None
        assert "Reloaded session instructions" not in await _system_prompt(
            acp_agent_loop, session_response.session_id
        )

        response = await acp_agent_loop.ext_method(
            "trust/decision",
            {
                "cwd": str(tmp_working_directory),
                "decision": "trust_session",
                "session_id": session_response.session_id,
            },
        )

        assert response == {"trust_status": "session", "details": None}
        normalized = str(tmp_working_directory.resolve())
        assert normalized in trusted_folders_manager._session_trusted
        assert normalized not in trusted_folders_manager._trusted
        assert "Reloaded session instructions" in await _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_workspace_trust_decision_rejects_unavailable_decision(
        self, acp_agent_loop: VibeAcpAgentLoop, tmp_working_directory: Path
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "No repo decision here", encoding="utf-8"
        )

        with pytest.raises(InvalidRequestError):
            await acp_agent_loop.ext_method(
                "trust/decision",
                {"cwd": str(tmp_working_directory), "decision": "trust_repo"},
            )

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None

    @pytest.mark.asyncio
    async def test_workspace_trust_decision_rejects_unknown_session_id(
        self, acp_agent_loop: VibeAcpAgentLoop, tmp_working_directory: Path
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Unknown session", encoding="utf-8"
        )

        with pytest.raises(SessionNotFoundError):
            await acp_agent_loop.ext_method(
                "trust/decision",
                {
                    "cwd": str(tmp_working_directory),
                    "decision": "trust_session",
                    "session_id": "missing-session",
                },
            )

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
