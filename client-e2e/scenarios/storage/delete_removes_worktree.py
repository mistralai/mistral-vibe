"""Deleting the only session of a worktree asks to remove it; a worktree the server keeps is reported as kept."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    NO_SESSIONS_LEFT,
    REQUEST_METHODS,
    flagged,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "session/list": NO_SESSIONS_LEFT,
    "workspace/git/worktrees/remove": [
        {"outcome": "removed", "branchDeleted": True, "reasons": []},
        {"outcome": "kept_in_use", "branchDeleted": False, "reasons": []},
    ],
})
request_methods = REQUEST_METHODS

# Delete the largest session, then the next one, whose worktree another process still holds.
timeline: Timeline = [
    "/storage",
    "\r",
    DOWN,
    "d",
    "y",
    take_snapshot(),
    "d",
    "y",
    take_snapshot(),
]

screen_contains = ("Its worktree was removed.", "Its worktree was kept: kept in use.")
