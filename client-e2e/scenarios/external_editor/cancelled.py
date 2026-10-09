"""An editor that exits with an error leaves the draft untouched."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

# Writes over the file, then fails like Vim's `:cq`: the write must be ignored.
env = {"VISUAL": "sh -c 'printf discarded > \"$1\"; exit 1' editor"}

capture_startup = False
screen_contains = {"rust": ("keep my draft",)}
screen_excludes = {"rust": ("discarded",)}

timeline: Timeline = ["keep my draft", _CTRL_G]
