"""Deleting a session whose worktree another session shares keeps the worktree and never removes it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import DOWN, REQUEST_METHODS, TRACK, flagged, usage

_BEFORE = usage()

# The saved sessions now: Track still works in the shared worktree.
_SAVED = {
    "items": [
        {
            "id": TRACK,
            "rootSessionId": TRACK,
            "parentSessionId": None,
            "title": "Track disk usage per session",
            "status": {"type": "idle"},
            "createdAt": 1_787_821_243_000,
            "updatedAt": 1_787_821_245_000,
            "cwd": "/home/user/.vibe/worktrees/dashboard-aaaaaaaaaaaa/disk",
        }
    ],
    "nextCursor": None,
    "previousCursor": None,
}

handshake = flagged({
    "storage/usage/read": _BEFORE,
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "session/list": _SAVED,
})
request_methods = REQUEST_METHODS

# Rows by size: suggestion, Rebase, Scopes, Track (open), Review; Review shares Track's worktree.
timeline: Timeline = [
    "/storage",
    "\r",
    DOWN * 4,
    "d",
    take_snapshot(),
    "y",
    take_snapshot(),
]

screen_contains = ("Its worktree stays: 1 other session uses it.",)
