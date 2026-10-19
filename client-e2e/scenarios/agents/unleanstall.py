"""`/unleanstall` of the active Lean agent applies the server's switch away from it."""

from __future__ import annotations

from e2e.app_server.agents import (
    FIXTURE_ACTIVE,
    FIXTURE_AGENTS,
    LEAN,
    METHODS,
    WITH_LEAN,
    agents_list,
    runtime_read,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": runtime_read(WITH_LEAN, active=LEAN),
    "agents/uninstall": agents_list(FIXTURE_AGENTS, active=FIXTURE_ACTIVE),
}
request_methods = METHODS

# The empty first step captures Lean as the active agent before the uninstall.
timeline: Timeline = ["", "/unleanstall\r", "/unleanstall\r"]

screen_contains = (
    "Lean agent uninstalled.",
    "Lean agent is not installed.",
    "accept edits",
)
