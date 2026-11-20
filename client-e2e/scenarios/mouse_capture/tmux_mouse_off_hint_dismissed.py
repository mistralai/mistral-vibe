"""Ctrl+X then `h` removes the tmux `mouse off` warning, and the box with it when it was the only one (VIBE-5726)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.mouse_capture._fake_tmux import MOUSE_OFF_ENV

_CTRL_X = "\x18"

env = MOUSE_OFF_ENV
mouse_tracking = False
request_methods = {"telemetry/record"}
# The `x` settles once the warning has mounted, so Ctrl+X answers a shown warning.
timeline: Timeline = ["x", _CTRL_X, "h"]
screen_contains = ("> x",)
screen_excludes = ("Warnings", "tmux has mouse off", "> xh")
