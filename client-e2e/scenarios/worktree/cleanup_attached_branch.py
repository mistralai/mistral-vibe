"""An attached branch asks a second question before the forced removal."""

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
request_methods = {"session/stop", "workspace/git/worktrees/remove"}
handshake = {
    "workspace/git/worktrees/remove": [
        {
            "outcome": "kept_dirty",
            "reasons": ["uncommitted changes"],
            "branchCreated": False,
            "branchDeleted": False,
        },
        {
            "outcome": "removed",
            "root": "/home/user/worktrees/feature",
            "branch": "feature",
            "branchCreated": False,
            "branchDeleted": True,
            "reasons": [],
        },
    ]
}
exit_responses = {
    "Remove worktree? [y/N] ": "y\n",
    "Also delete branch 'feature'? [y/N] ": "y\n",
}
exit_after_last_step = True
capture_startup = False
capture_steps = set()
timeline: Timeline = [
    "hi\r",
    worktree("feature", path="/home/user/worktrees/feature"),
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    "\x03",
    "\x03",
]
