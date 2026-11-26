"""A tmux `mouse off` warning dismissed in an earlier session stays hidden (VIBE-5726)."""

from __future__ import annotations

import atexit
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG
from e2e.app_server.scenario import Timeline
from scenarios.mouse_capture._fake_tmux import MOUSE_OFF_ENV

_home = tempfile.mkdtemp(prefix="e2e_tmux_hint_dismissed_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text("[tmux_mouse_hint]\ndismissed = 1\n")

env = {**MOUSE_OFF_ENV, "VIBE_HOME": _home}
mouse_tracking = False
request_methods = {"telemetry/record"}
timeline: Timeline = ["x"]
screen_contains = ("> x",)
screen_excludes = ("Warnings", "tmux has mouse off")
