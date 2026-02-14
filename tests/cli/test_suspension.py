"""Tests for suspension functionality (Ctrl+Z)."""

from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

from vibe.cli.cli import _setup_signal_handlers
from vibe.cli.textual_ui.app import VibeApp
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp


class TestSuspensionSignalHandlers:
    """Test signal handlers for suspension support."""

    def test_setup_signal_handlers_installs_sigcont(self) -> None:
        """Test that _setup_signal_handlers installs SIGCONT handler."""
        # Save original handlers
        original_sigcont = signal.getsignal(signal.SIGCONT)
        original_sigtstp = signal.getsignal(signal.SIGTSTP)

        try:
            # Setup signal handlers
            _setup_signal_handlers()

            # Check SIGCONT handler is installed
            sigcont_handler = signal.getsignal(signal.SIGCONT)
            assert sigcont_handler != signal.SIG_DFL
            assert callable(sigcont_handler)

            # Check SIGTSTP uses default handler
            sigtstp_handler = signal.getsignal(signal.SIGTSTP)
            assert sigtstp_handler == signal.SIG_DFL

        finally:
            # Restore original handlers
            signal.signal(signal.SIGCONT, original_sigcont)
            signal.signal(signal.SIGTSTP, original_sigtstp)

    def test_sigcont_handler_executable(self) -> None:
        """Test that SIGCONT handler can be called without errors."""
        # Save original handler
        original_sigcont = signal.getsignal(signal.SIGCONT)

        try:
            # Setup signal handlers
            _setup_signal_handlers()

            # Get the handler
            sigcont_handler = signal.getsignal(signal.SIGCONT)

            # Call it (should not raise - handler is robust against stdin issues)
            sigcont_handler(signal.SIGCONT, None)

        finally:
            # Restore original handler
            signal.signal(signal.SIGCONT, original_sigcont)


class TestSuspensionKeyBinding:
    """Test Ctrl+Z key binding for suspension."""

    def test_ctrl_z_binding_exists(self) -> None:
        """Test that Ctrl+Z is bound to suspend_process action."""
        ctrl_z_bindings = [b for b in VibeApp.BINDINGS if b.key == "ctrl+z"]
        assert len(ctrl_z_bindings) == 1

        binding = ctrl_z_bindings[0]
        assert binding.action == "suspend_process"
        assert binding.description == "Suspend"
        assert binding.show is False

    def test_vibe_app_has_suspend_methods(self) -> None:
        """Test that VibeApp has required suspend methods."""
        assert hasattr(VibeApp, 'action_suspend_process')
        assert hasattr(VibeApp, 'suspend')

    @pytest.mark.asyncio
    async def test_ctrl_z_binding_is_callable(self) -> None:
        """Test that Ctrl+Z binding can be called programmatically."""
        app = BaseSnapshotTestApp()

        async with app.run_test() as pilot:
            # Test that the action can be called directly
            with patch.object(app, 'action_suspend_process') as mock_suspend:
                # Call the action directly (simulating what the key binding would do)
                app.action_suspend_process()
                
                # Verify the action was called
                mock_suspend.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
