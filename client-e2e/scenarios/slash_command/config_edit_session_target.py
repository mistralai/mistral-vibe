"""Tab in the editor switches the save target to the session-only override layer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.slash_command._config_fixtures import field, fields_read

handshake = {
    "config/fields/read": fields_read(field("enable_notifications", "bool", True))
}

# Pick False, Tab past user and project config to the session layer, save.
timeline: Timeline = ["/config\r", "\r", "\x1b[B", "\t", "\t", "\r", take_snapshot()]
