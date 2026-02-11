from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents.models import AgentProfile, AgentSafety
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.session.session_logger import SessionLogger
from vibe.core.tools.manager import ToolManager
from vibe.core.types import AgentStats, LLMMessage, Role, SessionMetadata


@pytest.fixture
def temp_session_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for session logging tests."""
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
def disabled_session_config() -> SessionLoggingConfig:
    """Create a disabled session logging config for testing."""
    return SessionLoggingConfig(
        save_dir="/tmp/test", session_prefix="test", enabled=False
    )


@pytest.fixture
def mock_agent_profile() -> AgentProfile:
    """Create a mock agent profile for testing."""
    return AgentProfile(
        name="test-agent",
        display_name="Test Agent",
        description="A test agent",
        safety=AgentSafety.NEUTRAL,
        overrides={},
    )


@pytest.fixture
def mock_tool_manager() -> ToolManager:
    """Create a mock tool manager for testing."""
    manager = MagicMock(spec=ToolManager)
    manager.available_tools = {}
    return manager


@pytest.fixture
def mock_vibe_config() -> VibeConfig:
    """Create a mock vibe config for testing."""
    return build_test_vibe_config(active_model="test-model", models=[], providers=[])


class TestSessionLoggerInitialization:
    def test_enabled_session_logger_initialization(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that SessionLogger initializes correctly when enabled."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.enabled is True
        assert logger.session_id == session_id
        assert logger.save_dir == Path(session_config.save_dir)
        assert logger.session_prefix == session_config.session_prefix
        assert logger.session_dir is not None
        assert logger.session_metadata is not None
        assert isinstance(logger.session_metadata, SessionMetadata)

        # Check that session directory was created
        assert logger.session_dir is not None
        assert str(logger.session_dir).startswith(str(session_config.save_dir))

        # Check session directory name format
        dir_name = logger.session_dir.name
        assert dir_name.startswith(f"{session_config.session_prefix}_")
        assert session_id[:8] in dir_name

    def test_disabled_session_logger_initialization(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that SessionLogger initializes correctly when disabled."""
        session_id = "test-session-123"
        logger = SessionLogger(disabled_session_config, session_id)

        assert logger.enabled is False
        assert logger.session_id == "disabled"
        assert logger.save_dir is None
        assert logger.session_prefix is None
        assert logger.session_dir is None
        assert logger.session_metadata is None


class TestSessionLoggerMetadata:
    @patch("vibe.core.session.session_logger.subprocess.run")
    @patch("vibe.core.session.session_logger.getpass.getuser")
    def test_session_metadata_initialization(
        self, mock_getuser, mock_subprocess, session_config: SessionLoggingConfig
    ) -> None:
        """Test that session metadata is correctly initialized."""
        # Mock git commands
        git_commit_mock = MagicMock()
        git_commit_mock.returncode = 0
        git_commit_mock.stdout = "abc123\n"

        git_branch_mock = MagicMock()
        git_branch_mock.returncode = 0
        git_branch_mock.stdout = "main\n"

        mock_subprocess.side_effect = [git_commit_mock, git_branch_mock]
        mock_getuser.return_value = "testuser"

        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_metadata is not None
        metadata = logger.session_metadata

        assert metadata.session_id == session_id
        assert metadata.start_time == logger.session_start_time
        assert metadata.end_time is None
        assert metadata.git_commit == "abc123"
        assert metadata.git_branch == "main"
        assert metadata.username == "testuser"
        assert "working_directory" in metadata.environment
        assert metadata.environment["working_directory"] == str(Path.cwd())

    @patch("vibe.core.session.session_logger.subprocess.run")
    @patch("vibe.core.session.session_logger.getpass.getuser")
    def test_session_metadata_with_git_errors(
        self, mock_getuser, mock_subprocess, session_config: SessionLoggingConfig
    ) -> None:
        """Test that session metadata handles git command errors gracefully."""
        # Mock git commands to fail
        mock_subprocess.side_effect = FileNotFoundError("git not found")
        mock_getuser.return_value = "testuser"

        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_metadata is not None
        metadata = logger.session_metadata

        assert metadata.git_commit is None
        assert metadata.git_branch is None
        assert metadata.username == "testuser"


