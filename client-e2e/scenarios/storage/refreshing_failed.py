"""Opened during the startup scan, the panel joins it; its failure, even before the answer, clears `refreshing…`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import REQUEST_METHODS, flagged, updated, usage

_CACHED = usage()

handshake = flagged({
    "storage/usage/read": {**_CACHED, "status": "refreshing"},
    # The startup refresh starts scan 1; the panel's joins it.
    "storage/usage/refresh": [
        {"started": True, "scanId": 1},
        {"started": False, "reason": "already_running", "scanId": 1},
    ],
})
on_request = {
    "storage/usage/refresh": {
        "calls": [[], [updated({**_CACHED, "status": "failed"}, scan=1)]]
    }
}
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", take_snapshot()]

screen_contains = ("last refresh failed",)
screen_excludes = ("refreshing…",)
