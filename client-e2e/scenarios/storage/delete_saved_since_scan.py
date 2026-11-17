"""Deleting the last session the snapshot counts in a worktree keeps it when a session saved since the scan works below it."""

from __future__ import annotations

from typing import Any

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import DOWN, REQUEST_METHODS, flagged, usage


def _saved(sid: str, title: str, cwd: str) -> dict[str, Any]:
    return {
        "id": sid,
        "rootSessionId": sid,
        "parentSessionId": None,
        "title": title,
        "status": {"type": "idle"},
        "createdAt": 1_787_821_243_000,
        "updatedAt": 1_787_821_245_000,
        "cwd": cwd,
    }


# Two pages: the session under the deleted session's worktree is on the second.
_PAGES = [
    {
        "items": [
            _saved(
                "5e550000-0000-4000-8000-000000000098", "Elsewhere", "/work/dashboard"
            )
        ],
        "nextCursor": "page-2",
        "previousCursor": None,
    },
    {
        "items": [
            _saved(
                "5e550000-0000-4000-8000-000000000099",
                "Started after the scan",
                "/home/user/.vibe/worktrees/dashboard-aaaaaaaaaaaa/rebase/packages/api",
            )
        ],
        "nextCursor": None,
        "previousCursor": "page-1",
    },
]

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "session/list": _PAGES,
})
request_methods = REQUEST_METHODS

# Delete the largest session: the snapshot counts no other session in its worktree.
timeline: Timeline = ["/storage", "\r", DOWN, "d", "y", take_snapshot()]

screen_contains = ("Its worktree stays: 1 other session uses it.",)
