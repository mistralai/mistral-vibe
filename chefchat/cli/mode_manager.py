"""ChefChat Mode Manager (Legacy Compatibility Layer)
=====================================================

DEPRECATED: This module is maintained for backwards compatibility only.
All functionality has been moved to the chefchat.modes package.

Please update your imports:
    OLD: from chefchat.cli.mode_manager import ModeManager, VibeMode
    NEW: from chefchat.modes import ModeManager, VibeMode

This file will be removed in a future version.
"""

from __future__ import annotations

import warnings

# Issue deprecation warning
warnings.warn(
    "chefchat.cli.mode_manager is deprecated. "
    "Please use 'from chefchat.modes import ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the new location for backwards compatibility
from chefchat.modes import (
    MAX_COMMAND_DISPLAY_LEN,
    MODE_CONFIGS,
    MODE_CYCLE_ORDER,
    MODE_DESCRIPTIONS,
    MODE_EMOJIS,
    MODE_PERSONALITIES,
    MODE_TIPS,
    READONLY_BASH_COMMANDS,
    READONLY_TOOLS,
    SAFE_GIT_SUBCOMMANDS,
    WRITE_BASH_PATTERNS,
    WRITE_TOOLS,
    ModeAwareToolExecutor,
    ModeConfig,
    ModeManager,
    ModeState,
    ToolExecutorProtocol,
    VibeMode,
    get_mode_banner,
    inject_mode_into_system_prompt,
    is_write_bash_command,
    is_write_operation,
    mode_from_auto_approve,
    setup_mode_keybindings,
)

__all__ = [
    # Types
    "VibeMode",
    "ModeConfig",
    "ModeState",
    # Manager
    "ModeManager",
    # Executor
    "ModeAwareToolExecutor",
    "ToolExecutorProtocol",
    # Constants
    "MODE_CONFIGS",
    "MODE_CYCLE_ORDER",
    "MODE_EMOJIS",
    "MODE_DESCRIPTIONS",
    "MODE_PERSONALITIES",
    "MODE_TIPS",
    "READONLY_TOOLS",
    "WRITE_TOOLS",
    "READONLY_BASH_COMMANDS",
    "SAFE_GIT_SUBCOMMANDS",
    "WRITE_BASH_PATTERNS",
    "MAX_COMMAND_DISPLAY_LEN",
    # Functions
    "is_write_operation",
    "is_write_bash_command",
    "setup_mode_keybindings",
    "get_mode_banner",
    "inject_mode_into_system_prompt",
    "mode_from_auto_approve",
]
