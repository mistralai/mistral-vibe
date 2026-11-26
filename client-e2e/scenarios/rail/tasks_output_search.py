"""`/` then a query jumps to its latest match in the output, ignoring case, every match lit and the current one marked."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", "/", "BATCH-1", take_snapshot()]

screen_contains = (
    "batch-19.pdf",
    "batch-20.pdf",
    "10/10",
    "Enter keep  Esc leave search",
)
screen_excludes = ("progress 100%",)
