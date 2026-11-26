"""A non-default agent is named before the approval policy in the composer title."""

from __future__ import annotations

from e2e.app_server.agents import LEAN, WITH_LEAN, runtime_read
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {"runtime/read": runtime_read(WITH_LEAN, active=LEAN)}

timeline: Timeline = ["", take_snapshot()]

screen_contains = ("lean · accept edits",)
