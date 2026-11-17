"""A low disk at startup adds one line to the Warnings box pointing to `/storage`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import flagged, usage

handshake = flagged({"storage/usage/read": usage(low=True)})

timeline: Timeline = ["a", take_snapshot()]

screen_contains = (
    "Warnings",
    "Low disk space: 14 GB free of 494 GB. Run /storage to see what Vibe uses.",
)
