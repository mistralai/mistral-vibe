from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from acp import RequestError
from acp.schema import ClientCapabilities
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from vibe.acp.acp_agent_loop import WORKSPACE_TRUST_CAPABILITY, VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig
from vibe.core.trusted_folders import trusted_folders_manager


def _system_prompt(acp_agent_loop: VibeAcpAgentLoop, session_id: str) -> str:
    session = acp_agent_loop.sessions[session_id]
    return session.agent_loop.messages[0].content or ""


def _enable_workspace_trust(acp_agent_loop: VibeAcpAgentLoop) -> None:
    acp_agent_loop.client_capabilities = ClientCapabilities(
        field_meta={WORKSPACE_TRUST_CAPABILITY: True}
    )


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
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
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

        # Telemetry now fires from the background warm-up worker once
        # `wait_until_ready` joins both MCP and experiments. Awaiting it here
        # forces emission before assertions.
        await acp_session.agent_loop.wait_until_ready()

        new_session_events = [
            e for e in telemetry_events if e.get("event_name") == "vibe.new_session"
        ]
        assert len(new_session_events) == 1
        assert new_session_events[0]["properties"]["entrypoint"] == "acp"
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
        assert len(session_response.modes.available_modes) == 5

        assert session_response.modes.current_mode_id == BuiltinAgentName.DEFAULT
        # Check that all primary agents are available (order may vary)
        mode_ids = {m.id for m in session_response.modes.available_modes}
        assert mode_ids == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.CHAT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }

        # Check config_options
        assert session_response.config_options is not None
        assert len(session_response.config_options) == 3

        # Mode config option
        mode_config = session_response.config_options[0]
        assert mode_config.id == "mode"
        assert mode_config.category == "mode"
        assert mode_config.current_value == BuiltinAgentName.DEFAULT
        assert len(mode_config.options) == 5
        mode_option_values = {opt.value for opt in mode_config.options}
        assert mode_option_values == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.CHAT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }

        # Model config option
        model_config = session_response.config_options[1]
        assert model_config.id == "model"
        assert model_config.category == "model"
        assert model_config.current_value == "devstral-latest"
        assert len(model_config.options) == 2
        model_option_values = {opt.value for opt in model_config.options}
        assert model_option_values == {"devstral-latest", "devstral-small"}

        # Thinking config option
        thinking_config = session_response.config_options[2]
        assert thinking_config.id == "thinking"
        assert thinking_config.category == "thinking"
        assert thinking_config.current_value == "off"
        assert len(thinking_config.options) == 5

    @pytest.mark.asyncio
    async def test_new_session_loads_root_agents_md_from_workspace_cwd(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Root project instructions", encoding="utf-8"
        )
        _enable_workspace_trust(acp_agent_loop)
        request_trust = AsyncMock(return_value={"decision": "trust_cwd"})
        monkeypatch.setattr(acp_agent_loop.client, "ext_method", request_trust)

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        request_trust.assert_awaited_once()
        await_args = request_trust.await_args
        assert await_args is not None
        method, params = await_args.args
        assert method == "trust/request"
        assert params["cwd"] == str(tmp_working_directory.resolve())
        assert params["detectedFiles"] == ["AGENTS.md"]
        assert params["repoDetectedFiles"] == []
        assert params["availableDecisions"] == ["trust_cwd", "trust_session", "decline"]
        assert trusted_folders_manager.is_trusted(tmp_working_directory) is True
        assert "Root project instructions" in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_can_trust_full_repo_from_subdirectory(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo = tmp_path / "repo"
        cwd = repo / "src" / "pkg"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        cwd.mkdir(parents=True)
        (repo / "AGENTS.md").write_text("Repo instructions", encoding="utf-8")
        _enable_workspace_trust(acp_agent_loop)
        request_trust = AsyncMock(return_value={"decision": "trust_repo"})
        monkeypatch.setattr(acp_agent_loop.client, "ext_method", request_trust)

        session_response = await acp_agent_loop.new_session(
            cwd=str(cwd), mcp_servers=[]
        )
        assert session_response.session_id is not None

        request_trust.assert_awaited_once()
        await_args = request_trust.await_args
        assert await_args is not None
        method, params = await_args.args
        assert method == "trust/request"
        assert params["cwd"] == str(cwd.resolve())
        assert params["repoRoot"] == str(repo.resolve())
        assert params["detectedFiles"] == []
        assert params["repoDetectedFiles"] == ["AGENTS.md"]
        assert params["availableDecisions"] == [
            "trust_repo",
            "trust_cwd",
            "trust_session",
            "decline",
        ]
        assert trusted_folders_manager.is_trusted(repo) is True
        assert trusted_folders_manager.is_trusted(cwd) is True
        assert "Repo instructions" in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_decline_skips_project_docs(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Do not load this", encoding="utf-8"
        )
        _enable_workspace_trust(acp_agent_loop)
        monkeypatch.setattr(
            acp_agent_loop.client,
            "ext_method",
            AsyncMock(return_value={"decision": "decline"}),
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is False
        assert "Do not load this" not in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_session_trust_loads_docs_without_persisting(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Session-only instructions", encoding="utf-8"
        )
        _enable_workspace_trust(acp_agent_loop)
        monkeypatch.setattr(
            acp_agent_loop.client,
            "ext_method",
            AsyncMock(return_value={"decision": "trust_session"}),
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        normalized = str(tmp_working_directory.resolve())
        assert trusted_folders_manager.is_trusted(tmp_working_directory) is True
        assert normalized in trusted_folders_manager._session_trusted
        assert normalized not in trusted_folders_manager._trusted
        assert "Session-only instructions" in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_skips_trust_prompt_without_trustable_files(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_workspace_trust(acp_agent_loop)
        request_trust = AsyncMock(return_value={"decision": "trust_cwd"})
        monkeypatch.setattr(acp_agent_loop.client, "ext_method", request_trust)

        await acp_agent_loop.new_session(cwd=str(tmp_working_directory), mcp_servers=[])

        request_trust.assert_not_awaited()
        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None

    @pytest.mark.asyncio
    async def test_new_session_direct_client_fallback_skips_project_docs(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Direct client should skip this", encoding="utf-8"
        )
        _enable_workspace_trust(acp_agent_loop)
        monkeypatch.setattr(
            acp_agent_loop.client,
            "ext_method",
            AsyncMock(side_effect=RequestError.method_not_found("trust/request")),
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
        assert "Direct client should skip this" not in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_without_workspace_trust_capability_skips_prompt(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Unsupported client should skip this", encoding="utf-8"
        )
        request_trust = AsyncMock(return_value={"decision": "trust_cwd"})
        monkeypatch.setattr(acp_agent_loop.client, "ext_method", request_trust)

        session_response = await acp_agent_loop.new_session(
            cwd=str(tmp_working_directory), mcp_servers=[]
        )
        assert session_response.session_id is not None

        request_trust.assert_not_awaited()
        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
        assert "Unsupported client should skip this" not in _system_prompt(
            acp_agent_loop, session_response.session_id
        )

    @pytest.mark.asyncio
    async def test_new_session_cancelled_trust_prompt_cancels_session_creation(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_working_directory: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_working_directory / "AGENTS.md").write_text(
            "Cancelled prompt", encoding="utf-8"
        )
        _enable_workspace_trust(acp_agent_loop)
        monkeypatch.setattr(
            acp_agent_loop.client,
            "ext_method",
            AsyncMock(return_value={"decision": "cancelled"}),
        )

        with pytest.raises(InvalidRequestError):
            await acp_agent_loop.new_session(
                cwd=str(tmp_working_directory), mcp_servers=[]
            )

        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
        assert acp_agent_loop.sessions == {}

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
