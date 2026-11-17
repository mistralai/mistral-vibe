"""Ctrl+N removes the tmux `mouse off` warning, and the box with it when it was the only one (VIBE-5726)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.mouse_capture._fake_tmux import MOUSE_OFF_ENV

_CTRL_N = "\x0e"

env = MOUSE_OFF_ENV
mouse_tracking = False
request_methods = {"telemetry/record"}
# The `x` settles once the warning has mounted, so Ctrl+N answers a shown warning.
timeline: Timeline = ["x", _CTRL_N]
screen_excludes = ("Warnings", "tmux has mouse off")
