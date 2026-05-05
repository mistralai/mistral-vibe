from __future__ import annotations

import json
from pathlib import Path

from acp.schema import SessionInfoUpdate
import pytest
import tomli_w

from tests.conftest import build_test_vibe_config, get_base_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InternalError, InvalidRequestError, SessionNotFoundError
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, SessionLoggingConfig


@pytest.fixture
def acp_agent_with_session_config(
    backend: FakeBackend, temp_session_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[VibeAcpAgentLoop, FakeClient]:
    session_config = SessionLoggingConfig(
        save_dir=str(temp_session_dir), session_prefix="session", enabled=True
    )
    config = build_test_vibe_config(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest", provider="mistral", alias="devstral-latest"
            )
        ],
        session_logging=session_config,
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()

    monkeypatch.setattr("vibe.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr(VibeAcpAgentLoop, "_load_config", lambda self: config)
    monkeypatch.setattr(
        "vibe.acp.acp_agent_loop.VibeConfig.load", lambda *args, **kwargs: config
    )

    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()
    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent, client


class TestSessionSetTitle:
    @pytest.mark.asyncio
    async def test_updates_live_unsaved_session_title(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        response = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert response is not None

        result = await acp_agent.ext_method(
            "session/set_title",
            {"sessionId": response.session_id, "title": "Manual title"},
        )

        assert result == {}

        session = acp_agent.sessions[response.session_id]
        metadata = session.agent_loop.session_logger.session_metadata
        assert metadata is not None
        assert metadata.title == "Manual title"
        assert metadata.title_source == "manual"
        assert metadata.end_time is None
        assert not session.agent_loop.session_logger.metadata_filepath.exists()

        info_updates = [
            notification.update
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert len(info_updates) == 1
        assert info_updates[0].title == "Manual title"
        assert info_updates[0].updated_at is None

    @pytest.mark.asyncio
    async def test_updates_live_saved_session_title(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        saved_session_id = "saved-session-12345678"
        acp_session_id = saved_session_id[:8]
        cwd = str(Path.cwd())
        session_dir = create_test_session(
            temp_session_dir,
            saved_session_id,
            cwd,
            title="Old title",
            end_time="2024-01-01T12:05:00Z",
        )

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=acp_session_id)
        client._session_updates.clear()

        result = await acp_agent.ext_method(
            "session/set_title",
            {"sessionId": saved_session_id, "title": "Renamed session"},
        )

        assert result == {}

        session = acp_agent.sessions[acp_session_id]
        metadata = session.agent_loop.session_logger.session_metadata
        assert metadata is not None
        assert metadata.title == "Renamed session"
        assert metadata.title_source == "manual"
        assert metadata.end_time == "2024-01-01T12:05:00Z"
        assert session.agent_loop.session_id == saved_session_id

        saved_metadata = json.loads((session_dir / "meta.json").read_text())
        assert saved_metadata["title"] == "Renamed session"
        assert saved_metadata["title_source"] == "manual"
        assert saved_metadata["end_time"] == "2024-01-01T12:05:00Z"

        info_updates = [
            notification
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert len(info_updates) == 1
        assert info_updates[0].session_id == acp_session_id
        assert info_updates[0].update.title == "Renamed session"
        assert info_updates[0].update.updated_at == metadata.end_time
        assert saved_metadata["end_time"] == info_updates[0].update.updated_at

    @pytest.mark.asyncio
    async def test_loaded_session_title_is_unchanged_when_persist_fails(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        saved_session_id = "saved-session-12345678"
        acp_session_id = saved_session_id[:8]
        cwd = str(Path.cwd())
        create_test_session(
            temp_session_dir,
            saved_session_id,
            cwd,
            title="Old title",
            end_time="2024-01-01T12:05:00Z",
        )

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=acp_session_id)
        client._session_updates.clear()

        async def fail_persist(*args, **kwargs):
            raise ValueError("Cannot rewrite metadata")

        monkeypatch.setattr(
            "vibe.acp.acp_agent_loop.update_saved_session_title_at_path", fail_persist
        )

        with pytest.raises(InternalError):
            await acp_agent.ext_method(
                "session/set_title",
                {"sessionId": saved_session_id, "title": "Renamed session"},
            )

        session = acp_agent.sessions[acp_session_id]
        metadata = session.agent_loop.session_logger.session_metadata
        assert metadata is not None
        assert metadata.title == "Old title"
        assert metadata.title_source == "auto"
        assert not [
            notification
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]

    @pytest.mark.asyncio
    async def test_updates_saved_but_not_loaded_session_title(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "offline-session-12345678"
        cwd = str(Path.cwd())
        session_dir = create_test_session(
            temp_session_dir,
            session_id,
            cwd,
            title="Old title",
            end_time="2024-01-01T12:05:00Z",
        )

        result = await acp_agent.ext_method(
            "session/set_title", {"sessionId": session_id, "title": "Renamed session"}
        )

        assert result == {}

        saved_metadata = json.loads((session_dir / "meta.json").read_text())
        assert saved_metadata["title"] == "Renamed session"
        assert saved_metadata["title_source"] == "manual"

        info_updates = [
            notification
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert len(info_updates) == 1
        assert info_updates[0].session_id == session_id
        assert info_updates[0].update.title == "Renamed session"
        assert info_updates[0].update.updated_at == saved_metadata["end_time"]

    @pytest.mark.asyncio
    async def test_updates_saved_session_with_configured_log_dir_without_api_key(
        self,
        config_dir: Path,
        temp_session_dir: Path,
        create_test_session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = {
            **get_base_config(),
            "session_logging": {
                "enabled": True,
                "save_dir": str(temp_session_dir),
                "session_prefix": "session",
            },
        }
        monkeypatch.setenv("VIBE_HOME", str(config_dir))
        (config_dir / "config.toml").write_text(tomli_w.dumps(config), encoding="utf-8")
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

        session_id = "offline-session-12345678"
        session_dir = create_test_session(
            temp_session_dir,
            session_id,
            str(Path.cwd()),
            title="Old title",
            end_time="2024-01-01T12:05:00Z",
        )
        acp_agent = VibeAcpAgentLoop()
        client = FakeClient()
        acp_agent.on_connect(client)
        client.on_connect(acp_agent)

        result = await acp_agent.ext_method(
            "session/set_title",
            {"sessionId": session_id, "title": "Renamed without key"},
        )

        assert result == {}
        saved_metadata = json.loads((session_dir / "meta.json").read_text())
        assert saved_metadata["title"] == "Renamed without key"
        assert saved_metadata["title_source"] == "manual"

        info_updates = [
            notification
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert len(info_updates) == 1
        assert info_updates[0].session_id == session_id
        assert info_updates[0].update.title == "Renamed without key"

    @pytest.mark.asyncio
    async def test_raises_on_invalid_params(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/set_title", {"title": "Missing session id"}
            )

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/set_title", {"sessionId": "missing-title-session"}
            )

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/set_title",
                {"sessionId": "blank-title-session", "title": "   "},
            )

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/set_title", {"sessionId": "   ", "title": "Blank session id"}
            )

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/set_title",
                {"savedSessionId": "saved-session", "title": "Unsupported target"},
            )

    @pytest.mark.asyncio
    async def test_session_id_falls_back_to_saved_session_lookup(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "saved-session-12345678"
        cwd = str(Path.cwd())
        session_dir = create_test_session(
            temp_session_dir,
            session_id,
            cwd,
            title="Old title",
            end_time="2024-01-01T12:05:00Z",
        )

        result = await acp_agent.ext_method(
            "session/set_title", {"sessionId": session_id, "title": "Renamed session"}
        )

        assert result == {}
        saved_metadata = json.loads((session_dir / "meta.json").read_text())
        assert saved_metadata["title"] == "Renamed session"
        assert saved_metadata["title_source"] == "manual"

        info_updates = [
            notification
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert len(info_updates) == 1
        assert info_updates[0].session_id == session_id
        assert info_updates[0].update.title == "Renamed session"

    @pytest.mark.asyncio
    async def test_raises_when_session_cannot_be_found(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        with pytest.raises(SessionNotFoundError):
            await acp_agent.ext_method(
                "session/set_title",
                {"sessionId": "missing-session", "title": "Renamed session"},
            )
