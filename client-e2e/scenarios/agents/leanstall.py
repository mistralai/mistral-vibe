"""`/leanstall` installs the missing Lean agent, so a second one answers locally."""

from __future__ import annotations

from e2e.app_server.agents import METHODS, WITH_LEAN, agents_list
from e2e.app_server.scenario import Timeline

handshake = {"agents/install": agents_list(WITH_LEAN)}
request_methods = METHODS

timeline: Timeline = ["/leanstall\r", "/leanstall\r"]

screen_contains = ("Lean agent installed.", "Lean agent is already installed.")
