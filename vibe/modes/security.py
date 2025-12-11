"""ChefChat Mode Security
=======================

Security logic for detecting write operations in bash commands.
Extracted from mode_manager.py for better separation of concerns.

CRITICAL FIX: Regex patterns are now compiled at MODULE LEVEL,
not per-instance, preventing performance degradation.
"""

from __future__ import annotations

import re
from typing import Any

from vibe.modes.constants import (
    READONLY_BASH_COMMANDS,
    READONLY_TOOLS,
    SAFE_GIT_SUBCOMMANDS,
    WRITE_BASH_PATTERNS,
    WRITE_TOOLS,
)

# =============================================================================
# MODULE-LEVEL COMPILED PATTERNS (CRITICAL PERFORMANCE FIX)
# =============================================================================

# Compile regex patterns ONCE at module import time
# Previously these were recompiled for every ModeManager instance
_COMPILED_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern) for pattern in WRITE_BASH_PATTERNS
]


# =============================================================================
# WRITE OPERATION DETECTION
# =============================================================================


def is_write_operation(tool_name: str, args: dict[str, Any] | None = None) -> bool:
    """Detect if an operation would write to files.

    Args:
        tool_name: Name of the tool
        args: Tool arguments (for bash command analysis)

    Returns:
        True if this is a write operation
    """
    # Check known write tools
    if tool_name in WRITE_TOOLS:
        return True

    # Check bash/shell commands
    if tool_name in {"bash", "shell", "run_command", "execute_command"}:
        return is_write_bash_command(args)

    # Check for read-only tools
    if tool_name in READONLY_TOOLS:
        return False

    # Unknown tools are assumed to be potentially write operations
    return True


def is_write_bash_command(args: dict[str, Any] | None) -> bool:  # noqa: PLR0911
    """Check if a bash command is a write operation.

    Args:
        args: Tool arguments containing the command

    Returns:
        True if the command performs write operations
    """
    if not args:
        return False

    # Get command from various possible arg names
    command = (
        args.get("command")
        or args.get("cmd")
        or args.get("CommandLine")
        or args.get("commandLine")
        or ""
    )

    if not command:
        return False

    command = str(command).strip()
    parts = command.split()

    if not parts:
        return False

    base_cmd = parts[0]

    # Check for write patterns FIRST (redirects, rm, etc.)
    # This catches "echo hi > file" even though echo is readonly
    for pattern in _COMPILED_WRITE_PATTERNS:
        if pattern.search(command):
            return True

    # Special handling for git commands
    if base_cmd == "git" and len(parts) > 1:
        subcommand = parts[1]
        # Safe git subcommands are read-only
        if subcommand in SAFE_GIT_SUBCOMMANDS:
            return False
        # Unknown git commands are assumed writes
        return True

    # Known read-only commands are safe
    if base_cmd in READONLY_BASH_COMMANDS:
        return False

    # Unknown commands - be cautious, assume write
    return True
