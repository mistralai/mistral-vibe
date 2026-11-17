"""Below the low-disk threshold the panel says the disk is almost full above the suggestion."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(low=True),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", take_snapshot()]

screen_contains = ("14 GB free of 494 GB", "⚠ Your disk is almost full")
