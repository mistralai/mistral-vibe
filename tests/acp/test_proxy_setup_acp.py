from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp.schema import AgentMessageChunk, AvailableCommandsUpdate, TextContentBlock
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop


@pytest.fixture
def acp_agent_loop(backend) -> VibeAcpAgentLoop:
    config = build_test_vibe_config()

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()

    return _create_acp_agent()


def _get_fake_client(acp_agent_loop: VibeAcpAgentLoop) -> FakeClient:
    assert isinstance(acp_agent_loop.client, FakeClient)
    return acp_agent_loop.client


class TestAvailableCommandsUpdate:
    @pytest.mark.asyncio
    async def test_available_commands_sent_on_new_session(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        import asyncio

        await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await asyncio.sleep(0)

        updates = _get_fake_client(acp_agent_loop)._session_updates
        available_commands_updates = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]

        assert len(available_commands_updates) == 1
        update = available_commands_updates[0].update
        assert len(update.available_commands) == 1
        assert update.available_commands[0].name == "proxy-setup"
        assert "proxy" in update.available_commands[0].description.lower()


class TestProxySetupCommand:
    @pytest.mark.asyncio
    async def test_proxy_setup_shows_help_when_no_args(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"

        class FakeGlobalEnvFile:
            path = env_file

        monkeypatch.setattr(
            "vibe.core.proxy_setup.GLOBAL_ENV_FILE", FakeGlobalEnvFile()
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        _get_fake_client(acp_agent_loop)._session_updates.clear()

        response = await acp_agent_loop.prompt(
            prompt=[TextContentBlock(type="text", text="/proxy-setup")],
            session_id=session_id,
        )

        assert response.stop_reason == "end_turn"

        updates = _get_fake_client(acp_agent_loop)._session_updates
        message_updates = [
            u for u in updates if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(message_updates) == 1
        content = message_updates[0].update.content.text
        assert "## Proxy Configuration" in content
        assert "HTTP_PROXY" in content

    @pytest.mark.asyncio
    async def test_proxy_setup_sets_value(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"

        class FakeGlobalEnvFile:
            path = env_file

        monkeypatch.setattr(
            "vibe.core.proxy_setup.GLOBAL_ENV_FILE", FakeGlobalEnvFile()
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        _get_fake_client(acp_agent_loop)._session_updates.clear()

        response = await acp_agent_loop.prompt(
            prompt=[
                TextContentBlock(
                    type="text", text="/proxy-setup HTTP_PROXY http://localhost:8080"
                )
            ],
            session_id=session_id,
        )

        assert response.stop_reason == "end_turn"

        updates = _get_fake_client(acp_agent_loop)._session_updates
        message_updates = [
            u for u in updates if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(message_updates) == 1
        content = message_updates[0].update.content.text
        assert "HTTP_PROXY" in content
        assert "http://localhost:8080" in content

        assert env_file.exists()
        env_content = env_file.read_text()
        assert "HTTP_PROXY" in env_content
        assert "http://localhost:8080" in env_content

    @pytest.mark.asyncio
    async def test_proxy_setup_unsets_value(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("HTTP_PROXY=http://old-proxy.com\n")

        class FakeGlobalEnvFile:
            path = env_file

        monkeypatch.setattr(
            "vibe.core.proxy_setup.GLOBAL_ENV_FILE", FakeGlobalEnvFile()
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        _get_fake_client(acp_agent_loop)._session_updates.clear()

        response = await acp_agent_loop.prompt(
            prompt=[TextContentBlock(type="text", text="/proxy-setup HTTP_PROXY")],
            session_id=session_id,
        )

        assert response.stop_reason == "end_turn"

        updates = _get_fake_client(acp_agent_loop)._session_updates
        message_updates = [
            u for u in updates if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(message_updates) == 1
        content = message_updates[0].update.content.text
        assert "Removed" in content
        assert "HTTP_PROXY" in content

        env_content = env_file.read_text()
        assert "HTTP_PROXY" not in env_content

    @pytest.mark.asyncio
    async def test_proxy_setup_invalid_key_returns_error(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"

        class FakeGlobalEnvFile:
            path = env_file

        monkeypatch.setattr(
            "vibe.core.proxy_setup.GLOBAL_ENV_FILE", FakeGlobalEnvFile()
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        _get_fake_client(acp_agent_loop)._session_updates.clear()

        response = await acp_agent_loop.prompt(
            prompt=[
                TextContentBlock(type="text", text="/proxy-setup INVALID_KEY value")
            ],
            session_id=session_id,
        )

        assert response.stop_reason == "end_turn"

        updates = _get_fake_client(acp_agent_loop)._session_updates
        message_updates = [
            u for u in updates if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(message_updates) == 1
        content = message_updates[0].update.content.text
        assert "Error" in content
        assert "Unknown key" in content

    @pytest.mark.asyncio
    async def test_proxy_setup_case_insensitive(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"

        class FakeGlobalEnvFile:
            path = env_file

        monkeypatch.setattr(
            "vibe.core.proxy_setup.GLOBAL_ENV_FILE", FakeGlobalEnvFile()
        )

        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        _get_fake_client(acp_agent_loop)._session_updates.clear()

        response = await acp_agent_loop.prompt(
            prompt=[
                TextContentBlock(
                    type="text", text="/PROXY-SETUP http_proxy http://localhost:8080"
                )
            ],
            session_id=session_id,
        )

        assert response.stop_reason == "end_turn"

        assert env_file.exists()
        env_content = env_file.read_text()
        assert "HTTP_PROXY" in env_content