class TestSessionLoggerSaveInteraction:
    @pytest.mark.asyncio
    async def test_save_interaction_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that save_interaction returns None when logging is disabled."""
        logger = SessionLogger(disabled_session_config, "test-session")

        result = await logger.save_interaction(
            messages=[],
            stats=AgentStats(),
            base_config=build_test_vibe_config(
                active_model="test", models=[], providers=[]
            ),
            tool_manager=MagicMock(),
            agent_profile=AgentProfile(
                name="test",
                display_name="Test",
                description="Test agent",
                safety=AgentSafety.NEUTRAL,
                overrides={},
            ),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_save_interaction_success(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that save_interaction successfully saves session data."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        # Create test messages
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        # Create test stats
        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        # Verify behavior via file system
        assert logger.session_dir is not None
        messages_file = logger.session_dir / "messages.jsonl"
        metadata_file = logger.session_dir / "meta.json"

        assert messages_file.exists()
        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)
            assert metadata["session_id"] == session_id
            assert metadata["total_messages"] == 2
            assert metadata["stats"]["steps"] == stats.steps
            assert "title" in metadata
            assert metadata["title"] == "Hello"
            assert "system_prompt" in metadata

    @pytest.mark.asyncio
    async def test_save_interaction_system_prompt_in_metadata(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that system prompt is saved in metadata and not in messages."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        assert metadata_file.exists()
        with open(metadata_file) as f:
            metadata = json.load(f)
            assert "system_prompt" in metadata
            assert metadata["system_prompt"]["content"] == "System prompt"
            assert metadata["system_prompt"]["role"] == "system"

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            lines = f.readlines()
            messages_data = [json.loads(line) for line in lines]

            assert len(messages_data) == 2
            assert messages_data[0]["role"] == "user"
            assert messages_data[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_save_interaction_with_existing_messages(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that save_interaction correctly handles existing messages."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        # First save - create initial session
        initial_messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=initial_messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        # Second save - add more messages
        new_messages = [
            LLMMessage(role=Role.user, content="How are you?"),
            LLMMessage(role=Role.assistant, content="I'm fine, thanks!"),
        ]

        all_messages = initial_messages + new_messages
        updated_stats = AgentStats(
            steps=2, session_prompt_tokens=20, session_completion_tokens=40
        )

        await logger.save_interaction(
            messages=all_messages,
            stats=updated_stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        # Verify behavior via file system: metadata was updated
        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        assert metadata_file.exists()
        with open(metadata_file) as f:
            metadata = json.load(f)
            assert metadata["total_messages"] == 4
            assert metadata["stats"]["steps"] == updated_stats.steps

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            lines = f.readlines()
            assert len(lines) == 4

    @pytest.mark.asyncio
    async def test_save_interaction_no_new_messages_is_noop(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that save_interaction does nothing when there are no new messages."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]
        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        messages_file = logger.session_dir / "messages.jsonl"

        with open(metadata_file) as f:
            meta_before = json.load(f)
        with open(messages_file) as f:
            lines_before = f.readlines()

        # Call again with same messages: no new messages, should be no-op
        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        with open(metadata_file) as f:
            meta_after = json.load(f)
        with open(messages_file) as f:
            lines_after = f.readlines()

        assert len(lines_after) == len(lines_before) == 2
        assert lines_after == lines_before
        assert meta_after["total_messages"] == meta_before["total_messages"] == 2
        assert meta_after == meta_before

    @pytest.mark.asyncio
    async def test_save_interaction_no_user_messages(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that save_interaction handles sessions with no user messages."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        # Create messages with no user messages (only system and assistant)
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        # Verify behavior via file system
        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        assert metadata_file.exists()
        with open(metadata_file) as f:
            metadata = json.load(f)
            assert metadata["session_id"] == session_id
            assert metadata["total_messages"] == 1
            assert metadata["stats"]["steps"] == stats.steps
            assert metadata["title"] == "Untitled session"

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            assert len(f.readlines()) == 1

    @pytest.mark.asyncio
    async def test_save_interaction_long_user_message(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        """Test that save_interaction truncates long user messages for title."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        # Create a long user message (more than 50 characters)
        long_message = "This is a very long user message that exceeds fifty characters and should be truncated"
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content=long_message),
            LLMMessage(role=Role.assistant, content="Response"),
        ]

        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            base_config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        # Verify behavior via file system
        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        assert metadata_file.exists()
        with open(metadata_file) as f:
            metadata = json.load(f)
            assert metadata["session_id"] == session_id
            assert metadata["total_messages"] == 2
            assert metadata["stats"]["steps"] == stats.steps
            expected_title = long_message[:50] + "…"
            assert metadata["title"] == expected_title

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            assert len(f.readlines()) == 2


class TestSessionLoggerResetSession:
    def test_reset_session(self, session_config: SessionLoggingConfig) -> None:
        """Test that reset_session correctly resets session information."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        # Store original session info
        original_session_id = logger.session_id
        original_metadata = logger.session_metadata

        # Reset session
        new_session_id = "test-session-456"
        logger.reset_session(new_session_id)

        # Verify session was reset
        assert logger.session_id == new_session_id
        assert logger.session_start_time != "N/A"  # Should be a valid timestamp
        assert logger.session_metadata is not None
        assert logger.session_metadata.session_id == new_session_id

        # Verify that metadata was recreated (different object)
        assert logger.session_metadata is not original_metadata

        assert logger.session_id != original_session_id

    def test_reset_session_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that reset_session does nothing when logging is disabled."""
        logger = SessionLogger(disabled_session_config, "test-session")

        # Reset session should not raise any errors
        logger.reset_session("new-session")

        # Verify state is unchanged
        assert logger.enabled is False
        assert logger.session_id == "disabled"


