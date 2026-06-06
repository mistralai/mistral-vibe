from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError
from vibe.core.session import last_session_pointer


def _write_saved_session(
    session_dir: Path, timestamp: str, session_id: str, cwd: str
) -> Path:
    saved_session_dir = session_dir / f"session_{timestamp}_{session_id[:8]}"
    saved_session_dir.mkdir()
    (saved_session_dir / "messages.jsonl").write_text(
        json.dumps({"role": "user", "content": "Hello"}) + "\n", encoding="utf-8"
    )
    (saved_session_dir / "meta.json").write_text(
        json.dumps({
            "session_id": session_id,
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": cwd},
            "title": "Saved session",
        }),
        encoding="utf-8",
    )
    return saved_session_dir


class TestSessionDelete:
    @pytest.mark.asyncio
    async def test_deletes_saved_but_not_loaded_session(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = "offline-session-12345678"
        session_dir = create_test_session(temp_session_dir, session_id, str(Path.cwd()))

        result = await acp_agent.ext_method("session/delete", {"sessionId": session_id})

        assert result == {}
        assert not session_dir.exists()
        response = await acp_agent.list_sessions()
        assert response.sessions == []

    @pytest.mark.asyncio
    async def test_deletes_saved_session_and_clears_last_session_pointer(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = "pointer-session-12345678"
        session_dir = create_test_session(temp_session_dir, session_id, str(Path.cwd()))
        pointer_dir = temp_session_dir / last_session_pointer.POINTER_DIR_NAME
        pointer_dir.mkdir()
        matching_pointer = pointer_dir / "ttys001"
        other_pointer = pointer_dir / "ttys002"
        matching_pointer.write_text(f"{session_id}\n", encoding="utf-8")
        other_pointer.write_text("other-session\n", encoding="utf-8")

        result = await acp_agent.ext_method("session/delete", {"sessionId": session_id})

        assert result == {}
        assert not session_dir.exists()
        assert not matching_pointer.exists()
        assert other_pointer.read_text(encoding="utf-8") == "other-session\n"

    @pytest.mark.asyncio
    async def test_deletes_loaded_saved_session(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        saved_session_id = "saved-session-12345678"
        acp_session_id = saved_session_id[:8]
        cwd = str(Path.cwd())
        session_dir = create_test_session(temp_session_dir, saved_session_id, cwd)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=acp_session_id)
        session = acp_agent.sessions[acp_session_id]
        session.agent_loop.telemetry_client.aclose = AsyncMock()

        result = await acp_agent.ext_method(
            "session/delete", {"sessionId": saved_session_id}
        )

        assert result == {}
        assert acp_session_id not in acp_agent.sessions
        assert not session_dir.exists()
        response = await acp_agent.list_sessions()
        assert response.sessions == []

    @pytest.mark.asyncio
    async def test_deletes_live_unsaved_session_without_saved_history(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        response = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert response is not None
        session = acp_agent.sessions[response.session_id]
        session.agent_loop.telemetry_client.aclose = AsyncMock()
        assert not session.agent_loop.session_logger.metadata_filepath.exists()

        result = await acp_agent.ext_method(
            "session/delete", {"sessionId": response.session_id}
        )

        assert result == {}
        assert response.session_id not in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_raises_on_invalid_params(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method("session/delete", {})

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method("session/delete", {"sessionId": "   "})

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "session/delete", {"savedSessionId": "unsupported-session"}
            )

    @pytest.mark.asyncio
    async def test_succeeds_when_session_cannot_be_found(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]

        result = await acp_agent.ext_method(
            "session/delete", {"sessionId": "missing-session"}
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_requires_exact_saved_session_id_before_deleting(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        cwd = str(Path.cwd())
        collision_dir = _write_saved_session(
            temp_session_dir, "20240101_120000", "aaaaaaaa-1111", cwd
        )
        target_dir = _write_saved_session(
            temp_session_dir, "20240101_120500", "aaaaaaaa-2222", cwd
        )

        result = await acp_agent.ext_method(
            "session/delete", {"sessionId": "aaaaaaaa-2222"}
        )

        assert result == {}
        assert collision_dir.exists()
        assert not target_dir.exists()
