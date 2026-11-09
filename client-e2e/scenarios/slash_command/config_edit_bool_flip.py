"""Saving the other boolean choice persists the flipped value, not the current one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import field, fields_read

handshake = {
    "config/fields/read": fields_read(field("cursor_blink", "bool", True)),
    "config/write": {"runtime": {"config": {"cursorBlink": False}}},
}

capture_startup = False
# Open cursor_blink, move from True to False, save.
timeline: Timeline = ["/config\r", "\r", "\x1b[B", "\r"]
