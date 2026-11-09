"""Bare --worktree with a positional prompt sends an auto input with the prompt."""

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
            cwd="/home/user/worktrees/say-hi",
            name="say-hi",
            branch="say-hi",
            path="/home/user/worktrees/say-hi",
            created=True,
        )
    ]
}
client_args = ("say hi", "--worktree")
request_methods = {"session/start"}
timeline: Timeline = [
    "",
    worktree("say-hi", path="/home/user/worktrees/say-hi"),
    turn_started(),
    user_msg("say hi"),
    assistant_msg("Hello."),
    turn_completed(),
]
