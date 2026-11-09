"""Turning `enable_mouse_capture` on in `/config` turns mouse reports back on without a restart."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import field, fields_read

_CONFIG = {"enableMouseCapture": False}

handshake = {
    "config/read": {"config": _CONFIG},
    "runtime/read": {"runtime": {"config": _CONFIG}},
    "config/fields/read": fields_read(field("enable_mouse_capture", "bool", False)),
    "config/write": {"runtime": {"config": {"enableMouseCapture": True}}},
}

capture_startup = False
mouse_tracking = True
# Open enable_mouse_capture, move from False to True, save.
timeline: Timeline = ["/config\r", "\r", "\x1b[A", "\r"]
