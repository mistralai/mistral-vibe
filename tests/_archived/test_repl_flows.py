"""ChefChat Integration Tests — REPL Flows
==========================================

Integration tests for the ChefChat REPL, verifying:
- Mode cycling via Shift+Tab simulation
- Easter egg commands (/chef, /wisdom, /roast, /stats)
- Error handling behavior
- Session state management

Usage:
    pytest tests/integration/test_repl_flows.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vibe.cli.mode_manager import ModeManager, VibeMode

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def mock_config() -> MagicMock:
    """Create a mock VibeConfig for testing."""
    config = MagicMock()
    config.effective_workdir = "/tmp/test"

    # Mock get_active_model to return a proper object
    model = MagicMock()
    model.alias = "test-model"
    config.get_active_model.return_value = model

    return config


@pytest.fixture
def mode_manager() -> ModeManager:
    """Create a fresh ModeManager for testing."""
    return ModeManager(initial_mode=VibeMode.NORMAL)


# =============================================================================
# MODE CYCLING TESTS
# =============================================================================


class TestModeCycling:
    """Test mode cycling behavior."""

    def test_cycle_mode_updates_state(self, mode_manager: ModeManager) -> None:
        """Cycling mode should update the mode manager state."""
        initial = mode_manager.current_mode
        old, new = mode_manager.cycle_mode()

        assert old == initial
        assert new != initial
        assert mode_manager.current_mode == new

    def test_full_cycle_returns_to_start(self, mode_manager: ModeManager) -> None:
        """Cycling through all modes should return to start."""
        start = mode_manager.current_mode

        # Cycle through all 5 modes
        for _ in range(5):
            mode_manager.cycle_mode()

        assert mode_manager.current_mode == start

    def test_cycle_order_is_consistent(self, mode_manager: ModeManager) -> None:
        """Mode cycling should follow a consistent order."""
        visited = []

        for _ in range(5):
            _, new = mode_manager.cycle_mode()
            visited.append(new)

        # Should visit AUTO, PLAN, YOLO, ARCHITECT, then back to NORMAL
        expected = [
            VibeMode.AUTO,
            VibeMode.PLAN,
            VibeMode.YOLO,
            VibeMode.ARCHITECT,
            VibeMode.NORMAL,
        ]
        assert visited == expected


# =============================================================================
# EASTER EGG COMMAND TESTS
# =============================================================================


class TestEasterEggCommands:
    """Test easter egg commands."""

    def test_get_kitchen_status_returns_string(self, mode_manager: ModeManager) -> None:
        """Kitchen status should return a formatted string."""
        from vibe.cli.easter_eggs import get_kitchen_status

        status = get_kitchen_status(mode_manager)

        assert isinstance(status, str)
        assert len(status) > 0

    def test_get_random_wisdom_returns_string(self) -> None:
        """Random wisdom should return a string."""
        from vibe.cli.easter_eggs import get_random_wisdom

        wisdom = get_random_wisdom()

        assert isinstance(wisdom, str)
        assert len(wisdom) > 0

    def test_get_random_roast_returns_string(self) -> None:
        """Random roast should return a string."""
        from vibe.cli.easter_eggs import get_random_roast

        roast = get_random_roast()

        assert isinstance(roast, str)
        assert len(roast) > 0

    def test_get_modes_display_returns_string(self, mode_manager: ModeManager) -> None:
        """Modes display should return a formatted string."""
        from vibe.cli.easter_eggs import get_modes_display

        display = get_modes_display(mode_manager)

        assert isinstance(display, str)
        assert "NORMAL" in display or "normal" in display.lower()


# =============================================================================
# ERROR HANDLER TESTS
# =============================================================================


class TestErrorHandler:
    """Test the centralized error handler."""

    def test_display_error_does_not_raise(self) -> None:
        """display_error should not raise on valid input."""
        from vibe.core.error_handler import ChefErrorHandler

        try:
            ChefErrorHandler.display_error(
                ValueError("Test error"), context="Test", show_traceback=False
            )
        except Exception as e:
            pytest.fail(f"display_error raised: {e}")

    def test_display_warning_does_not_raise(self) -> None:
        """display_warning should not raise on valid input."""
        from vibe.core.error_handler import ChefErrorHandler

        try:
            ChefErrorHandler.display_warning("Test warning", context="Test")
        except Exception as e:
            pytest.fail(f"display_warning raised: {e}")

    def test_format_error_message_returns_string(self) -> None:
        """format_error_message should return a formatted string."""
        from vibe.core.error_handler import ChefErrorHandler

        result = ChefErrorHandler.format_error_message(
            ValueError("Test"), context="Testing"
        )

        assert isinstance(result, str)
        assert "ValueError" in result
        assert "Testing" in result


# =============================================================================
# SESSION STATE TESTS
# =============================================================================


class TestSessionState:
    """Test session state management."""

    def test_mode_manager_tracks_history(self, mode_manager: ModeManager) -> None:
        """Mode manager should track mode history."""
        initial_history_len = len(mode_manager.state.mode_history)

        mode_manager.cycle_mode()
        mode_manager.cycle_mode()

        assert len(mode_manager.state.mode_history) == initial_history_len + 2

    def test_auto_approve_updates_on_yolo_mode(self, mode_manager: ModeManager) -> None:
        """auto_approve should be True in YOLO mode."""
        mode_manager.set_mode(VibeMode.YOLO)

        assert mode_manager.auto_approve is True

    def test_read_only_in_plan_mode(self, mode_manager: ModeManager) -> None:
        """read_only_tools should be True in PLAN mode."""
        mode_manager.set_mode(VibeMode.PLAN)

        assert mode_manager.read_only_tools is True


# =============================================================================
# CONFIG SINGLETON TESTS
# =============================================================================


class TestConfigSingleton:
    """Test the config singleton pattern."""

    def test_get_config_returns_vibeconfig(self) -> None:
        """get_config should return a VibeConfig instance."""
        from vibe.core.config import VibeConfig, clear_config_cache, get_config

        clear_config_cache()

        with patch.object(VibeConfig, "load", return_value=MagicMock(spec=VibeConfig)):
            config = get_config()
            assert config is not None

    def test_get_config_caches_result(self) -> None:
        """get_config should return the same instance on subsequent calls."""
        from vibe.core.config import VibeConfig, clear_config_cache, get_config

        clear_config_cache()

        with patch.object(
            VibeConfig, "load", return_value=MagicMock(spec=VibeConfig)
        ) as mock_load:
            config1 = get_config()
            config2 = get_config()

            assert config1 is config2
            assert mock_load.call_count == 1  # Only called once


# =============================================================================
# ASYNC HELPERS TESTS
# =============================================================================


class TestAsyncHelpers:
    """Test async utility functions."""

    @pytest.mark.asyncio
    async def test_run_with_spinner_executes_coroutine(self) -> None:
        """run_with_spinner should execute the provided coroutine."""
        from vibe.utils.async_helpers import run_with_spinner

        async def sample_coro() -> str:
            return "success"

        result = await run_with_spinner(sample_coro(), "Testing...")

        assert result == "success"

    @pytest.mark.asyncio
    async def test_batch_execute_runs_all_tasks(self) -> None:
        """batch_execute should run all provided tasks."""
        from vibe.utils.async_helpers import batch_execute

        results_tracker = []

        async def task(value: int) -> int:
            results_tracker.append(value)
            return value * 2

        tasks = [lambda v=i: task(v) for i in range(5)]
        results = await batch_execute(tasks, max_concurrent=2)

        assert len(results) == 5
        assert len(results_tracker) == 5
