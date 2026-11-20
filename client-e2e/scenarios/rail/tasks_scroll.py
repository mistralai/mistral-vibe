"""Moving past the last visible task scrolls the tasks window so the highlighted row stays painted; a scrollbar marks the overflow."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP

_DOWN = "\x1b[B"

handshake = {
    "session/start": {
        "state": {
            "backgroundProcesses": [
                {
                    "processId": f"process-{index}",
                    "command": f"task-{index:02}",
                    "status": "running",
                    "createdAt": "2026-08-24T17:41:00Z",
                }
                for index in range(14)
            ]
        }
    }
}

timeline: Timeline = [*SETUP, CTRL_X, "b", *[_DOWN] * 13, take_snapshot()]

screen_contains = ("Background tasks 14", "task-13")
screen_excludes = ("task-00",)
