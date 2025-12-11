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
    from vibe.modes import ModeManager, VibeMode

    manager = ModeManager(initial_mode=VibeMode.NORMAL)
    old, new = manager.cycle_mode()
"""

from __future__ import annotations

# Core types
from vibe.modes.types import ModeConfig, ModeState, VibeMode

# Constants and configurations
from vibe.modes.constants import (
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

# Security functions
from vibe.modes.security import is_write_bash_command, is_write_operation

# Prompt injection
from vibe.modes.prompts import get_system_prompt_modifier

# Main manager
from vibe.modes.manager import ModeManager

# Helper functions
from vibe.modes.helpers import (
    get_mode_banner,
    inject_mode_into_system_prompt,
    mode_from_auto_approve,
    setup_mode_keybindings,
)

# Tool executor
from vibe.modes.executor import ModeAwareToolExecutor, ToolExecutorProtocol

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
    # Security Functions
    "is_write_operation",
    "is_write_bash_command",
    # Prompt Functions
    "get_system_prompt_modifier",
    # Helper Functions
    "setup_mode_keybindings",
    "get_mode_banner",
    "inject_mode_into_system_prompt",
    "mode_from_auto_approve",
]
