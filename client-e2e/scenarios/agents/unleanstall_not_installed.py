"""`/unleanstall` without Lean answers locally without a request."""

from __future__ import annotations

from e2e.app_server.agents import METHODS
from e2e.app_server.scenario import Timeline

request_methods = METHODS

timeline: Timeline = ["/unleanstall\r"]

screen_contains = ("Lean agent is not installed.",)
