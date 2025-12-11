"""ChefChat Michelin-Style Test Suite: Modes & Safety
=====================================================

Level 1 Unit Tests - Les Fondations

Tests for:
- Mode Cycle Order (Shift+Tab behavior)
- Gatekeeper Logic (write blocking in read-only modes)
- System Prompt Injection (mode-specific content)

Usage:
    pytest tests/chef_unit/test_modes_and_safety.py -v
"""

from __future__ import annotations

import pytest

from vibe.cli.mode_errors import (
    ModeErrorType,
    create_write_blocked_error,
    reset_to_safe_mode,
    validate_mode_state,
)
from vibe.cli.mode_manager import MODE_CYCLE_ORDER, ModeManager, VibeMode

# =============================================================================
# FIXTURES - Mise en Place
# =============================================================================


@pytest.fixture
def plan_manager() -> ModeManager:
    """ModeManager in PLAN mode (read-only)."""
    return ModeManager(initial_mode=VibeMode.PLAN)


@pytest.fixture
def normal_manager() -> ModeManager:
    """ModeManager in NORMAL mode."""
    return ModeManager(initial_mode=VibeMode.NORMAL)


@pytest.fixture
def yolo_manager() -> ModeManager:
    """ModeManager in YOLO mode (full auto-approve)."""
    return ModeManager(initial_mode=VibeMode.YOLO)


@pytest.fixture
def architect_manager() -> ModeManager:
    """ModeManager in ARCHITECT mode (design-only, read-only)."""
    return ModeManager(initial_mode=VibeMode.ARCHITECT)


# =============================================================================
# TEST CLASS 1: Mode Cycle Order
# =============================================================================


class TestModeCycleOrder:
    """Verify that mode cycling follows the correct order.

    Expected order: NORMAL → AUTO → PLAN → YOLO → ARCHITECT → NORMAL ...
    """

    def test_cycle_order_constant_is_correct(self) -> None:
        """MODE_CYCLE_ORDER should contain exactly 5 modes in correct order."""
        expected_order = (
            VibeMode.NORMAL,
            VibeMode.AUTO,
            VibeMode.PLAN,
            VibeMode.YOLO,
            VibeMode.ARCHITECT,
        )
        assert MODE_CYCLE_ORDER == expected_order

    def test_cycle_from_normal_goes_to_auto(self, normal_manager: ModeManager) -> None:
        """Pressing Shift+Tab in NORMAL mode should go to AUTO."""
        old, new = normal_manager.cycle_mode()
        assert old == VibeMode.NORMAL
        assert new == VibeMode.AUTO

    def test_cycle_from_auto_goes_to_plan(self) -> None:
        """Pressing Shift+Tab in AUTO mode should go to PLAN."""
        manager = ModeManager(initial_mode=VibeMode.AUTO)
        old, new = manager.cycle_mode()
        assert old == VibeMode.AUTO
        assert new == VibeMode.PLAN

    def test_cycle_from_plan_goes_to_yolo(self, plan_manager: ModeManager) -> None:
        """Pressing Shift+Tab in PLAN mode should go to YOLO."""
        old, new = plan_manager.cycle_mode()
        assert old == VibeMode.PLAN
        assert new == VibeMode.YOLO

    def test_cycle_from_yolo_goes_to_architect(self, yolo_manager: ModeManager) -> None:
        """Pressing Shift+Tab in YOLO mode should go to ARCHITECT."""
        old, new = yolo_manager.cycle_mode()
        assert old == VibeMode.YOLO
        assert new == VibeMode.ARCHITECT

    def test_cycle_from_architect_goes_to_normal(
        self, architect_manager: ModeManager
    ) -> None:
        """Pressing Shift+Tab in ARCHITECT mode should wrap around to NORMAL."""
        old, new = architect_manager.cycle_mode()
        assert old == VibeMode.ARCHITECT
        assert new == VibeMode.NORMAL

    def test_full_cycle_returns_to_start(self, normal_manager: ModeManager) -> None:
        """Cycling 5 times should return to the starting mode."""
        for _ in range(5):
            normal_manager.cycle_mode()
        assert normal_manager.current_mode == VibeMode.NORMAL

    def test_cycle_visits_all_modes(self, normal_manager: ModeManager) -> None:
        """A full cycle should visit all 5 modes exactly once."""
        visited = [VibeMode.NORMAL]
        for _ in range(5):
            _, new = normal_manager.cycle_mode()
            visited.append(new)

        # First 5 should be unique (all modes)
        assert len(set(visited[:5])) == 5
        # The 6th should be back to NORMAL
        assert visited[5] == VibeMode.NORMAL


