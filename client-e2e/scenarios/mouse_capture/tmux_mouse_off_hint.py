"""Inside tmux with `mouse off`, the startup warnings explain how to scroll, with the command, file and keys as code (VIBE-5726)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.mouse_capture._fake_tmux import MOUSE_OFF_ENV

env = MOUSE_OFF_ENV
mouse_tracking = False
request_methods = {"telemetry/record"}
# The snapshot pins the code-span styling of the command, file and keys.
timeline: Timeline = [take_snapshot()]
screen_contains = ("Warnings", "tmux has mouse off", "~/.tmux.conf", "Ctrl+X")
screen_excludes = ("`",)
