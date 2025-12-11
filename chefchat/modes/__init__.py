"""ChefChat Modes Package
========================

Modular mode system for ChefChat CLI.

This package provides a clean, maintainable architecture for the mode system,
replacing the previous 1000+ line "God Class" with specialized modules.

Architecture:
- types.py: Core type definitions (VibeMode, ModeConfig, ModeState)
- constants.py: All constants, mappings, and configurations
- security.py: Write operation detection with compiled regex patterns
- prompts.py: System prompt injection for each mode
- manager.py: Orchestrator that ties everything together

Usage:
    from chefchat.modes import ModeManager, VibeMode

    manager = ModeManager(initial_mode=VibeMode.NORMAL)
    old, new = manager.cycle_mode()
"""

from __future__ import annotations

# Constants and configurations
from chefchat.modes.constants import (
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
)

# Tool executor
from chefchat.modes.executor import ModeAwareToolExecutor, ToolExecutorProtocol

# Helper functions
from chefchat.modes.helpers import (
    get_mode_banner,
    inject_mode_into_system_prompt,
    mode_from_auto_approve,
    setup_mode_keybindings,
)

# Main manager
from chefchat.modes.manager import ModeManager

# Prompt injection
from chefchat.modes.prompts import get_system_prompt_modifier

# Security functions
from chefchat.modes.security import is_write_bash_command, is_write_operation

# Core types
from chefchat.modes.types import ModeConfig, ModeState, VibeMode

__all__ = [  # noqa: RUF022
    # Constants
    "MAX_COMMAND_DISPLAY_LEN",
    "MODE_CONFIGS",
    "MODE_CYCLE_ORDER",
    "MODE_DESCRIPTIONS",
    "MODE_EMOJIS",
    "MODE_PERSONALITIES",
    "MODE_TIPS",
    "READONLY_BASH_COMMANDS",
    "READONLY_TOOLS",
    "SAFE_GIT_SUBCOMMANDS",
    "WRITE_BASH_PATTERNS",
    "WRITE_TOOLS",
    # Executor
    "ModeAwareToolExecutor",
    "ToolExecutorProtocol",
    # Helper Functions
    "get_mode_banner",
    "inject_mode_into_system_prompt",
    "mode_from_auto_approve",
    "setup_mode_keybindings",
    # Manager
    "ModeManager",
    # Prompt Functions
    "get_system_prompt_modifier",
    # Security Functions
    "is_write_bash_command",
    "is_write_operation",
    # Types
    "ModeConfig",
    "ModeState",
    "VibeMode",
]
