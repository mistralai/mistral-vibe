"""An enum setting is edited through its choice list and the picked choice is saved."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.slash_command._config_fixtures import field, fields_read

handshake = {
    "config/fields/read": fields_read(
        field(
            "otel_redaction", "enum", "default", choices=["default", "none", "strict"]
        )
    )
}

timeline: Timeline = ["/config\r", "\r", "\x1b[B", "\r", take_snapshot()]
