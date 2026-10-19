"""A `session/fork` answer that cannot be decoded shows the branch error instead of crashing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS

handshake = {"session/fork": {"state": {"unexpected": True}}}
request_methods = REQUEST_METHODS

capture_startup = False
screen_contains = ("Failed to branch session: missing field",)
timeline: Timeline = ["/branch\r"]
