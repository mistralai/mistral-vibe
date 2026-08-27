from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents.models import AgentProfile, AgentSafety
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.experiments.models import EvalResponse
from vibe.core.loop import ScheduledLoop
from vibe.core.session.session_loader import SessionLoader
from vibe.core.session.session_logger import SessionLogger
from vibe.core.tools.manager import ToolManager
from vibe.core.types import (
    AgentStats,
    LLMMessage,
    Role,
    SessionMetadata,
    WorktreeContext,
)


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
def mock_vibe_config() -> VibeConfigSchema:
    """Create a mock vibe config for testing."""
    return build_test_vibe_config()


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
        # Mock combined git command
        git_mock = MagicMock()
        git_mock.returncode = 0
        git_mock.stdout = "abc123\nmain\n"

        mock_subprocess.return_value = git_mock
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
        assert metadata.title is None
        assert metadata.title_source == "auto"

    @patch("vibe.core.session.session_logger.subprocess.run")
    @patch("vibe.core.session.session_logger.getpass.getuser")
    def test_session_metadata_with_git_errors(
        self, mock_getuser, mock_subprocess, session_config: SessionLoggingConfig
    ) -> None:
        """Test that session metadata handles git command errors gracefully."""
        # Mock combined git command to fail
        mock_subprocess.side_effect = FileNotFoundError("git not found")
        mock_getuser.return_value = "testuser"

        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        assert logger.session_metadata is not None
        metadata = logger.session_metadata

        assert metadata.git_commit is None
        assert metadata.git_branch is None
        assert metadata.username == "testuser"


