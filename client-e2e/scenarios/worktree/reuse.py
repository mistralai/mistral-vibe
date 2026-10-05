"""A reused worktree is never probed for exit removal."""

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
            created=False,
        )
    ]
}
client_args = ("--worktree", "feature")
request_methods = {"workspace/git/worktrees/remove"}
exit_after_last_step = True
capture_startup = False
capture_steps = set()
timeline: Timeline = [
    "hi\r",
    worktree("feature", path="/home/user/worktrees/feature", created=False),
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    "\x03",
    "\x03",
]
