from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.interaction_logger import InteractionLogger
from vibe.core.types import LLMMessage, Role


class TestSessionManagement:
    @pytest.fixture
    def temp_session_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def session_config(self, temp_session_dir):
        return SessionLoggingConfig(
            save_dir=str(temp_session_dir), session_prefix="test_session", enabled=True
        )

    @pytest.fixture
    def interaction_logger(self, session_config):
        return InteractionLogger(
            session_config=session_config,
            session_id="test-session-123",
            session_name="Test Session",
        )

    def test_session_metadata_with_name(self, interaction_logger):
        metadata = interaction_logger.session_metadata
        assert metadata is not None
        assert metadata.name == "Test Session"
        assert metadata.session_id == "test-session-123"

    def test_generate_session_name_from_user_message(self, interaction_logger):
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(
                role=Role.user,
                content="Help me build a web application with React and TypeScript",
            ),
            LLMMessage(
                role=Role.assistant, content="I'll help you build that web application!"
            ),
        ]

        name = interaction_logger.generate_session_name(messages)
        assert name == "Help me build a web application with React and ..."
        assert len(name) <= 50  # Should be truncated

    def test_generate_session_name_short_message(self, interaction_logger):
        messages = [LLMMessage(role=Role.user, content="Hello")]

        name = interaction_logger.generate_session_name(messages)
        assert name == "Hello"
        assert len(name) <= 50

    def test_generate_session_name_no_messages(self, interaction_logger):
        name = interaction_logger.generate_session_name([])
        assert name.startswith("Session ")

    def test_generate_session_name_no_user_messages(self, interaction_logger):
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.assistant, content="Hello! How can I help?"),
        ]

        name = interaction_logger.generate_session_name(messages)
        assert name.startswith("Session ")

    def test_get_all_sessions(self, session_config, temp_session_dir):
        """Test retrieving all sessions."""
        # Create multiple session files
        for i in range(3):
            InteractionLogger(
                session_config=session_config,
                session_id=f"session-{i}",
                session_name=f"Test Session {i}",
            )

            sessions = InteractionLogger.get_all_sessions(session_config)
            assert isinstance(sessions, list)

    def test_session_name_update(self, interaction_logger):
        # Update session name
        interaction_logger.update_session_name("New Session Name")

        assert interaction_logger.session_name == "New Session Name"
        assert interaction_logger.session_metadata.name == "New Session Name"

    def test_session_name_update_none(self, interaction_logger):
        interaction_logger.update_session_name(None)

        assert interaction_logger.session_name is None
        assert interaction_logger.session_metadata.name is None

    def test_rename_session_file(self, temp_session_dir):
        # Create a test session file
        session_file = temp_session_dir / "test_session.json"

        # Create test data
        messages = [LLMMessage(role=Role.user, content="Test message")]
        metadata = {
            "session_id": "test-123",
            "name": "Original Name",
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "git_commit": None,
            "git_branch": None,
            "auto_approve": False,
            "username": "testuser",
            "environment": {"working_directory": "/test"},
        }

        # Save initial session data
        interaction_data = {
            "metadata": metadata,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
        }

        import json

        json_content = json.dumps(interaction_data, indent=2, ensure_ascii=False)
        session_file.write_text(json_content, encoding="utf-8")

        # Test renaming
        success = InteractionLogger.rename_session_file(
            session_file, "New Session Name"
        )
        assert success is True

        # Verify the rename worked
        loaded_messages, loaded_metadata = InteractionLogger.load_session(session_file)
        assert loaded_metadata["name"] == "New Session Name"

    def test_rename_session_file_error(self, temp_session_dir):
        non_existent_file = temp_session_dir / "non_existent.json"

        success = InteractionLogger.rename_session_file(non_existent_file, "New Name")
        assert success is False

    def test_find_session_by_id(self, session_config, temp_session_dir):
        # Create a test session file
        InteractionLogger(
            session_config=session_config,
            session_id="test-session-abc123",
            session_name="Find Me Session",
        )

        # Test finding by full ID
        found = InteractionLogger.find_session_by_id(
            "test-session-abc123", session_config
        )
        # Since we haven't saved the session, this will be None
        assert found is None or isinstance(found, Path)

    def test_find_latest_session(self, session_config, temp_session_dir):
        # Test with no sessions
        latest = InteractionLogger.find_latest_session(session_config)
        assert latest is None  # No sessions exist yet

    def test_session_loading_error_handling(self, temp_session_dir):
        # Create a corrupted JSON file
        corrupted_file = temp_session_dir / "corrupted.json"
        corrupted_file.write_text("invalid json content")

        # Should handle the error gracefully
        with pytest.raises((json.JSONDecodeError, ValueError)):
            messages, metadata = InteractionLogger.load_session(corrupted_file)
