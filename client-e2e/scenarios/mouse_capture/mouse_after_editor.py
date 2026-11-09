"""Returning from the Ctrl+G editor turns mouse reports back on by default."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

env = {"VISUAL": "true"}
capture_startup = False
mouse_tracking = True
timeline: Timeline = ["x", _CTRL_G]
