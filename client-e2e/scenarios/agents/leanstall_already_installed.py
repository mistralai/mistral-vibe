"""`/leanstall` with Lean already present answers locally without a request."""

from __future__ import annotations

from e2e.app_server.agents import METHODS, WITH_LEAN, runtime_read
from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": runtime_read(WITH_LEAN)}
request_methods = METHODS

timeline: Timeline = ["/leanstall\r"]

screen_contains = ("Lean agent is already installed.",)
