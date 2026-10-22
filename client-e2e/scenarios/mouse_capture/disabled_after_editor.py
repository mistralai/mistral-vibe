"""Returning from the Ctrl+G editor keeps mouse reports off with `enable_mouse_capture = false`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"
_CONFIG = {"enableMouseCapture": False}

handshake = {
    "config/read": {"config": _CONFIG},
    "runtime/read": {"runtime": {"config": _CONFIG}},
}
env = {"VISUAL": "true"}
capture_startup = False
mouse_tracking = False
timeline: Timeline = ["x", _CTRL_G]
