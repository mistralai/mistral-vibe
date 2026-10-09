"""A saved file that cannot be read back keeps the draft and says why."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

# Exits successfully after deleting the file it was given.
env = {"VISUAL": "sh -c 'rm \"$1\"' editor"}

capture_startup = False
screen_contains = {"rust": ("keep my draft", "Could not read the edited text")}

timeline: Timeline = ["keep my draft", _CTRL_G]
