"""Workarounds for VS Code terminal quirks affecting Textual widgets."""

from __future__ import annotations

from textual import events
from textual.widgets import Input


def patch_vscode_space(event: events.Key) -> None:
    """Patch space key events sent as CSI u by VS Code 1.110+.

    VS Code encodes space as ``\\x1b[32u`` (CSI u), which Textual parses as
    ``Key("space", character=None, is_printable=False)``.  Input widgets then
    silently drop the keystroke because there is no printable character.
    Assigning ``event.character = " "`` restores normal behaviour.
    """
    if event.key == "space" and event.character is None:
        event.character = " "


class VscodeCompatInput(Input):
    """``Input`` subclass that handles the VS Code CSI-u space quirk."""

    async def _on_key(self, event: events.Key) -> None:
        patch_vscode_space(event)
        await super()._on_key(event)
