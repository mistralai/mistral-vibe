from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hello world",
    "\x1b[1;6D\x7f",  # select "world", Backspace deletes it -> "hello "
]
