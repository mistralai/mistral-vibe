"""Left from the first item wraps to the todo list, whose Enter opens the todo window under the rail."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, LEFT, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, LEFT, "\r", take_snapshot()]

screen_contains = ("Todo 1/3", "Run the parity suite")