class TestSessionLoggerFileOperations:
    def test_save_folder(self, session_config: SessionLoggingConfig) -> None:
        """Test that save_folder creates correct folder name."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        folder = logger.save_folder

        assert folder.parent == Path(session_config.save_dir)
        assert folder.name.startswith(f"{session_config.session_prefix}_")
        assert session_id[:8] in folder.name

    def test_metadata_filepath(self, session_config: SessionLoggingConfig) -> None:
        """Test that metadata_filepath returns correct path."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        metadata_file = logger.metadata_filepath

        assert logger.session_dir is not None
        assert metadata_file == logger.session_dir / "meta.json"

    def test_messages_filepath(self, session_config: SessionLoggingConfig) -> None:
        """Test that messages_filepath returns correct path."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        messages_file = logger.messages_filepath

        assert logger.session_dir is not None
        assert messages_file == logger.session_dir / "messages.jsonl"

    def test_disabled_file_operations_raise_errors(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that file operations raise errors when logging is disabled."""
        logger = SessionLogger(disabled_session_config, "test-session")

        with pytest.raises(
            RuntimeError,
            match="Cannot get session save folder when logging is disabled",
        ):
            assert logger.save_folder is None

        with pytest.raises(
            RuntimeError,
            match="Cannot get session metadata filepath when logging is disabled",
        ):
            assert logger.metadata_filepath is None

        with pytest.raises(
            RuntimeError,
            match="Cannot get session messages filepath when logging is disabled",
        ):
            assert logger.messages_filepath is None


def create_temp_file_ago(tmp_path: Path, filename: str, minutes_ago: int = 0) -> Path:
    """Create a file with a modification time of `minutes_ago` minutes ago."""
    file = tmp_path / filename
    file.touch()
    old_time = datetime.now() - timedelta(minutes=minutes_ago)
    os.utime(file, (old_time.timestamp(), old_time.timestamp()))
    return file


class TestSessionLoggerCleanupTmpFiles:
    def test_cleanup_tmp_files_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        """Test that cleanup_tmp_files returns early when logging is disabled."""
        logger = SessionLogger(disabled_session_config, "test-session")

        logger.cleanup_tmp_files()

    def test_cleanup_tmp_files_no_tmp_files(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that cleanup_tmp_files handles no tmp files gracefully."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        logger.cleanup_tmp_files()

    def test_cleanup_tmp_files_deletes_old_files(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that cleanup_tmp_files deletes tmp files older than 5 minutes."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_dir is not None
        logger.session_dir.mkdir(parents=True, exist_ok=True)

        old_tmp_file = create_temp_file_ago(
            logger.session_dir, "session-123.json.tmp", 10
        )
        new_tmp_file = create_temp_file_ago(logger.session_dir, "session-123.json")

        logger.cleanup_tmp_files()

        assert not old_tmp_file.exists()
        assert new_tmp_file.exists()

    def test_cleanup_tmp_files_recursive(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that cleanup_tmp_files works recursively in subdirectories."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_dir is not None
        logger.session_dir.mkdir(parents=True, exist_ok=True)

        subdir_1 = logger.session_dir / "session-123"
        subdir_1.mkdir()

        old_tmp_file = create_temp_file_ago(subdir_1, "meta.json.tmp", 10)
        new_tmp_file = create_temp_file_ago(subdir_1, "meta.json")

        subdir_2 = logger.session_dir / "session-456"
        subdir_2.mkdir()

        old_tmp_file_2 = create_temp_file_ago(subdir_2, "meta.json.tmp", 10)

        logger.cleanup_tmp_files()

        assert not old_tmp_file.exists()
        assert not old_tmp_file_2.exists()
        assert new_tmp_file.exists()

    def test_cleanup_tmp_files_handles_exceptions(
        self, session_config: SessionLoggingConfig
    ) -> None:
        """Test that cleanup_tmp_files handles exceptions gracefully."""
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_dir is not None
        logger.session_dir.mkdir(parents=True, exist_ok=True)

        old_tmp_file = create_temp_file_ago(logger.session_dir, "meta.json.tmp", 10)
        another_old_tmp_file = create_temp_file_ago(
            logger.session_dir, "meta-002.json.tmp", 10
        )

        # Mock the unlink method to raise an exception for the first file
        original_unlink = Path.unlink

        def mock_unlink(self):
            if str(self) == str(old_tmp_file):
                raise OSError("Mocked error")
            return original_unlink(self)

        with patch.object(Path, "unlink", mock_unlink):
            logger.cleanup_tmp_files()

        assert old_tmp_file.exists()
        assert not another_old_tmp_file.exists()
