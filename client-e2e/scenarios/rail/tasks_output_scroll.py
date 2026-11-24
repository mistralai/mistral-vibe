"""The output scrolls up with k to its first line."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", *["k"] * 40]

screen_contains = ("scanning invoices/batch-01.pdf", "Esc back")
screen_excludes = ("progress 100%",)
