"""`--yolo` sends the auto-approve option and paints the auto-approve policy the server starts."""

from __future__ import annotations

from e2e.app_server.agents import FIXTURE_AGENTS, fixture_policy, runtime_read
from e2e.app_server.scenario import Timeline

client_args = ("--yolo",)
handshake = {
    "runtime/read": runtime_read(FIXTURE_AGENTS, policy=fixture_policy("auto-approve"))
}
request_methods = {"session/start"}
timeline: Timeline = [""]

screen_contains = ("auto approve",)
screen_excludes = ("· auto-approve",)
