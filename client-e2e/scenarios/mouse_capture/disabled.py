"""`enable_mouse_capture = false` turns mouse reports off once the config arrives."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CONFIG = {"enableMouseCapture": False}

handshake = {
    "config/read": {"config": _CONFIG},
    "runtime/read": {"runtime": {"config": _CONFIG}},
}
mouse_tracking = False
timeline: Timeline = []
