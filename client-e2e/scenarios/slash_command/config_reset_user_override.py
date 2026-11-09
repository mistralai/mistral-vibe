"""Ctrl+R removes a user-config override so the inherited default applies again."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import field, fields_read

_OVERRIDDEN = [("user-toml", 64000), ("default", 200000)]
handshake = {
    "config/fields/read": [
        fields_read(field("auto_compact_threshold", "int", 64000, layers=_OVERRIDDEN)),
        fields_read(field("auto_compact_threshold", "int", 200000)),
    ]
}

capture_startup = False
timeline: Timeline = ["/config\r", "\x12"]
screen_contains = ("200000",)
screen_excludes = ("64000",)
