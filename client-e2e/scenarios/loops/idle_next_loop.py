"""An idle session names its soonest loop and title, and the delay follows the clock minute by minute."""

from __future__ import annotations

from e2e.app_server.events import scheduled_loop
from e2e.app_server.scenario import Timeline, advance, take_snapshot

_LOOPS = [
    scheduled_loop(
        "later",
        "Nightly report\nSummarize the day.",
        in_seconds=3_600,
        interval_seconds=86_400,
    ),
    scheduled_loop(
        "soon",
        "PR watch #42\nCheck the PR's CI and reviews.",
        in_seconds=290,
        interval_seconds=1_200,
    ),
]
handshake = {
    "session/start": {"state": {"session": {"harness": "unified", "loops": _LOOPS}}}
}

timeline: Timeline = [
    take_snapshot(),
    # 50 s later the soonest loop is 4 minutes away.
    advance(50_000),
]

screen_contains = ("◷ next loop in 4m: PR watch #42",)
screen_excludes = ("next loop in 5m", "Nightly report", "Check the PR")
