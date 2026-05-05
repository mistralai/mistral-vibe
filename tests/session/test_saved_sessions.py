from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.saved_sessions import update_saved_session_title


@pytest.fixture
def temp_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    return session_dir


@pytest.fixture
def session_config(temp_session_dir: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(
        save_dir=str(temp_session_dir), session_prefix="test", enabled=True
    )


class TestUpdateSavedSessionTitle:
    @pytest.mark.asyncio
    async def test_updates_saved_session_title_without_losing_existing_metadata(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)
        saved_session_dir = session_dir / "test_20240101_120000_aaaaaaaa"
        saved_session_dir.mkdir()

        (saved_session_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )

        original_metadata = {
            "session_id": "aaaaaaaa-1111",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": "/home/user/project"},
            "title": "Old title",
            "stats": {"steps": 2},
            "total_messages": 1,
            "tools_available": [
                {
                    "type": "function",
                    "function": {"name": "bash", "description": "Run shell commands"},
                }
            ],
            "config": {"active_model": "test-model"},
            "system_prompt": {"role": "system", "content": "You are helpful"},
        }
        metadata_file = saved_session_dir / "meta.json"
        metadata_file.write_text(json.dumps(original_metadata), encoding="utf-8")

        updated_metadata = await update_saved_session_title(
            "aaaaaaaa-1111", "Renamed session", session_config
        )

        assert updated_metadata == {
            **original_metadata,
            "title": "Renamed session",
            "title_source": "manual",
        }
        assert json.loads(metadata_file.read_text(encoding="utf-8")) == updated_metadata

    @pytest.mark.asyncio
    async def test_rejects_empty_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)
        saved_session_dir = session_dir / "test_20240101_120000_bbbbbbbb"
        saved_session_dir.mkdir()

        (saved_session_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )

        original_metadata = {
            "session_id": "bbbbbbbb-2222",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": "/home/user/project"},
            "title": "Manual title",
            "title_source": "manual",
            "stats": {"steps": 2},
        }
        metadata_file = saved_session_dir / "meta.json"
        metadata_file.write_text(json.dumps(original_metadata), encoding="utf-8")

        with pytest.raises(ValueError, match="Session title cannot be empty."):
            await update_saved_session_title("bbbbbbbb-2222", "   ", session_config)

    @pytest.mark.asyncio
    async def test_preserves_saved_session_end_time_when_updating_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)
        saved_session_dir = session_dir / "test_20240101_120000_cccccccc"
        saved_session_dir.mkdir()

        (saved_session_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )

        original_metadata = {
            "session_id": "cccccccc-3333",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": "/home/user/project"},
            "title": "Old title",
        }
        metadata_file = saved_session_dir / "meta.json"
        metadata_file.write_text(json.dumps(original_metadata), encoding="utf-8")

        updated_metadata = await update_saved_session_title(
            "cccccccc-3333", "Renamed session", session_config
        )

        assert updated_metadata == {
            **original_metadata,
            "title": "Renamed session",
            "title_source": "manual",
        }
        assert json.loads(metadata_file.read_text(encoding="utf-8")) == updated_metadata

    @pytest.mark.asyncio
    async def test_raises_for_missing_saved_session(
        self, session_config: SessionLoggingConfig
    ) -> None:
        with pytest.raises(ValueError, match="Session not found: missing-session"):
            await update_saved_session_title(
                "missing-session", "Renamed", session_config
            )

    @pytest.mark.asyncio
    async def test_requires_exact_saved_session_id(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)
        saved_session_dir = session_dir / "test_20240101_120000_dddddddd"
        saved_session_dir.mkdir()

        (saved_session_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )

        original_metadata = {
            "session_id": "dddddddd-4444",
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": "/home/user/project"},
            "title": "Old title",
        }
        metadata_file = saved_session_dir / "meta.json"
        metadata_file.write_text(json.dumps(original_metadata), encoding="utf-8")

        with pytest.raises(ValueError, match="Session not found: dddddddd"):
            await update_saved_session_title("dddddddd", "Renamed", session_config)

        assert (
            json.loads(metadata_file.read_text(encoding="utf-8")) == original_metadata
        )
