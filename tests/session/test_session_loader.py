from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import LLMMessage, Role, ToolCall


@pytest.fixture
def temp_session_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for session loader tests."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    return session_dir


@pytest.fixture
def session_config(temp_session_dir: Path) -> SessionLoggingConfig:
    """Create a session logging config for testing."""
    return SessionLoggingConfig(
        save_dir=str(temp_session_dir), session_prefix="test", enabled=True
    )


@pytest.fixture
def create_test_session():
    """Helper fixture to create a test session with messages and metadata."""

    def _create_test_session(
        session_dir: Path,
        session_id: str,
        messages: list[LLMMessage] | None = None,
        metadata: dict | None = None,
    ) -> Path:
        """Create a test session directory with messages and metadata files."""
        # Create session directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_folder = session_dir / f"test_{timestamp}_{session_id[:8]}"
        session_folder.mkdir(exist_ok=True)

        # Create messages file
        messages_file = session_folder / "messages.jsonl"
        if messages is None:
            messages = [
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
                LLMMessage(role=Role.assistant, content="Hi there!"),
            ]

        with messages_file.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(
                    json.dumps(
                        message.model_dump(exclude_none=True), ensure_ascii=False
                    )
                    + "\n"
                )

        # Create metadata file
        metadata_file = session_folder / "meta.json"
        if metadata is None:
            metadata = {
                "session_id": session_id,
                "start_time": "2023-01-01T12:00:00Z",
                "end_time": "2023-01-01T12:05:00Z",
                "total_messages": 2,
                "stats": {
                    "steps": 1,
                    "session_prompt_tokens": 10,
                    "session_completion_tokens": 20,
                },
                "system_prompt": {"content": "System prompt", "role": "system"},
            }

        with metadata_file.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        return session_folder

    return _create_test_session


