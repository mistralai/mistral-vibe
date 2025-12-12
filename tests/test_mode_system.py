"""Comprehensive Test Suite for ChefChat Mode System
==================================================

Tests for:
- Mode cycling logic
- Tool permission checks
- Write operation detection
- System prompt injection
- Integration with Agent
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chefchat.cli.mode_manager import (
    MODE_CONFIGS,
    MODE_CYCLE_ORDER,
    READONLY_TOOLS,
    WRITE_TOOLS,
    ModeAwareToolExecutor,
    ModeConfig,
    ModeManager,
    ModeState,
    VibeMode,
    get_mode_banner,
    inject_mode_into_system_prompt,
    mode_from_auto_approve,
)

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def manager() -> ModeManager:
    """Create a fresh ModeManager in NORMAL mode."""
    return ModeManager(initial_mode=VibeMode.NORMAL)


@pytest.fixture
def plan_manager() -> ModeManager:
    """Create a ModeManager in PLAN mode (read-only)."""
    return ModeManager(initial_mode=VibeMode.PLAN)


@pytest.fixture
def auto_manager() -> ModeManager:
    """Create a ModeManager in AUTO mode (auto-approve all)."""
    return ModeManager(initial_mode=VibeMode.AUTO)


@pytest.fixture
def yolo_manager() -> ModeManager:
    """Create a ModeManager in YOLO mode (ultra-fast)."""
    return ModeManager(initial_mode=VibeMode.YOLO)


@pytest.fixture
def architect_manager() -> ModeManager:
    """Create a ModeManager in ARCHITECT mode (design-only)."""
    return ModeManager(initial_mode=VibeMode.ARCHITECT)


# =============================================================================
# UNIT TESTS: VibeMode Enum
# =============================================================================


class TestVibeModeEnum:
    """Tests for the VibeMode enum."""

    def test_all_modes_defined(self) -> None:
        """Verify all 5 modes are defined."""
        assert len(VibeMode) == 5
        assert VibeMode.PLAN in VibeMode
        assert VibeMode.NORMAL in VibeMode
        assert VibeMode.AUTO in VibeMode
        assert VibeMode.YOLO in VibeMode
        assert VibeMode.ARCHITECT in VibeMode

    def test_mode_values_are_strings(self) -> None:
        """Verify modes have string values."""
        for mode in VibeMode:
            assert isinstance(mode.value, str)
            assert len(mode.value) > 0


# =============================================================================
# UNIT TESTS: ModeState Dataclass
# =============================================================================


class TestModeState:
    """Tests for the ModeState dataclass."""

    def test_initial_state_from_mode(self) -> None:
        """State should initialize from mode config."""
        state = ModeState(current_mode=VibeMode.PLAN)
        assert state.current_mode == VibeMode.PLAN
        assert state.auto_approve is False
        assert state.read_only_tools is True

    def test_normal_mode_state(self) -> None:
        """NORMAL mode should not auto-approve or be read-only."""
        state = ModeState(current_mode=VibeMode.NORMAL)
        assert state.auto_approve is False
        assert state.read_only_tools is False

    def test_auto_mode_state(self) -> None:
        """AUTO mode should auto-approve."""
        state = ModeState(current_mode=VibeMode.AUTO)
        assert state.auto_approve is True
        assert state.read_only_tools is False

    def test_yolo_mode_state(self) -> None:
        """YOLO mode should auto-approve."""
        state = ModeState(current_mode=VibeMode.YOLO)
        assert state.auto_approve is True
        assert state.read_only_tools is False

    def test_architect_mode_state(self) -> None:
        """ARCHITECT mode should be read-only."""
        state = ModeState(current_mode=VibeMode.ARCHITECT)
        assert state.auto_approve is False
        assert state.read_only_tools is True

    def test_mode_history_initialized(self) -> None:
        """Mode history should have initial entry."""
        state = ModeState(current_mode=VibeMode.NORMAL)
        assert len(state.mode_history) == 1
        assert state.mode_history[0][0] == VibeMode.NORMAL

    def test_to_dict_serialization(self) -> None:
        """State should serialize to dict."""
        state = ModeState(current_mode=VibeMode.PLAN)
        d = state.to_dict()
        assert d["mode"] == "plan"
        assert d["auto_approve"] is False
        assert d["read_only"] is True
        assert "started_at" in d
        assert "transitions" in d


# =============================================================================
# UNIT TESTS: ModeManager - Initialization
# =============================================================================


class TestModeManagerInit:
    """Tests for ModeManager initialization."""

    def test_default_mode_is_normal(self) -> None:
        """Default initial mode should be NORMAL."""
        m = ModeManager()
        assert m.current_mode == VibeMode.NORMAL

    def test_custom_initial_mode(self) -> None:
        """Can initialize with custom mode."""
        for mode in VibeMode:
            m = ModeManager(initial_mode=mode)
            assert m.current_mode == mode

    def test_properties_match_config(self) -> None:
        """Properties should match mode config."""
        for mode in VibeMode:
            m = ModeManager(initial_mode=mode)
            config = MODE_CONFIGS[mode]
            assert m.auto_approve == config.auto_approve
            assert m.read_only_tools == config.read_only


# =============================================================================
# UNIT TESTS: ModeManager - Mode Cycling
# =============================================================================


class TestModeManagerCycling:
    """Tests for mode cycling (Shift+Tab behavior)."""

    def test_cycle_order(self, manager: ModeManager) -> None:
        """Should cycle through modes in correct order."""
        expected = ["auto", "plan", "yolo", "architect", "normal"]
        for expected_mode in expected:
            old, new = manager.cycle_mode()
            assert new.value == expected_mode

    def test_cycle_wraps_around(self, manager: ModeManager) -> None:
        """After cycling through all modes, should return to start."""
        initial = manager.current_mode
        for _ in range(len(MODE_CYCLE_ORDER)):
            manager.cycle_mode()
        assert manager.current_mode == initial

    def test_cycle_returns_old_and_new(self, manager: ModeManager) -> None:
        """cycle_mode should return both old and new modes."""
        old, new = manager.cycle_mode()
        assert old == VibeMode.NORMAL
        assert new == VibeMode.AUTO

    def test_set_mode_directly(self, manager: ModeManager) -> None:
        """Can set mode directly without cycling."""
        manager.set_mode(VibeMode.YOLO)
        assert manager.current_mode == VibeMode.YOLO
        assert manager.auto_approve is True

    def test_mode_history_tracked(self, manager: ModeManager) -> None:
        """Mode transitions should be recorded in history."""
        initial_history_len = len(manager.state.mode_history)
        manager.cycle_mode()
        manager.cycle_mode()
        assert len(manager.state.mode_history) == initial_history_len + 2


# =============================================================================
# UNIT TESTS: ModeManager - Tool Permission
# =============================================================================


class TestModeManagerToolPermission:
    """Tests for tool permission checking."""

    def test_auto_approve_mode_approves_all(self, auto_manager: ModeManager) -> None:
        """AUTO mode should approve all tools."""
        assert auto_manager.should_approve_tool("write_file") is True
        assert auto_manager.should_approve_tool("delete_file") is True
        assert auto_manager.should_approve_tool("read_file") is True

    def test_plan_mode_approves_only_readonly(self, plan_manager: ModeManager) -> None:
        """PLAN mode should only approve read-only tools."""
        assert plan_manager.should_approve_tool("read_file") is True
        assert plan_manager.should_approve_tool("grep") is True
        assert plan_manager.should_approve_tool("write_file") is False

    def test_normal_mode_approves_nothing(self, manager: ModeManager) -> None:
        """NORMAL mode should not auto-approve anything."""
        assert manager.should_approve_tool("read_file") is False
        assert manager.should_approve_tool("write_file") is False


# =============================================================================
# UNIT TESTS: ModeManager - Write Operation Detection
# =============================================================================


class TestWriteOperationDetection:
    """Tests for detecting write operations."""

    def test_write_tools_detected(self, manager: ModeManager) -> None:
        """Known write tools should be detected."""
        for tool in WRITE_TOOLS:
            assert manager.is_write_operation(tool) is True

    def test_readonly_tools_not_detected_as_write(self, manager: ModeManager) -> None:
        """Known read-only tools should not be flagged as writes."""
        for tool in list(READONLY_TOOLS)[:10]:  # Sample
            assert manager.is_write_operation(tool) is False

    def test_bash_readonly_commands(self, manager: ModeManager) -> None:
        """Known read-only bash commands should be safe."""
        safe_commands = ["ls -la", "cat file.txt", "grep pattern file", "pwd"]
        for cmd in safe_commands:
            assert manager.is_write_operation("bash", {"command": cmd}) is False

    def test_bash_write_commands(self, manager: ModeManager) -> None:
        """Write bash commands should be detected."""
        write_commands = [
            "rm file.txt",
            "rm -rf /",
            "mv old new",
            "touch newfile",
            "echo test > file",
            "sed -i 's/old/new/' file",
        ]
        for cmd in write_commands:
            assert manager.is_write_operation("bash", {"command": cmd}) is True, (
                f"Expected write: {cmd}"
            )


class TestGitCommandDetection:
    """Tests for git command classification."""

    def test_git_readonly_subcommands(self, manager: ModeManager) -> None:
        """Git read-only subcommands should be safe."""
        safe_commands = [
            "git status",
            "git log --oneline",
            "git diff HEAD",
            "git branch -a",
            "git show HEAD",
            "git describe --tags",
        ]
        for cmd in safe_commands:
            assert manager.is_write_operation("bash", {"command": cmd}) is False, (
                f"Should be safe: {cmd}"
            )

    def test_git_write_subcommands(self, manager: ModeManager) -> None:
        """Git write subcommands should be blocked."""
        write_commands = [
            "git commit -m 'test'",
            "git push origin main",
            "git checkout feature",
            "git reset --hard HEAD",
            "git rebase main",
            "git merge feature",
            "git stash",
        ]
        for cmd in write_commands:
            assert manager.is_write_operation("bash", {"command": cmd}) is True, (
                f"Should be write: {cmd}"
            )


# =============================================================================
# UNIT TESTS: ModeManager - Tool Blocking
# =============================================================================


class TestToolBlocking:
    """Tests for tool blocking in read-only modes."""

    def test_write_blocked_in_plan_mode(self, plan_manager: ModeManager) -> None:
        """Write operations should be blocked in PLAN mode."""
        blocked, reason = plan_manager.should_block_tool(
            "write_file", {"path": "test.py"}
        )
        assert blocked is True
        assert reason is not None
        assert "blocked" in reason.lower()

    def test_read_allowed_in_plan_mode(self, plan_manager: ModeManager) -> None:
        """Read operations should be allowed in PLAN mode."""
        blocked, reason = plan_manager.should_block_tool(
            "read_file", {"path": "test.py"}
        )
        assert blocked is False
        assert reason is None

    def test_write_allowed_in_auto_mode(self, auto_manager: ModeManager) -> None:
        """Write operations should be allowed in AUTO mode."""
        blocked, reason = auto_manager.should_block_tool(
            "write_file", {"path": "test.py"}
        )
        assert blocked is False
        assert reason is None

    def test_bash_rm_blocked_in_architect(self, architect_manager: ModeManager) -> None:
        """Destructive bash commands should be blocked in ARCHITECT mode."""
        blocked, reason = architect_manager.should_block_tool(
            "bash", {"command": "rm -rf /"}
        )
        assert blocked is True
        assert reason is not None

    def test_bash_ls_allowed_in_architect(self, architect_manager: ModeManager) -> None:
        """Safe bash commands should be allowed in ARCHITECT mode."""
        blocked, reason = architect_manager.should_block_tool(
            "bash", {"command": "ls -la"}
        )
        assert blocked is False

    def test_blocked_reason_contains_options(self, plan_manager: ModeManager) -> None:
        """Block reason should contain helpful options."""
        blocked, reason = plan_manager.should_block_tool("write_file", {})
        assert blocked is True
        assert "approved" not in reason.lower()
        assert "shift+tab" in reason.lower()


# =============================================================================
# UNIT TESTS: ModeManager - Display Methods
# =============================================================================


class TestDisplayMethods:
    """Tests for display/indicator methods."""

    def test_mode_indicator_format(self, manager: ModeManager) -> None:
        """Mode indicator should have emoji and name."""
        indicator = manager.get_mode_indicator()
        assert "✋" in indicator
        assert "NORMAL" in indicator

    def test_mode_description(self, manager: ModeManager) -> None:
        """Mode description should be meaningful."""
        desc = manager.get_mode_description()
        assert len(desc) > 10
        assert isinstance(desc, str)

    def test_transition_message(self, manager: ModeManager) -> None:
        """Transition message should show both modes."""
        old, new = manager.cycle_mode()
        msg = manager.get_transition_message(old, new)
        assert old.value.upper() in msg
        assert new.value.upper() in msg
        assert "→" in msg


# =============================================================================
# UNIT TESTS: ModeManager - System Prompt Injection
# =============================================================================


class TestSystemPromptInjection:
    """Tests for system prompt modification."""

    def test_all_modes_have_prompt_modifier(self) -> None:
        """Each mode should have a unique prompt modifier."""
        for mode in VibeMode:
            m = ModeManager(initial_mode=mode)
            modifier = m.get_system_prompt_modifier()
            assert len(modifier) > 0
            assert "<active_mode>" in modifier
            assert "<rules>" in modifier

    def test_plan_mode_modifier_content(self, plan_manager: ModeManager) -> None:
        """PLAN mode modifier should mention planning."""
        modifier = plan_manager.get_system_prompt_modifier()
        assert "PLAN" in modifier
        assert "read-only" in modifier.lower()
        assert "plan" in modifier.lower()

    def test_yolo_mode_modifier_content(self, yolo_manager: ModeManager) -> None:
        """YOLO mode modifier should emphasize speed."""
        modifier = yolo_manager.get_system_prompt_modifier()
        assert "YOLO" in modifier
        assert "ULTRA-CONCISE" in modifier
        assert "✓" in modifier

    def test_architect_mode_modifier_content(
        self, architect_manager: ModeManager
    ) -> None:
        """ARCHITECT mode modifier should mention design."""
        modifier = architect_manager.get_system_prompt_modifier()
        assert "ARCHITECT" in modifier
        assert "design" in modifier.lower()
        assert "mermaid" in modifier.lower()


# =============================================================================
# UNIT TESTS: Helper Functions
# =============================================================================


class TestHelperFunctions:
    """Tests for standalone helper functions."""

    def test_mode_from_auto_approve_true(self) -> None:
        """auto_approve=True should map to AUTO mode."""
        mode = mode_from_auto_approve(True)
        assert mode == VibeMode.AUTO

    def test_mode_from_auto_approve_false(self) -> None:
        """auto_approve=False should map to NORMAL mode."""
        mode = mode_from_auto_approve(False)
        assert mode == VibeMode.NORMAL

    def test_inject_mode_into_system_prompt(self, plan_manager: ModeManager) -> None:
        """Mode injection should prepend to base prompt."""
        base = "You are a helpful assistant."
        result = inject_mode_into_system_prompt(base, plan_manager)
        assert result.startswith("<active_mode>")
        assert base in result
        assert len(result) > len(base)

    def test_get_mode_banner(self, yolo_manager: ModeManager) -> None:
        """Banner should contain mode info and instructions."""
        banner = get_mode_banner(yolo_manager)
        assert "🚀" in banner
        assert "YOLO" in banner
        assert "Shift+Tab" in banner
        assert "╔" in banner  # ASCII art


# =============================================================================
# UNIT TESTS: ModeAwareToolExecutor
# =============================================================================


class TestModeAwareToolExecutor:
    """Tests for the ModeAwareToolExecutor wrapper."""

    @pytest.mark.asyncio
    async def test_blocks_write_in_plan_mode(self, plan_manager: ModeManager) -> None:
        """Should block write tools in PLAN mode."""
        original_executor = AsyncMock(return_value={"success": True})
        executor = ModeAwareToolExecutor(plan_manager, original_executor)

        result = await executor.execute_tool("write_file", {"path": "test.py"})

        assert result["blocked"] is True
        assert result["error"] is True
        assert "write_file" in result["message"]
        original_executor.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_read_in_plan_mode(self, plan_manager: ModeManager) -> None:
        """Should allow read tools in PLAN mode."""
        original_executor = AsyncMock(return_value={"content": "file contents"})
        executor = ModeAwareToolExecutor(plan_manager, original_executor)

        result = await executor.execute_tool("read_file", {"path": "test.py"})

        assert "blocked" not in result
        assert result["content"] == "file contents"
        original_executor.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_in_yolo_mode(self, yolo_manager: ModeManager) -> None:
        """Should truncate large results in YOLO mode."""
        long_content = "x" * 1000
        original_executor = AsyncMock(return_value={"content": long_content})
        executor = ModeAwareToolExecutor(yolo_manager, original_executor)

        result = await executor.execute_tool("read_file", {"path": "test.py"})

        assert len(result["content"]) < len(long_content)
        assert "[truncated]" in result["content"]


# =============================================================================
# UNIT TESTS: MODE_CONFIGS
# =============================================================================


class TestModeConfigs:
    """Tests for mode configuration constants."""

    def test_all_modes_have_config(self) -> None:
        """Each mode should have a config entry."""
        for mode in VibeMode:
            assert mode in MODE_CONFIGS
            config = MODE_CONFIGS[mode]
            assert isinstance(config, ModeConfig)

    def test_configs_have_required_fields(self) -> None:
        """Each config should have all required fields."""
        for mode, config in MODE_CONFIGS.items():
            assert isinstance(config.auto_approve, bool)
            assert isinstance(config.read_only, bool)
            assert isinstance(config.emoji, str)
            assert len(config.emoji) > 0
            assert isinstance(config.description, str)
            assert len(config.description) > 0

    def test_readonly_modes_not_auto_approve(self) -> None:
        """Read-only modes should not auto-approve."""
        for mode, config in MODE_CONFIGS.items():
            if config.read_only:
                assert config.auto_approve is False, (
                    f"{mode} is read-only but auto-approves"
                )


# =============================================================================
# INTEGRATION TESTS: With System Prompt
# =============================================================================


class TestIntegrationWithSystemPrompt:
    """Integration tests with system_prompt module."""

    def test_system_prompt_includes_mode_injection(self) -> None:
        """System prompt should include mode modifier when manager provided."""
        from chefchat.core.system_prompt import get_universal_system_prompt

        class MockModel:
            max_tokens = None  # No validation needed for test

        class MockConfig:
            system_prompt = "Base prompt."
            include_model_info = False
            include_prompt_detail = False
            include_project_context = False

            def get_active_model(self):
                return MockModel()

        manager = ModeManager(initial_mode=VibeMode.ARCHITECT)
        prompt = get_universal_system_prompt(None, MockConfig(), manager)

        assert "<active_mode>" in prompt
        assert "ARCHITECT" in prompt
        assert "Base prompt." in prompt

    def test_system_prompt_works_without_manager(self) -> None:
        """System prompt should work when no mode_manager provided."""
        from chefchat.core.system_prompt import get_universal_system_prompt

        class MockModel:
            max_tokens = None

        class MockConfig:
            system_prompt = "Base prompt only."
            include_model_info = False
            include_prompt_detail = False
            include_project_context = False

            def get_active_model(self):
                return MockModel()

        prompt = get_universal_system_prompt(None, MockConfig(), None)

        assert "<active_mode>" not in prompt
        assert prompt == "Base prompt only."


# =============================================================================
# SCENARIO TESTS
# =============================================================================


class TestScenarios:
    """High-level scenario tests."""

    def test_scenario_plan_mode_blocks_write(self) -> None:
        """User in PLAN mode tries to write_file → should block."""
        manager = ModeManager(initial_mode=VibeMode.PLAN)

        blocked, reason = manager.should_block_tool("write_file", {"path": "new.py"})

        assert blocked is True
        assert "approved" not in reason.lower()
        assert "shift+tab" in reason.lower()

    def test_scenario_cycle_through_all_modes(self) -> None:
        """User presses Shift+Tab 5 times → should visit all modes."""
        manager = ModeManager(initial_mode=VibeMode.NORMAL)
        visited = [VibeMode.NORMAL]

        for _ in range(5):
            _, new = manager.cycle_mode()
            visited.append(new)

        # Should have visited all modes plus returned to start
        assert len(set(visited)) == 5
        assert visited[-1] == VibeMode.NORMAL

    def test_scenario_yolo_mode_ultra_concise(self) -> None:
        """User in YOLO mode → system prompt should emphasize conciseness."""
        manager = ModeManager(initial_mode=VibeMode.YOLO)

        modifier = manager.get_system_prompt_modifier()

        assert "ULTRA-CONCISE" in modifier
        assert "concise" in modifier.lower()

    def test_scenario_bash_detection(self) -> None:
        """Bash command detection: ls = ok, rm = blocked in PLAN."""
        manager = ModeManager(initial_mode=VibeMode.PLAN)

        # ls should be allowed
        blocked_ls, _ = manager.should_block_tool("bash", {"command": "ls -la"})
        assert blocked_ls is False

        # rm should be blocked
        blocked_rm, _ = manager.should_block_tool("bash", {"command": "rm file.txt"})
        assert blocked_rm is True

    def test_scenario_mode_transition_updates_state(self) -> None:
        """Mode transitions should update all related state."""
        manager = ModeManager(initial_mode=VibeMode.NORMAL)

        # Start in NORMAL
        assert manager.auto_approve is False
        assert manager.read_only_tools is False

        # Move to AUTO
        manager.set_mode(VibeMode.AUTO)
        assert manager.auto_approve is True
        assert manager.read_only_tools is False

        # Move to PLAN
        manager.set_mode(VibeMode.PLAN)
        assert manager.auto_approve is False
        assert manager.read_only_tools is True


# =============================================================================
# PARAMETRIZED TESTS
# =============================================================================


@pytest.mark.parametrize(
    "mode,expected_auto,expected_readonly",
    [
        (VibeMode.PLAN, False, True),
        (VibeMode.NORMAL, False, False),
        (VibeMode.AUTO, True, False),
        (VibeMode.YOLO, True, False),
        (VibeMode.ARCHITECT, False, True),
    ],
)
def test_mode_permissions(
    mode: VibeMode, expected_auto: bool, expected_readonly: bool
) -> None:
    """Test that each mode has correct permission settings."""
    manager = ModeManager(initial_mode=mode)
    assert manager.auto_approve == expected_auto
    assert manager.read_only_tools == expected_readonly


@pytest.mark.parametrize(
    "command,expected_write",
    [
        ("ls -la", False),
        ("cat file.txt", False),
        ("grep pattern file", False),
        ("rm file.txt", True),
        ("rm -rf /", True),
        ("mv old new", True),
        ("cp src dst", True),
        ("touch newfile", True),
        ("mkdir newdir", True),
        ("echo hi > file", True),
        ("echo hi >> file", True),
        ("sed -i 's/a/b/' file", True),
        ("git status", False),
        ("git log", False),
        ("git diff", False),
        ("git commit -m 'msg'", True),
        ("git push", True),
        ("git checkout branch", True),
    ],
)
def test_bash_command_classification(command: str, expected_write: bool) -> None:
    """Test bash command write detection."""
    manager = ModeManager()
    is_write = manager.is_write_operation("bash", {"command": command})
    assert is_write == expected_write, f"Command '{command}' classification mismatch"


@pytest.mark.parametrize(
    "tool_name,is_readonly",
    [
        ("read_file", True),
        ("grep", True),
        ("list_files", True),
        ("write_file", False),
        ("delete_file", False),
        ("edit_file", False),
    ],
)
def test_tool_classification(tool_name: str, is_readonly: bool) -> None:
    """Test tool classification."""
    if is_readonly:
        assert tool_name in READONLY_TOOLS
    else:
        assert tool_name in WRITE_TOOLS
