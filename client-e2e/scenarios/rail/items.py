"""The rail shares the loading row: subagents, tasks, goal and todo list sit at its right, the todo list last."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, take_snapshot()]

screen_contains = ("◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
screen_excludes = ("Main conversation", "Run the parity suite", "Cmd/Alt")