# =============================================================================
# TEST CLASS 2: Gatekeeper Logic (Safety!)
# =============================================================================


class TestGatekeeperLogic:
    """Test the safety-critical tool blocking functionality.

    CRITICAL: These tests verify that write operations are blocked
    in read-only modes (PLAN, ARCHITECT).
    """

    def test_plan_mode_blocks_write_file(self, plan_manager: ModeManager) -> None:
        """PLAN mode should NOT approve write_file tool."""
        result = plan_manager.should_approve_tool("write_file")
        assert result is False, "SECURITY: write_file should be blocked in PLAN mode!"

    def test_plan_mode_blocks_delete_file(self, plan_manager: ModeManager) -> None:
        """PLAN mode should NOT approve delete_file tool."""
        result = plan_manager.should_approve_tool("delete_file")
        assert result is False, "SECURITY: delete_file should be blocked in PLAN mode!"

    def test_plan_mode_blocks_edit_file(self, plan_manager: ModeManager) -> None:
        """PLAN mode should NOT approve edit_file tool."""
        result = plan_manager.should_approve_tool("edit_file")
        assert result is False, "SECURITY: edit_file should be blocked in PLAN mode!"

    def test_plan_mode_allows_read_file(self, plan_manager: ModeManager) -> None:
        """PLAN mode should approve read_file tool."""
        result = plan_manager.should_approve_tool("read_file")
        assert result is True, "read_file should be allowed in PLAN mode"

    def test_plan_mode_allows_grep(self, plan_manager: ModeManager) -> None:
        """PLAN mode should approve grep tool."""
        result = plan_manager.should_approve_tool("grep")
        assert result is True, "grep should be allowed in PLAN mode"

    def test_yolo_mode_approves_write_file(self, yolo_manager: ModeManager) -> None:
        """YOLO mode should approve write_file tool."""
        result = yolo_manager.should_approve_tool("write_file")
        assert result is True, "write_file should be auto-approved in YOLO mode"

    def test_yolo_mode_approves_delete_file(self, yolo_manager: ModeManager) -> None:
        """YOLO mode should approve delete_file tool."""
        result = yolo_manager.should_approve_tool("delete_file")
        assert result is True, "delete_file should be auto-approved in YOLO mode"

    def test_yolo_mode_approves_everything(self, yolo_manager: ModeManager) -> None:
        """YOLO mode should auto-approve any tool."""
        dangerous_tools = ["write_file", "delete_file", "rm_rf_everything", "nuke_it"]
        for tool in dangerous_tools:
            assert yolo_manager.should_approve_tool(tool) is True

    def test_architect_mode_blocks_writes(self, architect_manager: ModeManager) -> None:
        """ARCHITECT mode (design-only) should block write operations."""
        result = architect_manager.should_approve_tool("write_file")
        assert result is False, "ARCHITECT mode should be read-only!"

    def test_normal_mode_does_not_auto_approve(
        self, normal_manager: ModeManager
    ) -> None:
        """NORMAL mode should not auto-approve anything (needs confirmation)."""
        assert normal_manager.should_approve_tool("read_file") is False
        assert normal_manager.should_approve_tool("write_file") is False

    def test_should_block_tool_returns_reason(self, plan_manager: ModeManager) -> None:
        """should_block_tool should return a helpful reason message."""
        blocked, reason = plan_manager.should_block_tool(
            "write_file", {"path": "test.py"}
        )

        assert blocked is True
        assert reason is not None
        assert "write_file" in reason
        assert "PLAN" in reason
        # Should contain helpful options (but NOT "approved" - that bypass was removed for security)
        assert "switch modes" in reason.lower() or "shift+tab" in reason.lower()


# =============================================================================
# TEST CLASS 3: Bash Command Detection (Safety!)
# =============================================================================


