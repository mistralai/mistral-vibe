"""`r` runs the highlighted loop now with `loops/run`: its row reads `due` and keeps its place and schedule."""

from __future__ import annotations

from e2e.app_server.events import loops_updated, scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot

_SOON = scheduled_loop(
    "soon",
    "PR watch #42\nCheck the PR's CI and reviews.",
    in_seconds=290,
    interval_seconds=1_200,
)
_NIGHTLY = scheduled_loop(
    "nightly",
    "Nightly report\nSummarize the day.",
    in_seconds=86_400,
    interval_seconds=86_400,
)
_DUE = scheduled_loop(
    "nightly",
    "Nightly report\nSummarize the day.",
    in_seconds=0,
    interval_seconds=86_400,
)

handshake = {
    "session/start": {
        "state": {"session": {"harness": "unified", "loops": [_SOON, _NIGHTLY]}}
    },
    "loops/run": {"loop": _DUE},
}
on_request = {"loops/run": [loops_updated([_DUE, _SOON])]}
request_methods = {"loops/run", "loops/delete", "turn/start", "session/turn/enqueue"}

_DOWN = "\x1b[B"
timeline: Timeline = ["/loop\r", _DOWN, "r", take_snapshot()]

screen_contains = (
    "Loops",
    "Nightly report",
    "every 1d",
    "due",
    "r run now",
    "Summarize the day.",
)
screen_excludes = ("Failed to run loop",)
