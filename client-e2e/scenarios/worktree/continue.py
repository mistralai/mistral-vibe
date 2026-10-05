"""--worktree NAME with -c resumes the saved session scoped to the worktree.

Python prepares the worktree first and its session lookups run inside it; the
Rust handshake sequences the same way: the settle precedes the session list,
which is scoped to the worktree directory, and the resume rebinds onto it.
"""

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

_RESUMED = "00000000-0000-4000-8000-000000000003"

client_args = ("--worktree", "feature", "-c")
request_methods = {"session/list", "session/resume"}
on_request = {
    "session/start": [
        # The worktree already exists, so this run reuses it: the settle
        # reports created=False and the exit never probes a removal.
        worktree_settled(
            cwd="/home/user/worktrees/feature",
            name="feature",
            branch="feature",
            path="/home/user/worktrees/feature",
            created=False,
        )
    ]
}
screen_contains = {"rust": ("Resumed session",)}
capture_startup = False
capture_steps = set()
exit_after_last_step = True
timeline: Timeline = [
    "hi\r",
    # The resume rebinds the client onto the saved session, so the turn's
    # events must name it, not the one session/start opened.
    worktree(
        "feature",
        path="/home/user/worktrees/feature",
        created=False,
        session_id=_RESUMED,
    ),
    turn_started(session_id=_RESUMED),
    user_msg("hi", session_id=_RESUMED),
    assistant_msg("Hello.", session_id=_RESUMED),
    turn_completed(session_id=_RESUMED),
    "\x03",
    "\x03",
]