class TestBashCommandDetection:
    """Test that bash commands are correctly classified as read/write."""

    @pytest.mark.parametrize(
        "command", ["rm file.txt", "rm -rf /", "rm -f important.py"]
    )
    def test_rm_commands_detected_as_write(
        self, plan_manager: ModeManager, command: str
    ) -> None:
        """rm commands should be detected as write operations."""
        is_write = plan_manager.is_write_operation("bash", {"command": command})
        assert is_write is True, f"'{command}' should be detected as write operation"

    @pytest.mark.parametrize(
        "command",
        [
            "mv old.py new.py",
            "cp source.py dest.py",
            "touch newfile.py",
            "mkdir newfolder",
            "echo 'text' > file.txt",
            "echo 'text' >> file.txt",
        ],
    )
    def test_file_modification_commands_detected(
        self, plan_manager: ModeManager, command: str
    ) -> None:
        """File modification commands should be detected as write operations."""
        is_write = plan_manager.is_write_operation("bash", {"command": command})
        assert is_write is True, f"'{command}' should be detected as write"

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "cat file.py",
            "grep pattern file",
            "head -n 10 file",
            "tail -f log.txt",
            "pwd",
            "echo hello",  # echo without redirect is safe
            "tree",
            "find . -name '*.py'",
        ],
    )
    def test_readonly_commands_allowed(
        self, plan_manager: ModeManager, command: str
    ) -> None:
        """Read-only bash commands should not be flagged as writes."""
        is_write = plan_manager.is_write_operation("bash", {"command": command})
        assert is_write is False, f"'{command}' should NOT be detected as write"

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'test'",
            "git push origin main",
            "git checkout feature",
            "git reset --hard HEAD",
            "git merge branch",
        ],
    )
    def test_git_write_commands_detected(
        self, plan_manager: ModeManager, command: str
    ) -> None:
        """Git write commands should be detected."""
        is_write = plan_manager.is_write_operation("bash", {"command": command})
        assert is_write is True, f"'{command}' should be detected as write"

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline",
            "git diff",
            "git branch -a",
            "git show HEAD",
        ],
    )
    def test_git_readonly_commands_allowed(
        self, plan_manager: ModeManager, command: str
    ) -> None:
        """Git read-only commands should be allowed."""
        is_write = plan_manager.is_write_operation("bash", {"command": command})
        assert is_write is False, f"'{command}' should NOT be detected as write"


# =============================================================================
# TEST CLASS 4: System Prompt Injection
# =============================================================================


class TestSystemPromptInjection:
    """Test that mode-specific content is correctly injected into prompts."""

    def test_yolo_prompt_contains_marker(self, yolo_manager: ModeManager) -> None:
        """YOLO mode system prompt should contain 'YOLO MODE' marker."""
        prompt = yolo_manager.get_system_prompt_modifier()
        assert "YOLO MODE" in prompt, "YOLO prompt should contain 'YOLO MODE'"

    def test_yolo_prompt_contains_ultra_concise(
        self, yolo_manager: ModeManager
    ) -> None:
        """YOLO mode prompt should emphasize conciseness."""
        prompt = yolo_manager.get_system_prompt_modifier()
        assert "ULTRA-CONCISE" in prompt, "YOLO prompt should mention ULTRA-CONCISE"

    def test_plan_prompt_contains_readonly(self, plan_manager: ModeManager) -> None:
        """PLAN mode prompt should mention read-only restrictions."""
        prompt = plan_manager.get_system_prompt_modifier()
        assert "PLAN MODE" in prompt
        assert "read-only" in prompt.lower()

    def test_plan_prompt_mentions_implementation_plan(
        self, plan_manager: ModeManager
    ) -> None:
        """PLAN mode prompt should mention creating implementation plans."""
        prompt = plan_manager.get_system_prompt_modifier()
        assert "implementation plan" in prompt.lower()

    def test_architect_prompt_mentions_design(
        self, architect_manager: ModeManager
    ) -> None:
        """ARCHITECT mode prompt should focus on design."""
        prompt = architect_manager.get_system_prompt_modifier()
        assert "ARCHITECT MODE" in prompt
        assert "HIGH-LEVEL DESIGN" in prompt

    def test_all_modes_have_active_mode_tag(self) -> None:
        """All modes should include <active_mode> XML tag in their prompts."""
        for mode in VibeMode:
            manager = ModeManager(initial_mode=mode)
            prompt = manager.get_system_prompt_modifier()
            assert "<active_mode>" in prompt, f"{mode} should have <active_mode> tag"

    def test_all_modes_have_mode_rules_tag(self) -> None:
        """All modes should include <mode_rules> XML tag in their prompts."""
        for mode in VibeMode:
            manager = ModeManager(initial_mode=mode)
            prompt = manager.get_system_prompt_modifier()
            assert "<mode_rules>" in prompt, f"{mode} should have <mode_rules> tag"


