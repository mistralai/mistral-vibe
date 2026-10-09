"""Cmd+Left/Right at a line edge move on to the previous line start or the next line end."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

screen_contains = {"rust": ("one", "Xtwo", "threeY")}

_CMD_RIGHT = "\x1b[1;9C"  # Super+Right in the kitty keyboard protocol

timeline: Timeline = [
    "one",
    "\n",
    "two",
    "\n",
    "three",
    "\x01",  # Ctrl+A, what macOS terminals send for Cmd+Left: start of `three`
    "\x01",  # already at the line start: start of `two`
    "X",
    _CMD_RIGHT,  # end of `Xtwo`
    _CMD_RIGHT,  # already at the line end: end of `three`
    "Y",
]
