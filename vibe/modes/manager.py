"""ChefChat Mode Manager
=======================

Refactored mode manager that orchestrates the mode system.
Previously a 1000+ line "God Class", now a clean coordinator.

This is the main entry point for mode management, delegating to:
- vibe.modes.types: Core type definitions
- vibe.modes.constants: All constants and configurations
- vibe.modes.security: Write operation detection
- vibe.modes.prompts: System prompt injection
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from vibe.modes.constants import MODE_CONFIGS, MODE_CYCLE_ORDER, READONLY_TOOLS
from vibe.modes.prompts import get_system_prompt_modifier
from vibe.modes.security import is_write_operation
from vibe.modes.types import ModeState, VibeMode

if TYPE_CHECKING:
    from vibe.modes.types import ModeConfig


class ModeManager:
    """Central manager for the ChefChat mode system.

    Handles mode cycling, transitions, tool permission checks,
    and system prompt injection.

    Example:
        >>> manager = ModeManager(initial_mode=VibeMode.NORMAL)
        >>> print(manager.get_mode_indicator())
        ✋ NORMAL
        >>> old, new = manager.cycle_mode()
        >>> print(f"{old} -> {new}")
        normal -> auto
    """

    # Class-level constants
    CYCLE_ORDER: ClassVar[tuple[VibeMode, ...]] = MODE_CYCLE_ORDER

    def __init__(self, initial_mode: VibeMode = VibeMode.NORMAL) -> None:
        """Initialize the mode manager.

        Args:
            initial_mode: Starting mode (default: NORMAL for safety)
        """
        self.state = ModeState(current_mode=initial_mode)

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def current_mode(self) -> VibeMode:
        """Get the current mode."""
        return self.state.current_mode

    @property
    def auto_approve(self) -> bool:
        """Whether tools should be auto-approved."""
        return self.state.auto_approve

    @property
    def read_only_tools(self) -> bool:
        """Whether write operations are blocked."""
        return self.state.read_only_tools

    @property
    def config(self) -> ModeConfig:
        """Get the configuration for the current mode."""
        return MODE_CONFIGS[self.state.current_mode]

    # -------------------------------------------------------------------------
    # Mode Transitions
    # -------------------------------------------------------------------------

    def cycle_mode(self) -> tuple[VibeMode, VibeMode]:
        """Cycle to the next mode (Shift+Tab behavior).

        Returns:
            Tuple of (old_mode, new_mode)
        """
        old_mode = self.state.current_mode
        try:
            current_idx = self.CYCLE_ORDER.index(old_mode)
            next_idx = (current_idx + 1) % len(self.CYCLE_ORDER)
        except ValueError:
            # Mode not in cycle order, start from beginning
            next_idx = 0

        new_mode = self.CYCLE_ORDER[next_idx]
        self.set_mode(new_mode)
        return old_mode, new_mode

    def set_mode(self, mode: VibeMode) -> None:
        """Set a specific mode.

        Args:
            mode: The mode to switch to
        """
        config = MODE_CONFIGS[mode]
        now = datetime.now()

        self.state.current_mode = mode
        self.state.auto_approve = config.auto_approve
        self.state.read_only_tools = config.read_only
        self.state.started_at = now
        self.state.mode_history.append((mode, now))

    # -------------------------------------------------------------------------
    # Tool Permission Checks
    # -------------------------------------------------------------------------

    def should_approve_tool(self, tool_name: str) -> bool:
        """Determine if a tool should be automatically approved.

        Args:
            tool_name: Name of the tool being called

        Returns:
            True if tool should be auto-approved, False if confirmation needed
        """
        # Auto-approve mode approves everything
        if self.state.auto_approve:
            return True

        # In read-only mode, only approve read-only tools
        if self.state.read_only_tools:
            return tool_name in READONLY_TOOLS

        # In normal mode (not auto, not read-only), nothing is auto-approved
        return False

    def is_write_operation(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> bool:
        """Detect if an operation would write to files.

        Delegates to vibe.modes.security module.

        Args:
            tool_name: Name of the tool
            args: Tool arguments (for bash command analysis)

        Returns:
            True if this is a write operation
        """
        return is_write_operation(tool_name, args)

    def should_block_tool(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        """Check if a tool should be blocked in the current mode.

        Args:
            tool_name: Name of the tool being called
            args: Tool arguments

        Returns:
            Tuple of (blocked: bool, reason: str | None)
        """
        # If not in read-only mode, nothing is blocked
        if not self.state.read_only_tools:
            return False, None

        # Check if this is a write operation
        if not self.is_write_operation(tool_name, args):
            return False, None

        # Block and provide helpful message
        mode_name = self.state.current_mode.value.upper()
        emoji = self.config.emoji

        reason = f"""⛔ Tool '{tool_name}' blocked in {emoji} {mode_name} mode

This operation would modify files. Current mode is read-only for safety.

**Options:**
1. Press Shift+Tab to switch to NORMAL or AUTO mode
2. Let me add this to the implementation plan instead

What would you like to do?"""

        return True, reason

    # -------------------------------------------------------------------------
    # Display Methods
    # -------------------------------------------------------------------------

    def get_mode_indicator(self) -> str:
        """Get a display string for the current mode.

        Returns:
            String like "📋 PLAN" or "🚀 YOLO"
        """
        emoji = self.config.emoji
        name = self.state.current_mode.value.upper()
        return f"{emoji} {name}"

    def get_mode_description(self) -> str:
        """Get the short description of current mode.

        Returns:
            One-line description of mode behavior
        """
        return self.config.description

    def get_transition_message(self, old_mode: VibeMode, new_mode: VibeMode) -> str:
        """Get the message to display when transitioning modes.

        Args:
            old_mode: The mode being left
            new_mode: The mode being entered

        Returns:
            Formatted transition message
        """
        new_config = MODE_CONFIGS[new_mode]
        return (
            f"🔄 Mode: {old_mode.value.upper()} → {new_mode.value.upper()}\n"
            f"{new_config.emoji} {new_mode.value.upper()}: "
            f"{new_config.description}"
        )

    # -------------------------------------------------------------------------
    # System Prompt Injection
    # -------------------------------------------------------------------------

    def get_system_prompt_modifier(self) -> str:
        """Get mode-specific system prompt injection.

        Delegates to vibe.modes.prompts module.

        Returns:
            XML-formatted mode instruction block
        """
        return get_system_prompt_modifier(self.state.current_mode)
