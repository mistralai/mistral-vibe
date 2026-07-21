from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError, SessionNotFoundError
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig
from vibe.core.trusted_folders import trusted_folders_manager


async def _system_prompt(acp_agent_loop: VibeAcpAgentLoop, session_id: str) -> str:
    session = acp_agent_loop.sessions[session_id]
    await session.agent_loop.wait_until_ready()
    return session.agent_loop.messages[0].content or ""


async def _wait_for_background_tasks(
    acp_agent_loop: VibeAcpAgentLoop, session_id: str
) -> None:
    session = acp_agent_loop.sessions[session_id]
    while session._tasks:
        await asyncio.gather(*list(session._tasks))


@pytest.fixture
def acp_agent_loop(
    backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> VibeAcpAgentLoop:
    config = build_test_vibe_config(
        active_model="devstral-latest",
        include_project_context=True,
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
            kwargs["config_orchestrator"] = FakeConfigOrchestrator(config)
            super().__init__(*args, **{**kwargs, "backend": backend})
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
                "availableDecisions": ["trust_cwd", "decline"],
            },
        }

    @pytest.mark.asyncio
    async def test_workspace_trust_decision_trusts_cwd_and_reloads_session(
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
                "decision": "trust_cwd",
                "session_id": session_response.session_id,
            },
        )

        assert response == {"trust_status": "trusted", "details": None}
        normalized = str(tmp_working_directory.resolve())
        assert normalized not in trusted_folders_manager._session_trusted
        assert normalized in trusted_folders_manager._trusted
        await _wait_for_background_tasks(acp_agent_loop, session_response.session_id)
        assert "Reloaded session instructions" in await _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_workspace_trust_decision_returns_before_reload_completes(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Slow reload instructions", encoding="utf-8"
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        reload_started = asyncio.Event()
        release_reload = asyncio.Event()

        async def slow_reload_session_config(session) -> None:
            reload_started.set()
            await release_reload.wait()

        monkeypatch.setattr(
            acp_agent_loop, "_reload_session_config", slow_reload_session_config
        )

        decision_task = asyncio.create_task(
            acp_agent_loop.ext_method(
                "trust/decision",
                {
                    "cwd": str(tmp_working_directory),
                    "decision": "trust_cwd",
                    "session_id": session_response.session_id,
                },
            )
        )

        try:
            await asyncio.wait_for(reload_started.wait(), timeout=1)
            await asyncio.sleep(0)

            assert decision_task.done()
            assert decision_task.result() == {
                "trust_status": "trusted",
                "details": None,
            }
        finally:
            release_reload.set()
            await decision_task
            await _wait_for_background_tasks(
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

        with pytest.raises(InvalidRequestError):
            await acp_agent_loop.ext_method(
                "trust/decision",
                {"cwd": str(tmp_working_directory), "decision": "trust_session"},
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
                    "decision": "trust_cwd",
                    "session_id": "missing-session",
                },
            )

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
