"""Ctrl+X letters the rail over an open window; a letter switches to its item's window and the letters go away."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "t", CTRL_X, take_snapshot("letters"), "g"]

screen_contains = ("▶ Goal active", "▪ 2 tasks · ▶ goal 4m · ≡ 1/3")
screen_excludes = ("Todo 1/3", "t 1/3")
