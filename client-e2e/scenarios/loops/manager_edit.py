"""Enter edits the highlighted loop in a filled form, a click focuses a field, and Enter saves it under the same id."""

from __future__ import annotations

from e2e.app_server.events import loops_updated, scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.pty.mouse import click

_SOON = scheduled_loop(
    "soon",
    "PR watch #42\nCheck the PR's CI and reviews.",
    in_seconds=290,
    interval_seconds=1_200,
)
_WEEKDAYS = scheduled_loop(
    "weekdays",
    "Morning review\nReview yesterday's CI.",
    in_seconds=7_200,
    cron="0 9 * * 1-5",
)
_EDITED = scheduled_loop(
    "weekdays",
    "Morning review\nReview yesterday's CI and flaky tests.",
    in_seconds=3_600,
    interval_seconds=3_600,
)

handshake = {
    "session/start": {
        "state": {"session": {"harness": "unified", "loops": [_WEEKDAYS, _SOON]}}
    },
    "loops/update": {"loop": _EDITED},
}
on_request = {"loops/update": [loops_updated([_SOON, _EDITED])]}
request_methods = {"loops/update", "loops/create", "turn/start", "session/turn/enqueue"}

_DOWN = "\x1b[B"
_CTRL_U = "\x15"
_BACKSPACE = "\x7f"
timeline: Timeline = [
    "/loop\r",
    _DOWN,
    "\r",
    take_snapshot(),
    # The Every label focuses the schedule; a click past the instructions puts the caret at their end.
    click(4, 35),
    _CTRL_U,
    "1h",
    click(40, 36),
    _BACKSPACE,
    " and flaky tests.",
    "\r",
    take_snapshot(),
]

# The edited loop keeps its row and highlight, with its new schedule and instructions.
screen_contains = (
    "Loops",
    "Morning review",
    "every 1h",
    "Review yesterday's CI and flaky tests.",
    "Enter/e edit",
)
screen_excludes = ("Edit loop", "weekdays 09:00")
