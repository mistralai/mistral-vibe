"""`--yolo` sends the auto-approve option and paints the auto-approve agent the server starts."""

from __future__ import annotations

from e2e.app_server.agents import FIXTURE_AGENTS, runtime_read
from e2e.app_server.scenario import Timeline

_AUTO_APPROVE = next(
    agent for agent in FIXTURE_AGENTS if agent["name"] == "auto-approve"
)

client_args = ("--yolo",)
handshake = {"runtime/read": runtime_read(FIXTURE_AGENTS, _AUTO_APPROVE)}
request_methods = {"session/start"}
timeline: Timeline = [""]

screen_contains = ("auto approve",)
screen_excludes = ("· auto-approve",)
