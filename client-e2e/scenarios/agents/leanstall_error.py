"""A failing `agents/install` shows the server's bare message."""

from __future__ import annotations

from e2e.app_server.agents import METHODS
from e2e.app_server.scenario import Timeline

handshake = {
    "agents/install": {
        "error": {"code": "invalid_params", "message": "Agent 'lean' not found."}
    }
}
request_methods = METHODS

timeline: Timeline = ["/leanstall\r"]

screen_contains = ("Agent 'lean' not found.",)
screen_excludes = ("Lean agent installed.",)
