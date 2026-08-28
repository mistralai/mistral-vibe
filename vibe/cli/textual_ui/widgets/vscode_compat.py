"""Workarounds for VS Code terminal quirks affecting Textual widgets."""

from __future__ import annotations

from textual import events
from textual.widgets import Input

# Map of key names to their characters for CSI u encoded keys.
# Some terminals (like VS Code and macOS Terminal.app) encode keys as CSI u sequences
# (e.g., \\x1b[47u for '/'), which Textual parses as Key("slash", character=None, ...).
# Input widgets silently drop these keystrokes because there is no printable character.
# This mapping restores the correct character for known problematic keys.
CSI_U_CHARACTER_MAP = {
    "space": " ",
    "shift+space": " ",
    "slash": "/",
    "asterisk": "*",
    "minus": "-",
    "plus": "+",
    "period": ".",
    "equal": "=",
    "numpad_equal": "=",
    "numpad_divide": "/",
    "numpad_multiply": "*",
    "numpad_subtract": "-",
    "numpad_add": "+",
    "numpad_decimal": ".",
    "numpad_enter": "\n"
}

# Map of alternative key names to their standard names for CSI u encoded keys.
# Some terminals use different key names for numpad keys (e.g., "kp_enter" instead of "enter").
# This mapping normalizes these to the standard key names that the rest of the code expects.
CSI_U_KEY_NAME_MAP = {
    "kp_enter": "enter",
    "numpad_enter": "enter",
}


def patch_csi_u_keys(event: events.Key) -> None:
    """Patch key events sent as CSI u (like VS Code or modern terminals).

    Fixes space keys and numpad keys that are parsed by Textual with
    character=None, causing input widgets to silently drop them.
    """
    # Normalize key names first (e.g., "kp_enter" -> "enter")
    if event.key in CSI_U_KEY_NAME_MAP:
        event.key = CSI_U_KEY_NAME_MAP[event.key]

    # Set character for keys that should have printable characters
    if event.character is None and event.key in CSI_U_CHARACTER_MAP:
        event.character = CSI_U_CHARACTER_MAP[event.key]


class VscodeCompatInput(Input):
    """``Input`` subclass that handles the VS Code CSI-u space quirk."""

    async def _on_key(self, event: events.Key) -> None:
        patch_vscode_space(event)
        await super()._on_key(event)
