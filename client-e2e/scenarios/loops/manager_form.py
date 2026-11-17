"""`n` adds a loop from a title, a cron and instructions; a refused schedule keeps the form with the reason."""

from __future__ import annotations

from e2e.app_server.events import loops_updated, scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot

_CREATED = scheduled_loop(
    "built",
    "Build watch\nCheck the build and report.",
    in_seconds=7_200,
    cron="0 9 * * 1-5",
)

handshake = {
    "session/start": {"state": {"session": {"harness": "unified"}}},
    "loops/create": {"loop": _CREATED},
}
on_request = {"loops/create": [loops_updated([_CREATED])]}
request_methods = {"loops/create", "turn/start", "session/turn/enqueue"}

timeline: Timeline = [
    "/loop\r",
    take_snapshot(),
    "n",
    "Build watch",
    "\t",
    "every ten minutes",
    "\t",
    "Check the build and report.",
    "\r",
    take_snapshot(),
    # Up from the instructions to the schedule, which Ctrl+U clears.
    "\x1b[A",
    "\x15",
    "0 9 * * 1-5",
    "\r",
    take_snapshot(),
]

screen_contains = (
    "Build watch",
    "weekdays 09:00",
    "Check the build and report.",
    "d delete",
)
screen_excludes = ("New loop", "Use an interval")