class TestSessionLoggerTitleManagement:
    def test_set_title_marks_live_session_title_as_manual(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        logger._set_title("Manual title")

        assert logger.session_metadata is not None
        assert logger.session_metadata.title == "Manual title"
        assert logger.session_metadata.title_source == "manual"

    def test_set_title_none_returns_live_session_to_auto_mode(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger._set_title("Manual title")

        logger._set_title(None)

        assert logger.session_metadata is not None
        assert logger.session_metadata.title is None
        assert logger.session_metadata.title_source == "auto"

    def test_set_title_rejects_empty_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        with pytest.raises(ValueError, match="Session title cannot be empty."):
            logger._set_title("   ")

    def test_set_title_preserves_live_session_end_time(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        assert logger.session_metadata is not None
        logger.session_metadata.end_time = "2026-01-01T10:00:00+00:00"

        logger._set_title("Manual title")

        assert logger.session_metadata.end_time == "2026-01-01T10:00:00+00:00"

    def test_set_initial_auto_title_applies_when_no_title_set(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        applied = logger.set_initial_auto_title("Pretty title")

        assert applied is True
        assert logger.session_metadata is not None
        assert logger.session_metadata.title == "Pretty title"
        assert logger.session_metadata.title_source == "auto"

    def test_set_initial_auto_title_noop_when_title_already_set(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger._set_title("Manual title")

        applied = logger.set_initial_auto_title("Pretty title")

        assert applied is False
        assert logger.session_metadata is not None
        assert logger.session_metadata.title == "Manual title"
        assert logger.session_metadata.title_source == "manual"

    def test_set_initial_auto_title_noop_when_prior_auto_title_set(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("First title")

        applied = logger.set_initial_auto_title("Second title")

        assert applied is False
        assert logger.session_metadata is not None
        assert logger.session_metadata.title == "First title"

    def test_set_initial_auto_title_rejects_blank(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        applied = logger.set_initial_auto_title("   ")

        assert applied is False
        assert logger.session_metadata is not None
        assert logger.session_metadata.title is None

    def test_needs_initial_auto_title_true_when_no_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        assert logger.needs_initial_auto_title() is True

    def test_needs_initial_auto_title_false_after_set_initial_auto_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Pretty title")

        assert logger.needs_initial_auto_title() is False

    def test_needs_initial_auto_title_false_after_manual_set_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger._set_title("Manual title")

        assert logger.needs_initial_auto_title() is False

    def test_needs_initial_auto_title_true_when_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(disabled_session_config, "test-session-123")

        assert logger.needs_initial_auto_title() is True

    def test_title_source_defaults_to_auto(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        assert logger.title_source == "auto"

    def test_title_source_auto_when_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(disabled_session_config, "test-session-123")

        assert logger.title_source == "auto"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_overwrites_prior_auto_title(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("First message based title")

        changed = await logger.refresh_auto_title("Concise generated title")

        assert changed is True
        assert logger.title == "Concise generated title"
        assert logger.title_source == "auto"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_never_overrides_manual(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger._set_title("Manual title")

        changed = await logger.refresh_auto_title("Generated title")

        assert changed is False
        assert logger.title == "Manual title"
        assert logger.title_source == "manual"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_noop_when_unchanged(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Same title")

        assert await logger.refresh_auto_title("Same title") is False

    @pytest.mark.asyncio
    async def test_refresh_auto_title_noop_when_blank(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Existing")

        assert await logger.refresh_auto_title("   ") is False
        assert logger.title == "Existing"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_persists_to_disk(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await logger.refresh_auto_title("Persisted generated title")

        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Persisted generated title"
        assert metadata.title_source == "auto"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_skips_a_title_for_a_reset_session(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        # A background generation that outlived a /new or /clear reset carries the
        # old session id; the logger (reset in place) must not persist it.
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        changed = await logger.refresh_auto_title(
            "Stale title", expected_session_id="a-different-session"
        )

        assert changed is False
        assert logger.title == "Auto title"
        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Auto title"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_keeps_state_consistent_on_read_failure(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        async def boom(*_args, **_kwargs):
            raise OSError("disk gone")

        monkeypatch.setattr("vibe.core.session.session_logger.read_safe_async", boom)

        with pytest.raises(RuntimeError, match="Failed to read session metadata"):
            await logger.refresh_auto_title("Generated title")

        # The flip never landed, so memory and disk stay consistent.
        assert logger.title == "Auto title"
        assert logger.title_source == "auto"
        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Auto title"
        assert metadata.title_source == "auto"

    @pytest.mark.asyncio
    async def test_save_interaction_keeps_refreshed_title(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )
        await logger.refresh_auto_title("Generated title")

        await logger.save_interaction(
            [
                LLMMessage(role=Role.user, content="hi"),
                LLMMessage(role=Role.assistant, content="hello"),
            ],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Generated title"
        assert metadata.title_source == "auto"

    @pytest.mark.asyncio
    async def test_refresh_auto_title_rechecks_manual_under_lock(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")

        # Hold the lock so the refresh blocks past its outer guard, then rename
        # before releasing: the refresh must re-check and leave the manual title.
        await logger._save_lock.acquire()
        refresh = asyncio.create_task(logger.refresh_auto_title("Generated title"))
        await asyncio.sleep(0)
        logger._set_title("Manual rename")
        logger._save_lock.release()

        assert await refresh is False
        assert logger.title == "Manual rename"
        assert logger.title_source == "manual"

    @pytest.mark.asyncio
    async def test_apply_manual_title_persists_manual_to_disk(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        updated_at = await logger.apply_manual_title("  Reviewed session  ")

        assert isinstance(updated_at, str)
        assert logger.title == "Reviewed session"
        assert logger.title_source == "manual"
        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Reviewed session"
        assert metadata.title_source == "manual"

    @pytest.mark.asyncio
    async def test_apply_manual_title_rejects_empty(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        with pytest.raises(ValueError, match="cannot be empty"):
            await logger.apply_manual_title("   ")

    @pytest.mark.asyncio
    async def test_apply_manual_title_blocks_racing_auto_refresh(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")

        # Rename flips memory to manual under the lock, so a later refresh bails.
        await logger.apply_manual_title("Manual rename")

        assert await logger.refresh_auto_title("Generated title") is False
        assert logger.title == "Manual rename"
        assert logger.title_source == "manual"

    @pytest.mark.asyncio
    async def test_apply_manual_title_keeps_state_consistent_on_read_failure(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        async def boom(*_args, **_kwargs):
            raise OSError("disk gone")

        monkeypatch.setattr("vibe.core.session.session_logger.read_safe_async", boom)

        with pytest.raises(RuntimeError, match="Failed to read session metadata"):
            await logger.apply_manual_title("Manual rename")

        # The flip never landed, so memory and disk stay consistent (auto).
        assert logger.title == "Auto title"
        assert logger.title_source == "auto"
        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Auto title"
        assert metadata.title_source == "auto"

    @pytest.mark.asyncio
    async def test_apply_manual_title_keeps_state_consistent_on_write_failure(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        async def boom(*_args, **_kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(
            "vibe.core.session.session_logger.SessionLogger.persist_metadata", boom
        )

        with pytest.raises(RuntimeError, match="disk full"):
            await logger.apply_manual_title("Manual rename")

        # The write failed before the flip, so memory stays auto (not manual),
        # leaving auto-refresh unblocked and memory consistent with disk.
        assert logger.title == "Auto title"
        assert logger.title_source == "auto"

    @pytest.mark.asyncio
    async def test_concurrent_rename_and_auto_refresh_leaves_manual_title(
        self,
        session_config: SessionLoggingConfig,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        # The two-writer invariant: a manual rename always wins a race with the
        # background auto-refresh, on disk and in memory, whichever runs first.
        logger = SessionLogger(session_config, "test-session-123")
        logger.set_initial_auto_title("Auto title")
        await logger.save_interaction(
            [LLMMessage(role=Role.user, content="hi")],
            stats=AgentStats(),
            config=build_test_vibe_config(),
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await asyncio.gather(
            logger.refresh_auto_title("Generated title"),
            logger.apply_manual_title("Manual rename"),
        )

        assert logger.title == "Manual rename"
        assert logger.title_source == "manual"
        assert logger.session_dir is not None
        metadata = SessionLoader.load_metadata(logger.session_dir)
        assert metadata.title == "Manual rename"
        assert metadata.title_source == "manual"
        # A later auto-refresh must still bail against the manual title.
        assert await logger.refresh_auto_title("Another title") is False


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
            config=build_test_vibe_config(),
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
        mock_vibe_config: VibeConfigSchema,
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
            config=mock_vibe_config,
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
            # No title until the background model generates one.
            assert metadata["title"] is None
            assert metadata["title_source"] == "auto"
            assert "system_prompt" in metadata

    @pytest.mark.asyncio
    async def test_save_interaction_system_prompt_in_metadata(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
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
            config=mock_vibe_config,
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
        mock_vibe_config: VibeConfigSchema,
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
            config=mock_vibe_config,
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
            config=mock_vibe_config,
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
        mock_vibe_config: VibeConfigSchema,
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
            config=mock_vibe_config,
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
            config=mock_vibe_config,
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
        mock_vibe_config: VibeConfigSchema,
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
            config=mock_vibe_config,
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
            assert metadata["title"] is None
            assert metadata["title_source"] == "auto"

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            assert len(f.readlines()) == 1

    @pytest.mark.asyncio
    async def test_save_interaction_preserves_preset_auto_title(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)
        assert logger.session_metadata is not None

        logger.set_initial_auto_title("Pretty @foo.py title")

        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(
                role=Role.user, content="path: file:///abs/foo.py\ncontent: ..."
            ),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]
        stats = AgentStats(
            steps=1, session_prompt_tokens=10, session_completion_tokens=20
        )

        await logger.save_interaction(
            messages=messages,
            stats=stats,
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["title"] == "Pretty @foo.py title"
        assert metadata["title_source"] == "auto"

    @pytest.mark.asyncio
    async def test_save_interaction_preserves_manual_title(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)
        assert logger.session_metadata is not None

        logger._set_title("Manual title")

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
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        metadata_file = logger.session_dir / "meta.json"
        with open(metadata_file) as f:
            metadata = json.load(f)

        assert metadata["title"] == "Manual title"
        assert metadata["title_source"] == "manual"

        messages_file = logger.session_dir / "messages.jsonl"
        assert messages_file.exists()
        with open(messages_file) as f:
            assert len(f.readlines()) == 2

    @pytest.mark.asyncio
    async def test_save_interaction_throttles_tmp_cleanup(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-123")

        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]

        cleanup_spy = MagicMock()
        with (
            patch.object(
                SessionLogger, "_persist_messages_sync"
            ) as persist_messages_mock,
            patch.object(
                SessionLogger, "_persist_metadata_sync"
            ) as persist_metadata_mock,
            patch.object(logger, "cleanup_tmp_files", cleanup_spy),
            patch(
                "vibe.core.session.session_logger.utc_now",
                # a bit brittle, but required for the call-count choregraphy...
                side_effect=[
                    datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
                    datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
                    datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC),
                    datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC),
                ],
            ),
        ):
            await logger.save_interaction(
                messages=messages,
                stats=AgentStats(steps=1),
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )
            await logger.save_interaction(
                messages=messages,
                stats=AgentStats(steps=2),
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )

        assert persist_messages_mock.call_count == 2
        assert persist_metadata_mock.call_count == 2
        assert cleanup_spy.call_count == 1

    @pytest.mark.asyncio
    async def test_save_interaction_rewrites_log_when_history_shrinks(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "shrink-history")
        stats = AgentStats(steps=1)
        full = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
            LLMMessage(role=Role.user, content="B"),
            LLMMessage(role=Role.assistant, content="response B"),
        ]
        await logger.save_interaction(
            messages=full,
            stats=stats,
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await logger.save_interaction(
            messages=full[:3],
            stats=stats,
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        with open(logger.session_dir / "messages.jsonl") as f:
            lines = [json.loads(line) for line in f]
        assert [m["content"] for m in lines] == ["A", "response A"]
        with open(logger.session_dir / "meta.json") as f:
            assert json.load(f)["total_messages"] == 2

    @pytest.mark.asyncio
    async def test_save_interaction_replaces_tail_after_shrink_and_regrow(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "shrink-regrow")
        stats = AgentStats(steps=1)

        async def save(messages: list[LLMMessage]) -> None:
            await logger.save_interaction(
                messages=messages,
                stats=stats,
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )

        system = LLMMessage(role=Role.system, content="System prompt")
        first_turn = [
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
        ]
        await save([
            *first_turn,
            LLMMessage(role=Role.user, content="B"),
            LLMMessage(role=Role.assistant, content="response B"),
        ])
        await save(first_turn)
        await save([
            *first_turn,
            LLMMessage(role=Role.user, content="B-bis"),
            LLMMessage(role=Role.assistant, content="response B-bis"),
        ])

        assert logger.session_dir is not None
        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == [
            "A",
            "response A",
            "B-bis",
            "response B-bis",
        ]
        assert metadata["total_messages"] == 4

    @pytest.mark.asyncio
    async def test_save_interaction_skips_system_only_history_then_reprompt_replaces_it(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "reduced-to-system")
        stats = AgentStats(steps=1)
        system = LLMMessage(role=Role.system, content="System prompt")

        async def save(messages: list[LLMMessage]) -> None:
            await logger.save_interaction(
                messages=messages,
                stats=stats,
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )

        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
        ])

        # Reducing the history to only the system prompt is a no-op: the prior
        # log stays in place (never emptied) so the session remains loadable.
        await save([system])
        assert logger.session_dir is not None
        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == ["A", "response A"]
        assert metadata["total_messages"] == 2

        # The next real message replaces the stale tail.
        await save([
            system,
            LLMMessage(role=Role.user, content="A-bis"),
            LLMMessage(role=Role.assistant, content="response A-bis"),
        ])

        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == ["A-bis", "response A-bis"]
        assert metadata["total_messages"] == 2

    @pytest.mark.asyncio
    async def test_save_interaction_rewrites_log_when_last_message_changes_at_same_count(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "same-count-diff-tail")
        stats = AgentStats(steps=1)
        system = LLMMessage(role=Role.system, content="System prompt")

        async def save(messages: list[LLMMessage]) -> None:
            await logger.save_interaction(
                messages=messages,
                stats=stats,
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )

        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
        ])

        # Same message count, but the last message content differs: the count
        # alone would treat this as a no-op, so the fingerprint must catch it.
        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A (edited)"),
        ])

        assert logger.session_dir is not None
        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == ["A", "response A (edited)"]
        assert metadata["total_messages"] == 2

    @pytest.mark.asyncio
    async def test_save_interaction_rewrites_log_for_legacy_session_without_fingerprint(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "legacy-no-fingerprint")
        stats = AgentStats(steps=1)
        system = LLMMessage(role=Role.system, content="System prompt")

        async def save(messages: list[LLMMessage]) -> None:
            await logger.save_interaction(
                messages=messages,
                stats=stats,
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
            )

        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
        ])

        # Simulate a session written before fingerprints existed.
        metadata = json.loads(logger.metadata_filepath.read_text(encoding="utf-8"))
        del metadata["last_message_fingerprint"]
        logger.metadata_filepath.write_text(json.dumps(metadata), encoding="utf-8")

        # Same count, edited tail: with no fingerprint the boundary can't be
        # verified, so the log must be fully rewritten rather than no-op'd.
        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A (edited)"),
        ])

        assert logger.session_dir is not None
        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == ["A", "response A (edited)"]
        assert metadata["total_messages"] == 2

    @pytest.mark.asyncio
    async def test_save_interaction_persists_empty_conversation_only_when_allowed(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "rewind-to-top")
        stats = AgentStats(steps=1)
        system = LLMMessage(role=Role.system, content="System prompt")

        async def save(
            messages: list[LLMMessage], *, allow_empty: bool = False
        ) -> None:
            await logger.save_interaction(
                messages=messages,
                stats=stats,
                config=mock_vibe_config,
                tool_manager=mock_tool_manager,
                agent_profile=mock_agent_profile,
                allow_empty=allow_empty,
            )

        await save([
            system,
            LLMMessage(role=Role.user, content="A"),
            LLMMessage(role=Role.assistant, content="response A"),
        ])

        # A system-only save without opt-in keeps the prior log intact.
        await save([system])
        assert logger.session_dir is not None
        loaded, _ = SessionLoader.load_session(logger.session_dir)
        assert [m.content for m in loaded] == ["A", "response A"]

        # An in-place rewind to the first message opts in: the emptied log is
        # persisted and loads back as an empty, valid session.
        await save([system], allow_empty=True)
        loaded, metadata = SessionLoader.load_session(logger.session_dir)
        assert loaded == []
        assert metadata["total_messages"] == 0


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

    def test_maybe_cleanup_tmp_files_throttles_calls(
        self, session_config: SessionLoggingConfig
    ) -> None:
        session_id = "test-session-123"
        logger = SessionLogger(session_config, session_id)

        cleanup_spy = MagicMock()
        with (
            patch.object(logger, "cleanup_tmp_files", cleanup_spy),
            patch(
                "vibe.core.session.session_logger.utc_now",
                side_effect=[
                    datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
                    datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC),
                    datetime(2026, 1, 1, 10, 0, 6, tzinfo=UTC),
                ],
            ),
        ):
            logger.maybe_cleanup_tmp_files()
            logger.maybe_cleanup_tmp_files()
            logger.maybe_cleanup_tmp_files()

        assert cleanup_spy.call_count == 2


class TestPersistLoops:
    @pytest.mark.asyncio
    async def test_writes_into_existing_metadata(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "test-session-loops")
        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
                LLMMessage(role=Role.assistant, content="Hi there!"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_metadata is not None
        logger.session_metadata.loops = [
            ScheduledLoop(
                id="aabbccdd",
                interval_seconds=30,
                prompt="ping",
                next_fire_at=12345.0,
                created_at=12000.0,
            )
        ]
        await logger.persist_loops()

        assert logger.session_dir is not None
        with open(logger.session_dir / "meta.json") as f:
            metadata = json.load(f)

        assert metadata["session_id"] == "test-session-loops"
        assert metadata["total_messages"] == 2
        assert metadata["loops"] == [
            {
                "id": "aabbccdd",
                "interval_seconds": 30,
                "prompt": "ping",
                "next_fire_at": 12345.0,
                "created_at": 12000.0,
            }
        ]

    @pytest.mark.asyncio
    async def test_noop_when_metadata_file_missing(
        self, session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(session_config, "no-meta-session")
        assert logger.session_dir is not None
        # No save_interaction was called -> meta.json does not exist
        assert not (logger.session_dir / "meta.json").exists()

        assert logger.session_metadata is not None
        logger.session_metadata.loops = [
            ScheduledLoop(
                id="aabbccdd",
                interval_seconds=30,
                prompt="ping",
                next_fire_at=1.0,
                created_at=0.0,
            )
        ]
        await logger.persist_loops()

        assert not (logger.session_dir / "meta.json").exists()

    @pytest.mark.asyncio
    async def test_noop_when_logging_disabled(
        self, disabled_session_config: SessionLoggingConfig
    ) -> None:
        logger = SessionLogger(disabled_session_config, "ignored")
        # Should not raise even though session_metadata is None
        await logger.persist_loops()

    @pytest.mark.asyncio
    async def test_subsequent_save_interaction_preserves_loops(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "loops-vs-save")
        messages = [
            LLMMessage(role=Role.system, content="System prompt"),
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]
        await logger.save_interaction(
            messages=messages,
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_metadata is not None
        logger.session_metadata.loops = [
            ScheduledLoop(
                id="aabbccdd",
                interval_seconds=30,
                prompt="ping",
                next_fire_at=12345.0,
                created_at=12000.0,
            )
        ]
        await logger.persist_loops()

        # A subsequent save (e.g. user sends another message) must not
        # overwrite the on-disk loops with the stale in-memory value.
        more_messages = [*messages, LLMMessage(role=Role.user, content="Again")]
        await logger.save_interaction(
            messages=more_messages,
            stats=AgentStats(steps=2),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        assert logger.session_dir is not None
        with open(logger.session_dir / "meta.json") as f:
            metadata = json.load(f)
        assert len(metadata["loops"]) == 1
        assert metadata["loops"][0]["id"] == "aabbccdd"


class TestPersistExperiments:
    @pytest.fixture
    def sample_response(self) -> EvalResponse:
        return EvalResponse.model_validate({
            "features": {
                "vibe_code_cli_test_ab": {
                    "defaultValue": "cli",
                    "rules": [
                        {
                            "force": "cli_v2",
                            "tracks": [
                                {
                                    "experiment": {"key": "vibe_code_cli_test_ab"},
                                    "result": {
                                        "key": "1",
                                        "variationId": 1,
                                        "inExperiment": True,
                                    },
                                }
                            ],
                        }
                    ],
                }
            }
        })

    @pytest.mark.asyncio
    async def test_writes_field_into_existing_metadata(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        sample_response: EvalResponse,
    ) -> None:
        logger = SessionLogger(session_config, "exp-session")
        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await logger.persist_experiments(sample_response)

        assert logger.session_dir is not None
        with open(logger.session_dir / "meta.json") as f:
            metadata = json.load(f)
        assert "experiments" in metadata
        assert (
            metadata["experiments"]["features"]["vibe_code_cli_test_ab"]["defaultValue"]
            == "cli"
        )

    @pytest.mark.asyncio
    async def test_persists_none_as_null(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
    ) -> None:
        logger = SessionLogger(session_config, "exp-none")
        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="x"),
                LLMMessage(role=Role.user, content="y"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await logger.persist_experiments(None)

        assert logger.session_dir is not None
        with open(logger.session_dir / "meta.json") as f:
            metadata = json.load(f)
        assert metadata.get("experiments") is None

    @pytest.mark.asyncio
    async def test_does_not_create_metadata_file_when_missing(
        self, session_config: SessionLoggingConfig, sample_response: EvalResponse
    ) -> None:
        # Sessions without any message must not be persisted at all —
        # persist_experiments updates only in-memory state when meta.json is
        # absent, and lets the eventual save_interaction write it.
        logger = SessionLogger(session_config, "fresh-session")
        assert logger.session_dir is not None
        assert not (logger.session_dir / "meta.json").exists()

        await logger.persist_experiments(sample_response)

        assert not (logger.session_dir / "meta.json").exists()
        assert logger.session_metadata is not None
        assert logger.session_metadata.experiments == sample_response

    @pytest.mark.asyncio
    async def test_noop_when_logging_disabled(
        self,
        disabled_session_config: SessionLoggingConfig,
        sample_response: EvalResponse,
    ) -> None:
        logger = SessionLogger(disabled_session_config, "ignored")
        await logger.persist_experiments(sample_response)

    @pytest.mark.asyncio
    async def test_first_save_interaction_includes_in_memory_experiments(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        sample_response: EvalResponse,
    ) -> None:
        # Real flow: persist_experiments at session start (no meta.json yet,
        # in-memory only). The first save_interaction must succeed AND
        # include the experiments snapshot in the eventual meta.json.
        logger = SessionLogger(session_config, "first-save-after-experiments")
        await logger.persist_experiments(sample_response)

        assert logger.session_dir is not None
        assert not (logger.session_dir / "meta.json").exists()

        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
                LLMMessage(role=Role.assistant, content="Hi"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        with open(logger.session_dir / "meta.json") as f:
            metadata = json.load(f)
        assert metadata["total_messages"] == 2
        assert (
            metadata["experiments"]["features"]["vibe_code_cli_test_ab"]["defaultValue"]
            == "cli"
        )


class TestPersistCreatedWorktree:
    @pytest.fixture
    def worktree(self) -> WorktreeContext:
        return WorktreeContext(
            entry_id="worktree-feature-x",
            name="feature-x",
            branch="vibe/feature-x",
            path="/repo/.worktrees/feature-x",
            created_at=1234567890,
        )

    @pytest.mark.asyncio
    async def test_a_fresh_session_carries_the_worktree_into_the_first_save(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        worktree: WorktreeContext,
    ) -> None:
        # The worktree is recorded before the session has a message, so there is
        # no meta.json to patch yet: only the in-memory metadata can carry it
        # until the first full save writes it out.
        logger = SessionLogger(session_config, "worktree-session")
        assert logger.session_dir is not None
        assert not (logger.session_dir / "meta.json").exists()

        await logger.persist_created_worktree(worktree)

        assert not (logger.session_dir / "meta.json").exists()

        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        reloaded = SessionLoader.load_metadata(logger.session_dir)
        assert reloaded.created_worktree == worktree

    @pytest.mark.asyncio
    async def test_an_existing_metadata_file_is_patched_without_a_full_save(
        self,
        session_config: SessionLoggingConfig,
        mock_vibe_config: VibeConfigSchema,
        mock_tool_manager: ToolManager,
        mock_agent_profile: AgentProfile,
        worktree: WorktreeContext,
    ) -> None:
        logger = SessionLogger(session_config, "worktree-resumed")
        await logger.save_interaction(
            messages=[
                LLMMessage(role=Role.system, content="System prompt"),
                LLMMessage(role=Role.user, content="Hello"),
            ],
            stats=AgentStats(steps=1),
            config=mock_vibe_config,
            tool_manager=mock_tool_manager,
            agent_profile=mock_agent_profile,
        )

        await logger.persist_created_worktree(worktree)

        assert logger.session_dir is not None
        reloaded = SessionLoader.load_metadata(logger.session_dir)
        assert reloaded.created_worktree == worktree


def _legacy_metadata(working_directory: Path) -> SessionMetadata:
    """Metadata as written before ``origin_directory`` existed."""
    return SessionMetadata(
        session_id="s1",
        start_time="2026-01-01T00:00:00+00:00",
        end_time=None,
        git_commit=None,
        git_branch=None,
        username="tester",
        environment={"working_directory": str(working_directory)},
    )


def test_a_legacy_session_keeps_its_origin_when_it_first_moves(
    tmp_path: Path, session_config
) -> None:
    # Sessions written before origin_directory existed, and imported ones, carry
    # only the environment entry. Overwriting it on the first move would leave
    # nothing naming where the session started, so it would vanish from that
    # directory: the disappearance this pair of fields exists to prevent.
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    logger = SessionLogger(session_config, "s1")
    logger.session_metadata = _legacy_metadata(repo)

    logger.relocated_to(worktree)

    metadata = logger.session_metadata.model_dump()
    assert SessionLoader._session_reaches(metadata, repo)
    assert SessionLoader._session_reaches(metadata, worktree)


def test_a_second_move_does_not_overwrite_the_promoted_origin(
    tmp_path: Path, session_config
) -> None:
    repo = tmp_path / "repo"
    first = tmp_path / "first"
    second = tmp_path / "second"
    logger = SessionLogger(session_config, "s1")
    logger.session_metadata = _legacy_metadata(repo)

    logger.relocated_to(first)
    logger.relocated_to(second)

    metadata = logger.session_metadata.model_dump()
    assert SessionLoader._session_reaches(metadata, repo)
    assert SessionLoader._session_reaches(metadata, second)
    assert not SessionLoader._session_reaches(metadata, first)
