"""The theme setting is edited through the list of available themes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import field, fields_read

handshake = {
    "config/fields/read": fields_read(
        field(
            "theme",
            "str",
            "ansi-dark",
            layers=[("user-toml", "ansi-dark"), ("default", "ansi-dark")],
        )
    ),
    "config/write": {"runtime": {"config": {"theme": "atom-one-dark"}}},
}

capture_startup = False
# The server sends theme as a plain string; the client offers its themes. Down from ansi-dark.
timeline: Timeline = ["/config\r", "\r", "\x1b[B", "\r"]
