"""A pinned model opens highlighted under its display name; re-picking it saves its alias."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._pinned import pinned_handshake

handshake = pinned_handshake()

capture_startup = False
request_methods = {"config/reload"}
# Open on the pinned row, pick it, then keep its thinking level.
timeline: Timeline = ["/model", "\r", "\r", "\r"]
screen_contains = ("Configuration reloaded",)
screen_excludes = ("Failed to apply",)
