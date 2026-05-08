"""Tests for ACP slash command handlers on VibeAcpAgentLoop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from acp.schema import AgentMessageChunk, AvailableCommandsUpdate, TextContentBlock
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.skills.conftest import create_skill
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig


def _get_client(agent: VibeAcpAgentLoop) -> FakeClient:
    assert isinstance(agent.client, FakeClient)
    return agent.client


def _get_message_texts(agent: VibeAcpAgentLoop) -> list[str]:
    """Extract text content from all AgentMessageChunk session updates."""
    return [
        u.update.content.text
        for u in _get_client(agent)._session_updates
        if isinstance(u.update, AgentMessageChunk)
    ]


async def _new_session_and_clear(agent: VibeAcpAgentLoop) -> str:
    """Create a new session, drain the startup updates, return session_id."""
    resp = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    await asyncio.sleep(0)  # let background tasks (available_commands) complete
    _get_client(agent)._session_updates.clear()
    return resp.session_id


async def _prompt(agent: VibeAcpAgentLoop, session_id: str, text: str):
    return await agent.prompt(
        prompt=[TextContentBlock(type="text", text=text)], session_id=session_id
    )


def _make_patched_agent_loop(
    backend: FakeBackend,
    *,
    skill_paths: list[Path] | None = None,
    session_logging: SessionLoggingConfig | None = None,
) -> type[AgentLoop]:
    """Create a PatchedAgentLoop class that injects config overrides."""
    config_updates: dict = {}
    if skill_paths is not None:
        config_updates["skill_paths"] = skill_paths
    if session_logging is not None:
        config_updates["session_logging"] = session_logging

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            if config_updates and "config" in kwargs and kwargs["config"] is not None:
                kwargs["config"] = kwargs["config"].model_copy(update=config_updates)
            super().__init__(*args, **{**kwargs, "backend": backend})

    return PatchedAgentLoop


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgentLoop:
    patched = _make_patched_agent_loop(backend)
    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched).start()
    return _create_acp_agent()


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    return d


@pytest.fixture
def acp_agent_loop_with_skills(
    backend: FakeBackend, skills_dir: Path
) -> VibeAcpAgentLoop:
    # Skills must exist in skills_dir BEFORE new_session() is called.
    patched = _make_patched_agent_loop(backend, skill_paths=[skills_dir])
    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched).start()
    return _create_acp_agent()


class TestHandleHelp:
    @pytest.mark.asyncio
    async def test_lists_all_registered_commands(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/help")

        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        assert len(texts) == 1
        content = texts[0]

        main_commands = ["help", "compact", "reload", "proxy-setup"]
        for cmd in main_commands:
            assert f"/{cmd}" in content

    @pytest.mark.asyncio
    async def test_includes_user_invocable_skills(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        # Create skills before new_session so SkillManager discovers them
        create_skill(skills_dir, "my-skill", "Does something useful")
        create_skill(skills_dir, "hidden-skill", "Secret", user_invocable=False)

        session_id = await _new_session_and_clear(acp_agent_loop_with_skills)
        await _prompt(acp_agent_loop_with_skills, session_id, "/help")

        content = _get_message_texts(acp_agent_loop_with_skills)[0]
        assert "/my-skill" in content
        assert "Does something useful" in content
        assert "hidden-skill" not in content


class TestHandleCompact:
    @pytest.mark.asyncio
    async def test_empty_history_does_not_compact(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.agent_loop, "compact", new_callable=AsyncMock
        ) as mock_compact:
            await _prompt(acp_agent_loop, session_id, "/compact")
            mock_compact.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_calls_agent_loop_compact(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)

        # Have a conversation first to create history
        await _prompt(acp_agent_loop, session_id, "Hello, tell me something")
        _get_client(acp_agent_loop)._session_updates.clear()

        session = acp_agent_loop.sessions[session_id]
        with patch.object(
            session.agent_loop, "compact", new_callable=AsyncMock
        ) as mock_compact:
            response = await _prompt(acp_agent_loop, session_id, "/compact")
            assert response.stop_reason == "end_turn"
            mock_compact.assert_called_once()


class TestHandleReload:
    @pytest.mark.asyncio
    async def test_reload_calls_reload_with_initial_messages(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.agent_loop, "reload_with_initial_messages", new_callable=AsyncMock
        ) as mock_reload:
            response = await _prompt(acp_agent_loop, session_id, "/reload")
            assert response.stop_reason == "end_turn"
            mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_reload_notifies_commands_changed(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.command_registry, "notify_changed", new_callable=AsyncMock
        ) as mock_notify:
            await _prompt(acp_agent_loop, session_id, "/reload")
            mock_notify.assert_called_once()


class TestCommandFallthrough:
    @pytest.mark.asyncio
    async def test_unknown_slash_command_reaches_agent(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/nonexistent")

        # The agent loop should have processed it (FakeBackend returns "Hi")
        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        # Should contain the LLM response, not a command reply
        assert any("Hi" in t for t in texts)

    @pytest.mark.asyncio
    async def test_regular_message_reaches_agent(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "Hello world")

        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        assert any("Hi" in t for t in texts)


class TestAvailableCommandsWithSkills:
    @pytest.mark.asyncio
    async def test_skills_appear_in_available_commands(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "my-skill", "A useful skill")

        await acp_agent_loop_with_skills.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        await asyncio.sleep(0)

        updates = _get_client(acp_agent_loop_with_skills)._session_updates
        available = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]
        assert len(available) == 1

        cmd_names = [c.name for c in available[0].update.available_commands]
        assert "my-skill" in cmd_names
        # Built-in commands should also be present
        assert "help" in cmd_names

    @pytest.mark.asyncio
    async def test_non_invocable_skills_excluded_from_available_commands(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "visible-skill", "Visible")
        create_skill(skills_dir, "hidden-skill", "Hidden", user_invocable=False)

        await acp_agent_loop_with_skills.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        await asyncio.sleep(0)

        updates = _get_client(acp_agent_loop_with_skills)._session_updates
        available = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]
        cmd_names = [c.name for c in available[0].update.available_commands]

        assert "visible-skill" in cmd_names
        assert "hidden-skill" not in cmd_names


class TestSlashCommandTelemetry:
    @pytest.mark.asyncio
    async def test_builtin_command_fires_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "/help")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert len(slash_events) == 1
        assert slash_events[0]["properties"]["command"] == "help"
        assert slash_events[0]["properties"]["command_type"] == "builtin"

    @pytest.mark.asyncio
    async def test_skill_command_fires_telemetry(
        self,
        acp_agent_loop_with_skills: VibeAcpAgentLoop,
        skills_dir: Path,
        telemetry_events: list[dict],
    ) -> None:
        create_skill(skills_dir, "my-skill", "Does something")
        session_id = await _new_session_and_clear(acp_agent_loop_with_skills)
        telemetry_events.clear()

        await _prompt(acp_agent_loop_with_skills, session_id, "/my-skill")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert len(slash_events) == 1
        assert slash_events[0]["properties"]["command"] == "my-skill"
        assert slash_events[0]["properties"]["command_type"] == "skill"

    @pytest.mark.asyncio
    async def test_unknown_slash_command_does_not_fire_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "/nonexistent")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert slash_events == []

    @pytest.mark.asyncio
    async def test_regular_message_does_not_fire_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "Hello world")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert slash_events == []


class TestCommandCaseInsensitivity:
    @pytest.mark.asyncio
    async def test_uppercase_command(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/HELP")

        assert response.stop_reason == "end_turn"
        content = _get_message_texts(acp_agent_loop)[0]
        assert "Available Commands" in content

    @pytest.mark.asyncio
    async def test_mixed_case_command(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/Help")

        assert response.stop_reason == "end_turn"
        content = _get_message_texts(acp_agent_loop)[0]
        assert "Available Commands" in content