# =============================================================================
# TEST CLASS 5: Mode State Consistency
# =============================================================================


class TestModeStateConsistency:
    """Test that mode state remains consistent after transitions."""

    def test_set_mode_updates_auto_approve(self, normal_manager: ModeManager) -> None:
        """Setting mode should update auto_approve flag correctly."""
        assert normal_manager.auto_approve is False

        normal_manager.set_mode(VibeMode.YOLO)
        assert normal_manager.auto_approve is True

        normal_manager.set_mode(VibeMode.PLAN)
        assert normal_manager.auto_approve is False

    def test_set_mode_updates_read_only(self, normal_manager: ModeManager) -> None:
        """Setting mode should update read_only_tools flag correctly."""
        assert normal_manager.read_only_tools is False

        normal_manager.set_mode(VibeMode.PLAN)
        assert normal_manager.read_only_tools is True

        normal_manager.set_mode(VibeMode.YOLO)
        assert normal_manager.read_only_tools is False

    def test_mode_history_is_tracked(self, normal_manager: ModeManager) -> None:
        """Mode transitions should be recorded in history."""
        initial_len = len(normal_manager.state.mode_history)

        normal_manager.cycle_mode()
        normal_manager.cycle_mode()

        assert len(normal_manager.state.mode_history) == initial_len + 2

    def test_validate_mode_state_returns_valid(
        self, normal_manager: ModeManager
    ) -> None:
        """validate_mode_state should return True for valid state."""
        is_valid, error = validate_mode_state(normal_manager)
        assert is_valid is True
        assert error is None


# =============================================================================
# TEST CLASS 6: Error Creation
# =============================================================================


class TestModeErrorCreation:
    """Test mode error message creation."""

    def test_write_blocked_error_contains_tool_name(self) -> None:
        """Error message should contain the tool name."""
        error = create_write_blocked_error("write_file", VibeMode.PLAN)

        assert "write_file" in error.user_message
        assert error.error_type == ModeErrorType.WRITE_IN_READONLY

    def test_write_blocked_error_contains_recovery_hints(self) -> None:
        """Error message should contain recovery options.

        Note: The 'approved' bypass was removed for security.
        Recovery hints now only suggest switching modes.
        """
        error = create_write_blocked_error("delete_file", VibeMode.ARCHITECT)

        # Should NOT contain "approved" - this bypass undermined read-only security
        assert "approved" not in error.recovery_hint.lower()
        # Should suggest switching modes instead
        assert "shift+tab" in error.recovery_hint.lower()
        assert "switch" in error.recovery_hint.lower()

    def test_error_to_display_message_is_markdown(self) -> None:
        """Display message should be valid markdown."""
        error = create_write_blocked_error("write_file", VibeMode.PLAN)
        display = error.to_display_message()

        assert "##" in display  # Headers
        assert "###" in display  # Subheaders


# =============================================================================
# TEST CLASS 7: Reset and Recovery
# =============================================================================


class TestModeRecovery:
    """Test mode recovery mechanisms."""

    def test_reset_to_safe_mode_returns_normal(self, yolo_manager: ModeManager) -> None:
        """reset_to_safe_mode should return NORMAL mode."""
        new_mode = reset_to_safe_mode(yolo_manager)

        assert new_mode == VibeMode.NORMAL
        assert yolo_manager.current_mode == VibeMode.NORMAL

    def test_reset_from_any_mode_goes_to_normal(self) -> None:
        """Reset should work from any mode."""
        for mode in VibeMode:
            manager = ModeManager(initial_mode=mode)
            reset_to_safe_mode(manager)
            assert manager.current_mode == VibeMode.NORMAL