class TestSessionLoaderFindLatestSession:
    def test_find_latest_session_no_sessions(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test finding latest session when no sessions exist."""
        result = SessionLoader.find_latest_session(session_config)
        assert result is None

    def test_find_latest_session_single_session(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding latest session with a single session."""
        session_dir = Path(session_config.save_dir)
        session = create_test_session(session_dir, "session-123")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result.exists()
        assert result == session

    def test_find_latest_session_multiple_sessions(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding latest session with multiple sessions."""
        session_dir = Path(session_config.save_dir)

        create_test_session(session_dir, "session-123")
        time.sleep(0.01)
        create_test_session(session_dir, "session-456")
        time.sleep(0.01)
        latest = create_test_session(session_dir, "session-789")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result.exists()
        assert result == latest

    def test_find_latest_session_nonexistent_save_dir(self) -> None:
        """Test finding latest session when save directory doesn't exist."""
        # Modify config to point to non-existent directory
        bad_config = SessionLoggingConfig(
            save_dir="/nonexistent/path", session_prefix="test", enabled=True
        )

        result = SessionLoader.find_latest_session(bad_config)
        assert result is None

    def test_find_latest_session_with_invalid_sessions(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test finding latest session when only invalid sessions exist."""
        session_dir = Path(session_config.save_dir)

        invalid_session1 = session_dir / "test_20230101_120000_invalid1"
        invalid_session1.mkdir()
        (invalid_session1 / "messages.jsonl").write_text("[]")  # Missing meta.json

        invalid_session2 = session_dir / "test_20230101_120001_invalid2"
        invalid_session2.mkdir()
        (invalid_session2 / "meta.json").write_text('{"session_id": "invalid"}')

        result = SessionLoader.find_latest_session(session_config)
        assert result is None  # Should return None when no valid sessions exist

    def test_find_latest_session_with_mixed_valid_invalid(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding latest session when both valid and invalid sessions exist."""
        session_dir = Path(session_config.save_dir)

        invalid_session = session_dir / "test_20230101_120000_invalid"
        invalid_session.mkdir()
        (invalid_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "test"}\n'
        )

        time.sleep(0.01)

        valid_session = create_test_session(session_dir, "test_20230101_120001_valid")

        time.sleep(0.01)

        newest_invalid = session_dir / "test_20230101_120002_newest"
        newest_invalid.mkdir()
        (newest_invalid / "messages.jsonl").write_text(
            '{"role": "user", "content": "test"}\n'
        )

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_with_invalid_json(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding latest session when sessions have invalid JSON."""
        session_dir = Path(session_config.save_dir)

        invalid_meta_session = session_dir / "test_20230101_120000_invalid_meta"
        invalid_meta_session.mkdir()
        (invalid_meta_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "test"}\n'
        )
        (invalid_meta_session / "meta.json").write_text("{invalid json}")

        time.sleep(0.01)

        invalid_msg_session = session_dir / "test_20230101_120001_invalid_msg"
        invalid_msg_session.mkdir()
        (invalid_msg_session / "messages.jsonl").write_text("{invalid json}")
        (invalid_msg_session / "meta.json").write_text('{"session_id": "invalid"}')

        time.sleep(0.01)

        valid_session = create_test_session(session_dir, "test_20230101_120002_valid")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_skips_empty_messages_file(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)

        valid_session = create_test_session(session_dir, "valid123-session")
        time.sleep(0.01)

        empty_session = create_test_session(session_dir, "emptymss-session")
        (empty_session / "messages.jsonl").write_text("")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_skips_messages_json_not_dict(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)

        valid_session = create_test_session(session_dir, "valid123-session")
        time.sleep(0.01)

        invalid_session = create_test_session(session_dir, "msglist-session")
        (invalid_session / "messages.jsonl").write_text("[]\n")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_skips_metadata_json_not_dict(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)

        valid_session = create_test_session(session_dir, "valid123-session")
        time.sleep(0.01)

        invalid_session = create_test_session(session_dir, "metalist-session")
        (invalid_session / "meta.json").write_text("[]")

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_skips_unreadable_messages_file(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)

        valid_session = create_test_session(session_dir, "valid123-session")
        time.sleep(0.01)

        unreadable_session = create_test_session(session_dir, "unreadab-session")
        unreadable_messages = unreadable_session / "messages.jsonl"
        unreadable_messages.chmod(0)

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session

    def test_find_latest_session_skips_unreadable_metadata_file(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)

        valid_session = create_test_session(session_dir, "valid123-session")
        time.sleep(0.01)

        unreadable_session = create_test_session(session_dir, "unreadab-session")
        unreadable_metadata = unreadable_session / "meta.json"
        unreadable_metadata.chmod(0)

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result == valid_session


class TestSessionLoaderFindSessionById:
    def test_find_session_by_id_exact_match(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session by exact ID match."""
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(session_dir, "test-session-123")

        # Test with full UUID format
        result = SessionLoader.find_session_by_id("test-session-123", session_config)
        assert result is not None
        assert result == session_folder

    def test_find_session_by_id_short_uuid(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session by short UUID."""
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(
            session_dir, "abc12345-6789-0123-4567-89abcdef0123"
        )

        # Test with short UUID
        result = SessionLoader.find_session_by_id("abc12345", session_config)
        assert result is not None
        assert result == session_folder

    def test_find_session_by_id_partial_match(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session by partial ID match"""
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(session_dir, "abc12345678")

        # Test with partial match
        result = SessionLoader.find_session_by_id("abc12345", session_config)
        assert result is not None
        assert result == session_folder

    def test_find_session_by_id_multiple_matches(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session when multiple sessions match (should return most recent)."""
        session_dir = Path(session_config.save_dir)

        # Create first session
        create_test_session(session_dir, "abcd1234")

        # Sleep to ensure different modification times
        time.sleep(0.01)

        # Create second session with similar ID prefix
        session_2 = create_test_session(session_dir, "abcd1234")

        result = SessionLoader.find_session_by_id("abcd1234", session_config)
        assert result is not None
        assert result == session_2

    def test_find_session_by_id_no_match(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session by ID when no match exists."""
        session_dir = Path(session_config.save_dir)
        create_test_session(session_dir, "test-session-123")

        result = SessionLoader.find_session_by_id("nonexistent", session_config)
        assert result is None

    def test_find_session_by_id_nonexistent_save_dir(self) -> None:
        """Test finding session by ID when save directory doesn't exist."""
        bad_config = SessionLoggingConfig(
            save_dir="/nonexistent/path", session_prefix="test", enabled=True
        )

        result = SessionLoader.find_session_by_id("test-session", bad_config)
        assert result is None


class TestSessionLoaderDoesSessionExist:
    def test_does_session_exist_no_messages(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(session_dir, "test-session-123")
        (session_folder / "messages.jsonl").unlink()

        result = SessionLoader.does_session_exist("test-session-123", session_config)
        assert result is None

    def test_does_session_exist_success(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(session_dir, "test-session-123")

        result = SessionLoader.does_session_exist("test-session-123", session_config)
        assert result == session_folder


class TestSessionLoaderLoadSession:
    def test_load_session_success(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test successfully loading a session."""
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(session_dir, "test-session-123")

        messages, metadata = SessionLoader.load_session(session_folder)

        # Verify messages
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Hello"
        assert messages[1].role == "assistant"
        assert messages[1].content == "Hi there!"

        # Verify metadata
        assert metadata["session_id"] == "test-session-123"
        assert metadata["total_messages"] == 2
        assert "stats" in metadata
        assert "system_prompt" in metadata

    def test_load_session_empty_messages(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session with empty messages file."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        # Create empty messages file
        messages_file = session_folder / "messages.jsonl"
        messages_file.write_text("")

        # Create metadata file
        metadata_file = session_folder / "meta.json"
        metadata_file.write_text('{"session_id": "test-session"}')

        with pytest.raises(ValueError, match="Session messages file is empty"):
            SessionLoader.load_session(session_folder)

    def test_load_session_invalid_json_messages(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session with invalid JSON in messages file."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        # Create messages file with invalid JSON
        messages_file = session_folder / "messages.jsonl"
        messages_file.write_text("{invalid json}")

        # Create metadata file
        metadata_file = session_folder / "meta.json"
        metadata_file.write_text('{"session_id": "test-session"}')

        with pytest.raises(ValueError, match="Session messages contain invalid JSON"):
            SessionLoader.load_session(session_folder)

    def test_load_session_invalid_json_metadata(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session with invalid JSON in metadata file."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        # Create valid messages file
        messages_file = session_folder / "messages.jsonl"
        messages_file.write_text('{"role": "user", "content": "Hello"}\n')

        # Create metadata file with invalid JSON
        metadata_file = session_folder / "meta.json"
        metadata_file.write_text("{invalid json}")

        with pytest.raises(ValueError, match="Session metadata contains invalid JSON"):
            SessionLoader.load_session(session_folder)

    def test_load_session_no_metadata_file(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session when metadata file doesn't exist."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        # Create valid messages file using the same format as create_test_session
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        messages_file = session_folder / "messages.jsonl"
        with messages_file.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(
                    json.dumps(
                        message.model_dump(exclude_none=True), ensure_ascii=False
                    )
                    + "\n"
                )

        loaded_messages, metadata = SessionLoader.load_session(session_folder)

        assert len(loaded_messages) == 2
        assert loaded_messages[0].content == "Hello"
        assert loaded_messages[0].role == Role.user
        assert loaded_messages[1].content == "Hi there!"
        assert loaded_messages[1].role == Role.assistant

        assert metadata == {}

    def test_load_session_nonexistent_directory(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session from non-existent directory."""
        nonexistent_dir = Path(session_config.save_dir) / "nonexistent"

        with pytest.raises(ValueError, match="Error reading session messages"):
            SessionLoader.load_session(nonexistent_dir)


class TestSessionLoaderEdgeCases:
    def test_find_latest_session_with_different_prefixes(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test finding latest session when sessions have different prefixes."""
        session_dir = Path(session_config.save_dir)

        # Create sessions with different prefixes
        other_session = session_dir / "other_20230101_120000_test123"
        other_session.mkdir()
        (other_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "test"}\n'
        )

        test_session = session_dir / "test_20230101_120000_test456"
        test_session.mkdir()
        (test_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "test"}\n'
        )
        (test_session / "meta.json").write_text('{"session_id": "test456"}')

        result = SessionLoader.find_latest_session(session_config)
        assert result is not None
        assert result.name == "test_20230101_120000_test456"

    def test_find_session_by_id_with_special_characters(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test finding session by ID containing special characters."""
        session_dir = Path(session_config.save_dir)
        session_folder = create_test_session(
            session_dir, "test-session_with-special.chars"
        )

        result = SessionLoader.find_session_by_id(
            "test-session_with-special.chars", session_config
        )
        assert result is not None
        assert result == session_folder

    def test_load_session_with_complex_messages(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test loading session with complex message structures."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        # Create messages with complex structure
        complex_messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(
                role=Role.user,
                content="Complex message",
                reasoning_content="Some reasoning",
                tool_calls=[ToolCall(id="call1", index=1, type="function")],
            ),
            LLMMessage(
                role=Role.assistant,
                content="Response",
                tool_calls=[ToolCall(id="call2", index=2, type="function")],
            ),
        ]

        messages_file = session_folder / "messages.jsonl"
        with messages_file.open("w", encoding="utf-8") as f:
            for message in complex_messages:
                f.write(
                    json.dumps(
                        message.model_dump(exclude_none=True), ensure_ascii=False
                    )
                    + "\n"
                )

        # Create metadata file
        metadata_file = session_folder / "meta.json"
        metadata_file.write_text('{"session_id": "complex-session"}')

        messages, _ = SessionLoader.load_session(session_folder)

        # Verify complex messages are loaded correctly
        assert len(messages) == 2
        assert messages[0].role == Role.user
        assert messages[0].content == "Complex message"
        assert messages[0].reasoning_content == "Some reasoning"
        assert len(messages[0].tool_calls or []) == 1
        assert messages[1].role == Role.assistant
        assert len(messages[1].tool_calls or []) == 1
        assert messages[1].content == "Response"

    def test_load_session_system_prompt_ignored_in_messages(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that system prompt is ignored when written in messages.jsonl."""
        session_dir = Path(session_config.save_dir)
        session_folder = session_dir / "test_20230101_120000_test123"
        session_folder.mkdir()

        messages_with_system = [
            LLMMessage(role=Role.system, content="System prompt from messages"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        messages_file = session_folder / "messages.jsonl"
        with messages_file.open("w", encoding="utf-8") as f:
            for message in messages_with_system:
                f.write(
                    json.dumps(
                        message.model_dump(exclude_none=True), ensure_ascii=False
                    )
                    + "\n"
                )

        metadata_file = session_folder / "meta.json"
        metadata_file.write_text(
            json.dumps({"session_id": "test-session", "total_messages": 3})
        )

        messages, metadata = SessionLoader.load_session(session_folder)

        # Verify that system prompt from messages.jsonl is ignored
        assert len(messages) == 2
        assert messages[0].role == Role.user
        assert messages[0].content == "Hello"
        assert messages[1].role == Role.assistant
        assert messages[1].content == "Hi there!"


@pytest.fixture
def create_test_session_with_cwd():
    def _create_session(
        session_dir: Path,
        session_id: str,
        cwd: str,
        title: str | None = None,
        end_time: str | None = None,
    ) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_folder = session_dir / f"test_{timestamp}_{session_id[:8]}"
        session_folder.mkdir(exist_ok=True)

        messages_file = session_folder / "messages.jsonl"
        messages_file.write_text('{"role": "user", "content": "Hello"}\n')

        metadata = {
            "session_id": session_id,
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": end_time or "2024-01-01T12:05:00Z",
            "environment": {"working_directory": cwd},
            "title": title,
        }

        metadata_file = session_folder / "meta.json"
        with metadata_file.open("w", encoding="utf-8") as f:
            json.dump(metadata, f)

        return session_folder

    return _create_session


class TestSessionLoaderListSessions:
    def test_list_sessions_empty(self, session_config: SessionLoggingConfig) -> None:
        result = SessionLoader.list_sessions(session_config)
        assert result == []

    def test_list_sessions_returns_all_sessions(
        self, session_config: SessionLoggingConfig, create_test_session_with_cwd
    ) -> None:
        session_dir = Path(session_config.save_dir)

        create_test_session_with_cwd(
            session_dir,
            "aaaaaaaa-1111",
            "/home/user/project1",
            title="First session",
            end_time="2024-01-01T12:00:00Z",
        )
        create_test_session_with_cwd(
            session_dir,
            "bbbbbbbb-2222",
            "/home/user/project2",
            title="Second session",
            end_time="2024-01-01T13:00:00Z",
        )

        result = SessionLoader.list_sessions(session_config)

        assert len(result) == 2
        session_ids = {s["session_id"] for s in result}
        assert "aaaaaaaa-1111" in session_ids
        assert "bbbbbbbb-2222" in session_ids

    def test_list_sessions_filters_by_cwd(
        self, session_config: SessionLoggingConfig, create_test_session_with_cwd
    ) -> None:
        session_dir = Path(session_config.save_dir)

        create_test_session_with_cwd(
            session_dir,
            "aaaaaaaa-proj1",
            "/home/user/project1",
            title="Project 1 session",
        )
        create_test_session_with_cwd(
            session_dir,
            "bbbbbbbb-proj2",
            "/home/user/project2",
            title="Project 2 session",
        )
        create_test_session_with_cwd(
            session_dir,
            "cccccccc-proj1",
            "/home/user/project1",
            title="Another Project 1 session",
        )

        result = SessionLoader.list_sessions(session_config, cwd="/home/user/project1")

        assert len(result) == 2
        for session in result:
            assert session["cwd"] == "/home/user/project1"

    def test_list_sessions_includes_all_fields(
        self, session_config: SessionLoggingConfig, create_test_session_with_cwd
    ) -> None:
        session_dir = Path(session_config.save_dir)

        create_test_session_with_cwd(
            session_dir,
            "test-session-123",
            "/home/user/project",
            title="Test Session Title",
            end_time="2024-01-15T10:30:00Z",
        )

        result = SessionLoader.list_sessions(session_config)

        assert len(result) == 1
        session = result[0]
        assert session["session_id"] == "test-session-123"
        assert session["cwd"] == "/home/user/project"
        assert session["title"] == "Test Session Title"

    def test_list_sessions_skips_invalid_sessions(
        self, session_config: SessionLoggingConfig, create_test_session_with_cwd
    ) -> None:
        session_dir = Path(session_config.save_dir)

        create_test_session_with_cwd(
            session_dir, "valid-se", "/home/user/project", title="Valid Session"
        )

        invalid_session = session_dir / "test_20240101_120000_invalid1"
        invalid_session.mkdir()
        (invalid_session / "meta.json").write_text('{"session_id": "invalid"}')

        no_id_session = session_dir / "test_20240101_120001_noid0000"
        no_id_session.mkdir()
        (no_id_session / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n'
        )
        (no_id_session / "meta.json").write_text(
            '{"environment": {"working_directory": "/test"}}'
        )

        result = SessionLoader.list_sessions(session_config)

        assert len(result) == 1
        assert result[0]["session_id"] == "valid-se"

    def test_list_sessions_nonexistent_save_dir(self) -> None:
        bad_config = SessionLoggingConfig(
            save_dir="/nonexistent/path", session_prefix="test", enabled=True
        )

        result = SessionLoader.list_sessions(bad_config)
        assert result == []

    def test_list_sessions_handles_missing_environment(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_dir = Path(session_config.save_dir)

        session_folder = session_dir / "test_20240101_120000_noenv000"
        session_folder.mkdir()
        (session_folder / "messages.jsonl").write_text(
            '{"role": "user", "content": "Hello"}\n'
        )
        (session_folder / "meta.json").write_text(
            '{"session_id": "noenv000", "end_time": "2024-01-01T12:00:00Z"}'
        )

        result = SessionLoader.list_sessions(session_config)

        assert len(result) == 1
        assert result[0]["session_id"] == "noenv000"
        assert result[0]["cwd"] == ""  # Empty string when no working_directory

    def test_list_sessions_handles_none_title(
        self, session_config: SessionLoggingConfig, create_test_session_with_cwd
    ) -> None:
        session_dir = Path(session_config.save_dir)

        create_test_session_with_cwd(
            session_dir, "notitle0", "/home/user/project", title=None
        )

        result = SessionLoader.list_sessions(session_config)

        assert len(result) == 1
        assert result[0]["session_id"] == "notitle0"
        assert result[0]["title"] is None


class TestSessionLoaderGetFirstUserMessage:
    """Tests for SessionLoader.get_first_user_message method."""

    def test_returns_first_user_message(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test that get_first_user_message returns the first user message."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="First user message"),
            LLMMessage(role=Role.assistant, content="First response"),
            LLMMessage(role=Role.user, content="Second user message"),
            LLMMessage(role=Role.assistant, content="Second response"),
        ]
        create_test_session(session_dir, "test-sess", messages=messages)

        result = SessionLoader.get_first_user_message("test-sess", session_config)

        assert result == "First user message"

    def test_returns_fallback_for_missing_session(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that get_first_user_message returns fallback when session not found."""
        result = SessionLoader.get_first_user_message("nonexistent", session_config)

        assert result == "(session not found)"

    def test_returns_no_user_messages_fallback(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test fallback when session has no user messages."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.assistant, content="Assistant only"),
        ]
        create_test_session(session_dir, "no-user0", messages=messages)

        result = SessionLoader.get_first_user_message("no-user0", session_config)

        assert result == "(no user messages)"

    def test_replaces_newlines_with_spaces(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test that newlines in messages are replaced with spaces."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Line one\nLine two\nLine three"),
        ]
        create_test_session(session_dir, "newline0", messages=messages)

        result = SessionLoader.get_first_user_message("newline0", session_config)

        assert "\n" not in result
        assert "Line one Line two Line three" == result

    def test_handles_empty_user_message(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test handling of empty user message content."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content=""),
        ]
        create_test_session(session_dir, "empty-ms", messages=messages)

        result = SessionLoader.get_first_user_message("empty-ms", session_config)

        assert result == "(no user messages)"

    def test_handles_whitespace_only_message(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test handling of whitespace-only user message."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="   \n\t  "),
        ]
        create_test_session(session_dir, "whitespc", messages=messages)

        result = SessionLoader.get_first_user_message("whitespc", session_config)

        assert result == "(empty message)"

    def test_handles_invalid_session_as_not_found(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that invalid sessions (bad JSON) are treated as not found.

        Note: Sessions with invalid JSON are filtered out by _is_valid_session
        during find_session_by_id, so they return 'not found' rather than
        'corrupted'. This is the expected behavior.
        """
        session_dir = Path(session_config.save_dir)

        # Create a session with invalid JSON - will fail validation
        session_folder = session_dir / "test_20230101_120000_corrupt0"
        session_folder.mkdir()
        (session_folder / "messages.jsonl").write_text("{invalid json}")
        (session_folder / "meta.json").write_text('{"session_id": "corrupt0"}')

        result = SessionLoader.get_first_user_message("corrupt0", session_config)

        # Invalid sessions are filtered by _is_valid_session, so not found
        assert result == "(session not found)"

    def test_skips_non_user_messages(
        self, session_config: SessionLoggingConfig, create_test_session
    ) -> None:
        """Test that only user messages are considered, not assistant/system."""
        session_dir = Path(session_config.save_dir)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="User question"),
            LLMMessage(role=Role.assistant, content="Assistant response"),
        ]
        create_test_session(session_dir, "skip-non", messages=messages)

        result = SessionLoader.get_first_user_message("skip-non", session_config)

        # Should return "User question", not "Assistant response"
        assert result == "User question"
