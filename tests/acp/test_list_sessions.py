from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.acp.conftest import _create_acp_agent
from vibe.core.config import MissingAPIKeyError, SessionLoggingConfig


class TestListSessions:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, temp_session_dir: Path) -> None:
        acp_agent = _create_acp_agent()

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert response.sessions == []

    @pytest.mark.asyncio
    async def test_list_sessions_returns_all_sessions(
        self, temp_session_dir: Path, create_test_session
    ) -> None:
        acp_agent = _create_acp_agent()

        create_test_session(
            temp_session_dir,
            "aaaaaaaa-1111",
            "/home/user/project1",
            title="First session",
            end_time="2024-01-01T12:00:00Z",
        )
        create_test_session(
            temp_session_dir,
            "bbbbbbbb-2222",
            "/home/user/project2",
            title="Second session",
            end_time="2024-01-01T13:00:00Z",
        )

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert len(response.sessions) == 2
        session_ids = {s.session_id for s in response.sessions}
        assert "aaaaaaaa-1111" in session_ids
        assert "bbbbbbbb-2222" in session_ids

    @pytest.mark.asyncio
    async def test_list_sessions_filters_by_cwd(
        self, temp_session_dir: Path, create_test_session
    ) -> None:
        acp_agent = _create_acp_agent()

        create_test_session(
            temp_session_dir,
            "aaaaaaaa-proj1",
            "/home/user/project1",
            title="Project 1 session",
        )
        create_test_session(
            temp_session_dir,
            "bbbbbbbb-proj2",
            "/home/user/project2",
            title="Project 2 session",
        )
        create_test_session(
            temp_session_dir,
            "cccccccc-proj1",
            "/home/user/project1",
            title="Another Project 1 session",
        )

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions(cwd="/home/user/project1")

        assert len(response.sessions) == 2
        for session in response.sessions:
            assert session.cwd == "/home/user/project1"

    @pytest.mark.asyncio
    async def test_list_sessions_sorted_by_updated_at(
        self, temp_session_dir: Path, create_test_session
    ) -> None:
        acp_agent = _create_acp_agent()

        create_test_session(
            temp_session_dir,
            "oldest-s",
            "/home/user/project",
            title="Oldest",
            end_time="2024-01-01T10:00:00Z",
        )
        create_test_session(
            temp_session_dir,
            "newest-s",
            "/home/user/project",
            title="Newest",
            end_time="2024-01-01T14:00:00Z",
        )
        create_test_session(
            temp_session_dir,
            "middle-s",
            "/home/user/project",
            title="Middle",
            end_time="2024-01-01T12:00:00Z",
        )

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert len(response.sessions) == 3

        assert response.sessions[0].title == "Newest"
        assert response.sessions[1].title == "Middle"
        assert response.sessions[2].title == "Oldest"

    @pytest.mark.asyncio
    async def test_list_sessions_includes_session_info_fields(
        self, temp_session_dir: Path, create_test_session
    ) -> None:
        acp_agent = _create_acp_agent()

        create_test_session(
            temp_session_dir,
            "test-session-123",
            "/home/user/project",
            title="Test Session Title",
            end_time="2024-01-15T10:30:00Z",
        )

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert len(response.sessions) == 1
        session = response.sessions[0]
        assert session.session_id == "test-session-123"
        assert session.cwd == "/home/user/project"
        assert session.title == "Test Session Title"
        # updated_at is normalized to UTC
        assert session.updated_at is not None
        assert session.updated_at.endswith("+00:00")

    @pytest.mark.asyncio
    async def test_list_sessions_skips_invalid_sessions(
        self, temp_session_dir: Path, create_test_session
    ) -> None:
        acp_agent = _create_acp_agent()

        create_test_session(
            temp_session_dir, "valid-se", "/home/user/project", title="Valid Session"
        )

        invalid_session = temp_session_dir / "session_20240101_120000_invalid1"
        invalid_session.mkdir()
        (invalid_session / "meta.json").write_text('{"session_id": "invalid"}')

        no_id_session = temp_session_dir / "session_20240101_120001_noid0000"
        no_id_session.mkdir()
        (no_id_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n'
        )
        (no_id_session / "meta.json").write_text(
            '{"environment": {"working_directory": "/test"}}'
        )

        session_config = SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert len(response.sessions) == 1
        assert response.sessions[0].session_id == "valid-se"

    @pytest.mark.asyncio
    async def test_list_sessions_nonexistent_save_dir(self) -> None:
        acp_agent = _create_acp_agent()

        session_config = SessionLoggingConfig(
            save_dir="/nonexistent/path", session_prefix="session", enabled=True
        )

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_config = mock_load.return_value
            mock_config.session_logging = session_config

            response = await acp_agent.list_sessions()

        assert response.sessions == []

    @pytest.mark.asyncio
    async def test_list_sessions_without_api_key(self) -> None:
        acp_agent = _create_acp_agent()

        with patch("vibe.acp.acp_agent_loop.VibeConfig.load") as mock_load:
            mock_load.side_effect = MissingAPIKeyError("api_key", "mistral")

            response = await acp_agent.list_sessions()

        assert response.sessions == []
