"""`/loop` alone lists the loops soonest first with their schedule, and `d` deletes the highlighted one."""

from __future__ import annotations

from e2e.app_server.events import loops_updated, scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot

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
_LATER = scheduled_loop(
    "later",
    "Nightly report\nSummarize the day.",
    in_seconds=86_400 * 2,
    interval_seconds=86_400,
)

handshake = {
    "session/start": {
        "state": {
            "session": {"harness": "unified", "loops": [_LATER, _WEEKDAYS, _SOON]}
        }
    },
    "loops/delete": {"loop": _WEEKDAYS},
}
on_request = {"loops/delete": [loops_updated([_SOON, _LATER])]}
request_methods = {"loops/delete", "turn/start", "session/turn/enqueue"}

_DOWN = "\x1b[B"
timeline: Timeline = ["/loop\r", take_snapshot(), _DOWN, "d", take_snapshot()]

# The deleted loop is gone; the highlight moved to the next row, whose instructions show.
screen_contains = (
    "Loops",
    "PR watch #42",
    "Nightly report",
    "every 1d",
    "Summarize the day.",
)
screen_excludes = ("Morning review", "weekdays 09:00", "next loop in")
