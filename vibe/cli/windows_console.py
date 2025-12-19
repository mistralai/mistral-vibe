"""Windows console mode management utilities.

This module provides functions to manage Windows console mode flags,
which can become corrupted during long-running TUI sessions, causing
issues like:
- Copy/paste stops working
- Scrollbar becomes unresponsive
- Mouse selection breaks

The fix involves periodically restoring the correct console mode flags.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from vibe.core.utils import logger

if TYPE_CHECKING:
    pass


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


# Windows console mode flags
# Input mode flags
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_INSERT_MODE = 0x0020
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Output mode flags
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
DISABLE_NEWLINE_AUTO_RETURN = 0x0008
ENABLE_LVB_GRID_WORLDWIDE = 0x0010


class WindowsConsoleManager:
    """Manages Windows console mode flags to prevent corruption."""

    def __init__(self) -> None:
        """Initialize the console manager."""
        logger.debug("WindowsConsoleManager: Initializing")
        self._kernel32 = None
        self._stdin_handle = None
        self._stdout_handle = None
        self._original_stdin_mode: int | None = None
        self._original_stdout_mode: int | None = None
        self._initialized = False

        if not is_windows():
            logger.debug("WindowsConsoleManager: Not on Windows, skipping init")
            return

        try:
            self._initialize_handles()
            self._save_original_modes()
            self._initialized = True
            logger.debug(
                "WindowsConsoleManager: Initialized successfully, "
                "stdin_mode=0x%x, stdout_mode=0x%x",
                self._original_stdin_mode or 0,
                self._original_stdout_mode or 0,
            )
        except Exception as e:
            logger.warning("WindowsConsoleManager: Failed to initialize: %s", e)

    def _initialize_handles(self) -> None:
        """Initialize Windows console handles."""
        import ctypes

        self._kernel32 = ctypes.windll.kernel32

        STD_INPUT_HANDLE = -10
        STD_OUTPUT_HANDLE = -11

        self._stdin_handle = self._kernel32.GetStdHandle(STD_INPUT_HANDLE)
        self._stdout_handle = self._kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

    def _save_original_modes(self) -> None:
        """Save the original console modes."""
        if self._kernel32 is None:
            return

        import ctypes

        mode = ctypes.c_ulong()

        if self._stdin_handle:
            if self._kernel32.GetConsoleMode(self._stdin_handle, ctypes.byref(mode)):
                self._original_stdin_mode = mode.value

        if self._stdout_handle:
            if self._kernel32.GetConsoleMode(self._stdout_handle, ctypes.byref(mode)):
                self._original_stdout_mode = mode.value

    def get_current_modes(self) -> tuple[int | None, int | None]:
        """Get the current console modes.

        Returns:
            Tuple of (stdin_mode, stdout_mode), or (None, None) if not available.
        """
        if not self._initialized or self._kernel32 is None:
            return None, None

        import ctypes

        stdin_mode = None
        stdout_mode = None
        mode = ctypes.c_ulong()

        if self._stdin_handle:
            if self._kernel32.GetConsoleMode(self._stdin_handle, ctypes.byref(mode)):
                stdin_mode = mode.value

        if self._stdout_handle:
            if self._kernel32.GetConsoleMode(self._stdout_handle, ctypes.byref(mode)):
                stdout_mode = mode.value

        return stdin_mode, stdout_mode

    def ensure_virtual_terminal_processing(self) -> bool:
        """Ensure virtual terminal processing is enabled.

        This is required for ANSI escape sequences to work properly.

        Returns:
            True if the mode was set successfully, False otherwise.
        """
        if not self._initialized or self._kernel32 is None:
            return False

        import ctypes

        mode = ctypes.c_ulong()
        success = True

        # Enable VT processing on stdout
        if self._stdout_handle:
            if self._kernel32.GetConsoleMode(self._stdout_handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if new_mode != mode.value:
                    if not self._kernel32.SetConsoleMode(self._stdout_handle, new_mode):
                        logger.warning(
                            "WindowsConsoleManager: Failed to set stdout VT mode"
                        )
                        success = False

        # Enable VT input processing on stdin
        if self._stdin_handle:
            if self._kernel32.GetConsoleMode(self._stdin_handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT
                if new_mode != mode.value:
                    if not self._kernel32.SetConsoleMode(self._stdin_handle, new_mode):
                        logger.warning(
                            "WindowsConsoleManager: Failed to set stdin VT mode"
                        )
                        success = False

        return success

    def ensure_mouse_input(self) -> bool:
        """Ensure mouse input is enabled.

        This is required for mouse selection and scrollbar to work.

        Returns:
            True if the mode was set successfully, False otherwise.
        """
        if not self._initialized or self._kernel32 is None:
            return False

        import ctypes

        if not self._stdin_handle:
            return False

        mode = ctypes.c_ulong()
        if not self._kernel32.GetConsoleMode(self._stdin_handle, ctypes.byref(mode)):
            return False

        # Ensure mouse input is enabled
        required_flags = ENABLE_MOUSE_INPUT | ENABLE_EXTENDED_FLAGS
        new_mode = mode.value | required_flags

        # Quick edit mode interferes with mouse input in TUI apps
        # Textual should manage this, but we double-check
        if new_mode & ENABLE_QUICK_EDIT_MODE:
            new_mode &= ~ENABLE_QUICK_EDIT_MODE
            logger.debug(
                "WindowsConsoleManager: Disabling quick edit mode for mouse support"
            )

        if new_mode != mode.value:
            if not self._kernel32.SetConsoleMode(self._stdin_handle, new_mode):
                logger.warning("WindowsConsoleManager: Failed to set mouse input mode")
                return False

        return True

    def refresh_console_modes(self) -> bool:
        """Refresh console modes to ensure they are in a good state.

        This should be called periodically to recover from console mode
        corruption that can occur during long-running sessions.

        Returns:
            True if refresh was successful, False otherwise.
        """
        if not self._initialized:
            return False

        logger.debug("WindowsConsoleManager: Refreshing console modes")

        vt_ok = self.ensure_virtual_terminal_processing()
        mouse_ok = self.ensure_mouse_input()

        if vt_ok and mouse_ok:
            logger.debug("WindowsConsoleManager: Console modes refreshed successfully")
            return True

        logger.warning(
            "WindowsConsoleManager: Console mode refresh had issues: "
            "vt=%s, mouse=%s",
            vt_ok,
            mouse_ok,
        )
        return False

    def restore_original_modes(self) -> bool:
        """Restore the original console modes.

        This should be called when the application exits.

        Returns:
            True if restoration was successful, False otherwise.
        """
        if not self._initialized or self._kernel32 is None:
            return False

        logger.debug("WindowsConsoleManager: Restoring original console modes")

        success = True

        if self._stdin_handle and self._original_stdin_mode is not None:
            if not self._kernel32.SetConsoleMode(
                self._stdin_handle, self._original_stdin_mode
            ):
                logger.warning(
                    "WindowsConsoleManager: Failed to restore stdin mode"
                )
                success = False

        if self._stdout_handle and self._original_stdout_mode is not None:
            if not self._kernel32.SetConsoleMode(
                self._stdout_handle, self._original_stdout_mode
            ):
                logger.warning(
                    "WindowsConsoleManager: Failed to restore stdout mode"
                )
                success = False

        return success


# Global instance for the application
_console_manager: WindowsConsoleManager | None = None


def get_console_manager() -> WindowsConsoleManager | None:
    """Get the global Windows console manager instance.

    Returns:
        The console manager instance, or None if not on Windows.
    """
    global _console_manager

    if not is_windows():
        return None

    if _console_manager is None:
        _console_manager = WindowsConsoleManager()

    return _console_manager


def refresh_console_if_windows() -> bool:
    """Refresh console modes if running on Windows.

    This is a convenience function that can be called periodically
    to prevent console mode corruption.

    Returns:
        True if refresh was successful or not on Windows, False otherwise.
    """
    if not is_windows():
        return True

    manager = get_console_manager()
    if manager is None:
        return True

    return manager.refresh_console_modes()
