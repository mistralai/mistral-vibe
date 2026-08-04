from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session import last_session_pointer
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_title,
)


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


def write_saved_session(
    session_config: SessionLoggingConfig,
    timestamp: str,
    short_id: str,
    metadata: dict[str, object],
) -> Path:
    saved_session_dir = Path(session_config.save_dir) / f"test_{timestamp}_{short_id}"
    saved_session_dir.mkdir()
    (saved_session_dir / "messages.jsonl").write_text(
        '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
    )
    (saved_session_dir / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return saved_session_dir


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
    async def test_skips_non_object_candidate_metadata(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)
        saved_session_dir = session_dir / "test_20240101_120000_eeeeeeee"
        saved_session_dir.mkdir()

        (saved_session_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )
        (saved_session_dir / "meta.json").write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError, match="Session not found: eeeeeeee-5555"):
            await update_saved_session_title("eeeeeeee-5555", "Renamed", session_config)

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


class TestDeleteSavedSession:
    @pytest.mark.asyncio
    async def test_deletes_saved_session_directory(
        self, session_config: SessionLoggingConfig
    ) -> None:
        target_dir = write_saved_session(
            session_config,
            "20240101_120000",
            "ffffffff",
            {"session_id": "ffffffff-6666", "title": "Target"},
        )
        other_dir = write_saved_session(
            session_config,
            "20240101_120000",
            "11111111",
            {"session_id": "11111111-7777", "title": "Other"},
        )

        await delete_saved_session("ffffffff-6666", session_config)

        assert not target_dir.exists()
        assert other_dir.exists()

    @pytest.mark.asyncio
    async def test_clears_matching_last_session_pointers(
        self, session_config: SessionLoggingConfig
    ) -> None:
        target_dir = write_saved_session(
            session_config,
            "20240101_120000",
            "ffffffff",
            {"session_id": "ffffffff-6666", "title": "Target"},
        )
        pointer_dir = (
            Path(session_config.save_dir) / last_session_pointer.POINTER_DIR_NAME
        )
        pointer_dir.mkdir()
        matching_pointer = pointer_dir / "ttys001"
        other_pointer = pointer_dir / "ttys002"
        matching_pointer.write_text("ffffffff-6666\n", encoding="utf-8")
        other_pointer.write_text("other-session\n", encoding="utf-8")

        await delete_saved_session("ffffffff-6666", session_config)

        assert not target_dir.exists()
        assert not matching_pointer.exists()
        assert other_pointer.read_text(encoding="utf-8") == "other-session\n"

    @pytest.mark.asyncio
    async def test_succeeds_for_missing_saved_session(
        self, session_config: SessionLoggingConfig
    ) -> None:
        await delete_saved_session("missing-session", session_config)

    @pytest.mark.asyncio
    async def test_clears_stale_last_session_pointer_for_missing_saved_session(
        self, session_config: SessionLoggingConfig
    ) -> None:
        pointer_dir = (
            Path(session_config.save_dir) / last_session_pointer.POINTER_DIR_NAME
        )
        pointer_dir.mkdir()
        matching_pointer = pointer_dir / "ttys001"
        other_pointer = pointer_dir / "ttys002"
        matching_pointer.write_text("missing-session\n", encoding="utf-8")
        other_pointer.write_text("other-session\n", encoding="utf-8")

        await delete_saved_session("missing-session", session_config)

        assert not matching_pointer.exists()
        assert other_pointer.read_text(encoding="utf-8") == "other-session\n"

    @pytest.mark.asyncio
    async def test_requires_exact_saved_session_id_before_deleting(
        self, session_config: SessionLoggingConfig
    ) -> None:
        collision_dir = write_saved_session(
            session_config,
            "20240101_120000",
            "aaaaaaaa",
            {"session_id": "aaaaaaaa-1111", "title": "Collision"},
        )
        target_dir = write_saved_session(
            session_config,
            "20240101_120500",
            "aaaaaaaa",
            {"session_id": "aaaaaaaa-2222", "title": "Target"},
        )

        await delete_saved_session("aaaaaaaa-2222", session_config)

        assert collision_dir.exists()
        assert not target_dir.exists()

    @pytest.mark.asyncio
    async def test_skips_invalid_candidate_metadata_before_deleting(
        self, session_config: SessionLoggingConfig
    ) -> None:
        invalid_dir = Path(session_config.save_dir) / "test_20240101_120000_bbbbbbbb"
        invalid_dir.mkdir()
        (invalid_dir / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n', encoding="utf-8"
        )
        (invalid_dir / "meta.json").write_text("[]", encoding="utf-8")
        target_dir = write_saved_session(
            session_config,
            "20240101_120500",
            "bbbbbbbb",
            {"session_id": "bbbbbbbb-3333", "title": "Target"},
        )

        await delete_saved_session("bbbbbbbb-3333", session_config)

        assert invalid_dir.exists()
        assert not target_dir.exists()
