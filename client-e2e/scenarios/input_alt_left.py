from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "foo bar",
    "\x1b[1;3D\x7f",  # Alt+Left to start of "bar", Backspace removes space -> "foobar"
]
