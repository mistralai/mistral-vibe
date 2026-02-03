from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_migration import migrate_sessions


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


@pytest.fixture
def disabled_session_config() -> SessionLoggingConfig:
    return SessionLoggingConfig(
        save_dir="/tmp/test", session_prefix="test", enabled=False
    )


@pytest.fixture
def old_session_data() -> dict:
    return {
        "metadata": {
            "session_id": "test-session-123",
            "start_time": "2023-01-01T00:00:00",
            "end_time": "2023-01-01T01:00:00",
            "git_commit": "abc123",
            "git_branch": "main",
            "username": "testuser",
            "environment": {"working_directory": "/test"},
        },
        "messages": [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
    }


class TestSessionMigration:
    @pytest.mark.asyncio
    async def test_migrate_sessions_disabled_config(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that migration does nothing when config is disabled."""
        result = await migrate_sessions(disabled_session_config)
        assert result == 0

    @pytest.mark.asyncio
    async def test_migrate_sessions_no_save_dir(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that migration handles missing save_dir gracefully."""
        config = SessionLoggingConfig(save_dir="", session_prefix="test", enabled=True)
        result = await migrate_sessions(config)
        assert result == 0

    @pytest.mark.asyncio
    async def test_migrate_sessions_no_old_files(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that migration handles no old session files gracefully."""
        session_dir = Path(session_config.save_dir)
        session_dir.mkdir(exist_ok=True)

        result = await migrate_sessions(session_config)
        assert result == 0

    @pytest.mark.asyncio
    async def test_migrate_sessions_successful_migration(
        self, session_config: SessionLoggingConfig, old_session_data: dict
    ) -> None:
        """Test successful migration of old session files."""
        session_dir = Path(session_config.save_dir)

        old_session_file = session_dir / "test_session-123.json"
        with open(old_session_file, "w") as f:
            json.dump(old_session_data, f)

        result = await migrate_sessions(session_config)
        assert result == 1

        assert not old_session_file.exists()
        session_subdir = session_dir / "test_session-123"
        assert session_subdir.is_dir()

        metadata_file = session_subdir / "meta.json"
        assert metadata_file.is_file()

        with open(metadata_file) as f:
            metadata = json.load(f)
            assert metadata == old_session_data["metadata"]

        messages_file = session_subdir / "messages.jsonl"
        assert messages_file.exists()

        with open(messages_file) as f:
            lines = f.readlines()
            assert len(lines) == len(old_session_data["messages"])

            for i, line in enumerate(lines):
                message = json.loads(line.strip())
                assert message == old_session_data["messages"][i]

    @pytest.mark.asyncio
    async def test_migrate_sessions_multiple_files(
        self, session_config: SessionLoggingConfig, old_session_data: dict
    ) -> None:
        """Test migration of multiple old session files."""
        session_dir = Path(session_config.save_dir)

        session_files = []
        for i in range(3):
            session_file = session_dir / f"test_session-{i:03d}.json"
            session_files.append(session_file)

            modified_data = old_session_data.copy()
            modified_data["metadata"]["session_id"] = f"test-session-{i}"

            with open(session_file, "w") as f:
                json.dump(modified_data, f)

        result = await migrate_sessions(session_config)
        assert result == 3

        for session_file in session_files:
            assert not session_file.exists()

        for i in range(3):
            session_subdir = session_dir / f"test_session-{i:03d}"
            assert session_subdir.exists()
            assert session_subdir.is_dir()

            metadata_file = session_subdir / "meta.json"
            messages_file = session_subdir / "messages.jsonl"
            assert metadata_file.exists()
            assert messages_file.exists()

    @pytest.mark.asyncio
    async def test_migrate_sessions_error_handling(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that migration handles errors gracefully and continues."""
        session_dir = Path(session_config.save_dir)

        valid_session_file = session_dir / "test_session-valid.json"
        valid_data = {
            "metadata": {"session_id": "valid-session"},
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with open(valid_session_file, "w") as f:
            json.dump(valid_data, f)

        invalid_session_file = session_dir / "test_session-invalid.json"
        with open(invalid_session_file, "w") as f:
            f.write("{invalid json}")

        result = await migrate_sessions(session_config)
        assert result == 1

        valid_session_subdir = session_dir / "test_session-valid"
        assert valid_session_subdir.exists()
        assert not valid_session_file.exists()
        assert invalid_session_file.exists()
