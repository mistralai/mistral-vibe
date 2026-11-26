"""Esc after previewing another theme restores the original one and writes nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_ESCAPE = "\x1b[27u"

request_methods = {"config/reload"}
# Down previews atom-one-dark; Esc must bring ansi-dark back.
timeline: Timeline = [
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    _ESCAPE,
    take_snapshot(),
]
