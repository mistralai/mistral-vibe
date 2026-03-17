"""Workarounds for VS Code terminal quirks affecting Textual widgets."""

from __future__ import annotations

from textual import events
from textual.widgets import Input


def patch_vscode_keys(event: events.Key) -> None:
    """Patch key events sent as CSI u by VS Code 1.110+.

    VS Code encodes certain keys using the kitty keyboard protocol (CSI u),
    which Textual parses with the correct key name but ``character=None``.
    Input widgets then silently drop the keystroke because there is no
    printable character.

    Affected keys:
    - Space: ``\\x1b[32u`` parsed as ``Key("space", character=None)``
    - Enter: ``\\x1b[13u`` parsed as ``Key("enter", character=None)``

    Assigning the appropriate character restores normal behaviour.
    """
    if event.key == "space" and event.character is None:
        event.character = " "
    elif event.key == "enter" and event.character is None:
        event.character = "\r"


def patch_vscode_space(event: events.Key) -> None:
    """Patch space key events sent as CSI u by VS Code 1.110+.

    .. deprecated::
        Use :func:`patch_vscode_keys` instead to handle all affected keys.

    VS Code encodes space as ``\\x1b[32u`` (CSI u), which Textual parses as
    ``Key("space", character=None, is_printable=False)``.  Input widgets then
    silently drop the keystroke because there is no printable character.
    Assigning ``event.character = " "`` restores normal behaviour.
    """
    patch_vscode_keys(event)


class VscodeCompatInput(Input):
    """``Input`` subclass that handles VS Code CSI-u key encoding quirks."""

    async def _on_key(self, event: events.Key) -> None:
        patch_vscode_keys(event)
        await super()._on_key(event)
