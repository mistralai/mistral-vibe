"""A rejected `session/fork` mounts the branch error and records no branch telemetry."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS

handshake = {
    "session/fork": {
        "error": {
            "code": "conflict",
            "message": "Detached forks require session logging to be enabled",
        }
    }
}
request_methods = REQUEST_METHODS

capture_startup = False
screen_contains = (
    "Failed to branch session: Detached forks require session logging to be",
)
timeline: Timeline = ["/branch\r"]
