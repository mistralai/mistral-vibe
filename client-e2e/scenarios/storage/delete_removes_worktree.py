"""A row's delete asks first with its `deleteBytes`, then sends one `storage/sessions/delete`; a worktree the server keeps is reported."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    REBASE,
    REQUEST_METHODS,
    SCOPES,
    flagged,
    sessions_deleted,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/sessions/delete": [
        sessions_deleted(
            [REBASE], worktrees=["dashboard-aaaaaaaaaaaa/rebase"], freed=2_100_000_000
        ),
        {
            **sessions_deleted([SCOPES], freed=236_000_000),
            "keptWorktrees": [
                {"id": "dashboard-aaaaaaaaaaaa/scopes", "reason": "in_use"}
            ],
        },
    ],
})
request_methods = REQUEST_METHODS

# Delete the largest session, then the next one, whose worktree another process still holds.
timeline: Timeline = [
    "/storage",
    "\r",
    DOWN,
    "d",
    take_snapshot(),
    "y",
    take_snapshot(),
    "d",
    "y",
    take_snapshot(),
]

screen_contains = (
    'Deleted "Rebase PR 71711 on the OIDC session work". Freed about 2.1 GB.',
    'Deleted "Add Vibe scopes to the ALBE OIDC config". Freed about 236 MB.',
    "Kept 1 worktree: in use (scopes).",
)
