"""ChefChat Mode Helpers
=======================

Helper functions for mode management.
Extracted from mode_manager.py for better organization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyBindings

    from chefchat.modes.manager import ModeManager

from chefchat.modes.constants import MODE_TIPS
from chefchat.modes.types import VibeMode


def setup_mode_keybindings(kb: KeyBindings, mode_manager: ModeManager) -> None:
    """Setup keyboard bindings for mode cycling.

    Adds Shift+Tab binding to cycle through modes.

    Args:
        kb: prompt_toolkit KeyBindings instance
        mode_manager: ModeManager to control

    Usage:
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()
        setup_mode_keybindings(kb, mode_manager)
    """
    from prompt_toolkit.keys import Keys

    @kb.add(Keys.BackTab)  # Shift+Tab
    def cycle_mode_handler(event: Any) -> None:
        """Shift+Tab: Cycle through modes."""
        old_mode, new_mode = mode_manager.cycle_mode()

        # Get display information
        indicator = mode_manager.get_mode_indicator()
        description = mode_manager.get_mode_description()
        tips = MODE_TIPS.get(new_mode, [])

        # Write feedback to terminal
        output = event.app.output
        output.write(
            f"\n\n🔄 Mode: {old_mode.value.upper()} → {new_mode.value.upper()}\n"
        )
        output.write(f"{indicator}: {description}\n")

        # Show tips for the new mode
        if tips:
            output.write("\n")
            for tip in tips:
                output.write(f"  {tip}\n")
        output.write("\n")
        output.flush()


def get_mode_banner(mode_manager: ModeManager) -> str:
    """Generate a startup banner for the current mode.

    Args:
        mode_manager: ModeManager instance

    Returns:
        ASCII art banner with mode info
    """
    indicator = mode_manager.get_mode_indicator()
    description = mode_manager.get_mode_description()

    # Pad strings for alignment
    indicator_padded = f"{indicator:60}"
    description_padded = f"{description:60}"

    return f"""
╔════════════════════════════════════════════════════════════════╗
║  {indicator_padded} ║
║  {description_padded} ║
║                                                                ║
║  💡 Press Shift+Tab to cycle modes                            ║
║  🍳 Type /chef for kitchen status                             ║
╚════════════════════════════════════════════════════════════════╝
"""


def inject_mode_into_system_prompt(base_prompt: str, mode_manager: ModeManager) -> str:
    """Inject mode-specific instructions into the system prompt.

    Args:
        base_prompt: The original system prompt
        mode_manager: ModeManager with current mode state

    Returns:
        System prompt with mode instructions prepended
    """
    mode_modifier = mode_manager.get_system_prompt_modifier()

    if not mode_modifier:
        return base_prompt

    return f"{mode_modifier}\n\n{base_prompt}"


def mode_from_auto_approve(auto_approve: bool) -> VibeMode:
    """Convert legacy auto_approve boolean to a VibeMode.

    Used for backwards compatibility with existing CLI flags.

    Args:
        auto_approve: The --auto-approve flag value

    Returns:
        VibeMode.AUTO if True, VibeMode.NORMAL if False
    """
    return VibeMode.AUTO if auto_approve else VibeMode.NORMAL
