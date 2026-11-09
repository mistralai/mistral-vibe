from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "foo bar baz",
    "\x17",  # delete "baz" -> "foo bar "
    "\x17",  # delete "bar" -> "foo "
]
