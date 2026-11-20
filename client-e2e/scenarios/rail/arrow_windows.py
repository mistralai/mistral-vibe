"""Ctrl+X then `s` focuses the subagent list with its button lit; ←→ walk to the next item's window and light it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "s",
    take_snapshot("subagents"),
    "\x1b[C",
    take_snapshot("tasks"),
    "\x1b[C",
]

screen_contains = ("▶ Goal active", "Make the parity suite pass")
screen_excludes = ("Main conversation", "Background tasks")
