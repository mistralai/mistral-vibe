"""Hovering a rail item tints it like a hovered button; the jump letters stay hidden until Ctrl+X."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

# The goal item starts at column 104 of the rail row 34 (1-based SGR coordinates).
_HOVER_GOAL = "\x1b[<35;106;34M"

timeline: Timeline = [*SETUP, _HOVER_GOAL, take_snapshot()]

screen_contains = ("◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
