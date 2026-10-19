"""Ctrl+R right after saving an edit reverts the persisted value to the default."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import field, fields_read

_DEFAULT = fields_read(field("auto_compact_threshold", "int", 200000))
_EDITED = [("user-toml", 64000), ("default", 200000)]
handshake = {
    "config/fields/read": [
        _DEFAULT,
        fields_read(field("auto_compact_threshold", "int", 64000, layers=_EDITED)),
        _DEFAULT,
    ]
}

capture_startup = False
# Replace 200000 with 64000, save, then reset the saved value.
timeline: Timeline = ["/config\r", "\r", "\x7f" * 6 + "64000", "\r", "\x12"]
screen_contains = ("200000",)
screen_excludes = ("64000",)
