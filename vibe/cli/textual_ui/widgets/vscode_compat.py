"""Workarounds for VS Code terminal quirks affecting Textual widgets."""

from __future__ import annotations

import os

from textual import events
from textual.widgets import Input


def _is_vscode_terminal() -> bool:
    """Detect if running inside VS Code integrated terminal."""
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    return term_program == "vscode" or _is_cursor() or _is_vscode_insiders()


def _is_cursor() -> bool:
    """Detect if running inside Cursor IDE terminal."""
    path_indicators = [
        "VSCODE_GIT_ASKPASS_NODE",
        "VSCODE_GIT_ASKPASS_MAIN",
        "VSCODE_IPC_HOOK_CLI",
        "VSCODE_NLS_CONFIG",
    ]
    for var in path_indicators:
        val = os.environ.get(var, "").lower()
        if "cursor" in val:
            return True
    return False


def _is_vscode_insiders() -> bool:
    """Detect if running inside VS Code Insiders terminal."""
    term_version = os.environ.get("TERM_PROGRAM_VERSION", "").lower()
    return term_version.endswith("-insider")


def patch_vscode_key(event: events.Key) -> None:
    """Patch key events affected by VS Code's Kitty keyboard protocol.

    VS Code 1.110+ enables Kitty keyboard protocol by default, which sends
    certain keys as CSI u sequences. Textual may parse these with
    character=None, causing input widgets to drop the keystroke.

    This function patches the following keys:
    - Space: \\x1b[32u -> character=" "

    Args:
        event: The Key event to patch in place.
    """
    # Space key sent as CSI u (\\x1b[32u)
    if event.key == "space" and event.character is None:
        event.character = " "


def patch_vscode_space(event: events.Key) -> None:
    """Patch space key events sent as CSI u by VS Code 1.110+.

    VS Code encodes space as ``\\x1b[32u`` (CSI u), which Textual parses as
    ``Key("space", character=None, is_printable=False)``.  Input widgets then
    silently drop the keystroke because there is no printable character.
    Assigning ``event.character = " "`` restores normal behaviour.

    .. deprecated::
        Use :func:`patch_vscode_key` instead for comprehensive key patching.
    """
    patch_vscode_key(event)


class VscodeCompatInput(Input):
    """``Input`` subclass that handles VS Code terminal quirks."""

    async def _on_key(self, event: events.Key) -> None:
        patch_vscode_key(event)
        await super()._on_key(event)
