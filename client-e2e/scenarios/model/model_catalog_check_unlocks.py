"""Opening /model refetches a catalog that locks a model: once the plan includes it, it appears in the open list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._locked import LARGE, handshake, unlocked

handshake = handshake()
# Startup reads the config first; the second read is the one /model sends.
on_request = {"config/read": {"calls": [[], [unlocked()]]}}
request_methods = {"config/read"}
screen_contains = (LARGE,)
screen_excludes = ("Checking for new models",)

timeline: Timeline = ["/model", "\r"]
