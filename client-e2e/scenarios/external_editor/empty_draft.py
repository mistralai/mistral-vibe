"""Ctrl+G on an empty composer opens the editor and loads the text it saves."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

env = {"VISUAL": 'sh -c \'printf "written by the editor" > "$1"\' editor'}

capture_startup = False
screen_contains = ("written by the editor",)

timeline: Timeline = [_CTRL_G]
