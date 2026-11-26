"""A pinned model opens highlighted under its display name; re-picking it saves its alias."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._pinned import pinned_handshake

handshake = pinned_handshake()

request_methods = {"config/reload"}
# Open on the pinned row, pick it, then keep its thinking level.
timeline: Timeline = ["/model", "\r", "\r", "\r"]
screen_contains = ("Applied: model devstral-small, thinking off.",)
screen_excludes = ("Failed to apply",)
