"""An open `/loop` keeps its rows in place as runs reschedule and loops arrive; reopening sorts them again."""

from __future__ import annotations

from e2e.app_server.events import loops_updated, scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot

_FIRST = scheduled_loop(
    "first", "Build watch\nCheck the build.", in_seconds=60, interval_seconds=600
)
_SECOND = scheduled_loop(
    "second", "PR watch #42\nCheck the PR.", in_seconds=290, interval_seconds=1_200
)
_GONE = scheduled_loop(
    "gone", "Old check\nNo longer needed.", in_seconds=200, interval_seconds=900
)
# While the delete is answered, `first` ran and is due after `second`, and `third` arrived due first.
_RAN = scheduled_loop(
    "first", "Build watch\nCheck the build.", in_seconds=660, interval_seconds=600
)
_THIRD = scheduled_loop(
    "third", "Deploy check\nCheck the deploy.", in_seconds=30, interval_seconds=300
)

handshake = {
    "session/start": {
        "state": {"session": {"harness": "unified", "loops": [_FIRST, _SECOND, _GONE]}}
    },
    "loops/delete": {"loop": _GONE},
}
on_request = {"loops/delete": [loops_updated([_RAN, _SECOND, _THIRD])]}
request_methods = {"loops/delete"}

# `j` highlights Old check, the middle row, and `d` deletes it: the highlight moves to PR watch
# whether the delete's answer or the session update lands first.
timeline: Timeline = [
    "/loop\r",
    "j",
    "d",
    take_snapshot("kept"),
    "\x1b",
    "/loop\r",
    take_snapshot("reopened"),
]

# Reopened: soonest first, the highlight back on the first row.
screen_contains = ("Deploy check", "Check the deploy.")
screen_excludes = ("Ctrl+R",)
