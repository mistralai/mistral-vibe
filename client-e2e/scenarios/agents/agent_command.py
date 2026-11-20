"""`/agent` lists the primary agents and `/agent <name>` switches the session to one."""

from __future__ import annotations

from e2e.app_server.agents import WITH_LEAN, runtime_read
from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": runtime_read(WITH_LEAN)}
request_methods = {"session/agent/update"}

timeline: Timeline = ["/agent\r", "/agent lean\r"]

screen_contains = (
    "default (active)",
    "Switch with /agent <name>.",
    "Switched to agent lean.",
    "lean · accept edits",
)
screen_excludes = ("explore",)
