"""On a wide terminal the highlighted loop's full instructions sit beside the rows; on a narrow one, under them."""

from __future__ import annotations

from e2e.app_server.events import scheduled_loop
from e2e.app_server.scenario import Timeline, resize, take_snapshot

_INSTRUCTIONS = (
    "Check the PR's CI and reviews, then summarize every new comment and failing check "
    "in a short report, and suggest the next step for each failing check."
)
_LOOPS = [
    scheduled_loop(
        "soon", f"PR watch #42\n{_INSTRUCTIONS}", in_seconds=290, interval_seconds=1_200
    ),
    scheduled_loop(
        "later",
        "Nightly report\nSummarize the day.",
        in_seconds=3_600,
        interval_seconds=86_400,
    ),
]
handshake = {
    "session/start": {"state": {"session": {"harness": "unified", "loops": _LOOPS}}}
}

timeline: Timeline = [
    resize(40, 160),
    "/loop\r",
    take_snapshot("beside"),
    resize(40, 80),
    take_snapshot("under"),
]

screen_contains = ("suggest the next step for each failing check.",)
