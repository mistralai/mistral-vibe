"""--worktree NAME sends a create worktree input with session/start."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
    worktree,
    worktree_settled,
)
from e2e.app_server.scenario import Timeline

on_request = {
    "session/start": [
        worktree_settled(
            cwd="/home/user/worktrees/feature",
            name="feature",
            branch="feature",
            path="/home/user/worktrees/feature",
            created=True,
        )
    ]
}
client_args = ("--worktree", "feature")
screen_contains = {"rust": ("Created worktrees",)}
request_methods = {"session/start"}
timeline: Timeline = [
    "hi\r",
    worktree("feature", path="/home/user/worktrees/feature"),
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
]
