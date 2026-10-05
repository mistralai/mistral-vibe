"""Pasted tabs expand to 4-column stops and delete cleanly like Textual's TextArea."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_BACKSPACE = "\x7f"

timeline: Timeline = [
    paste("build:\n\tcargo build\n\tcargo test\na\tb"),
    _BACKSPACE * 2,
    "x",
]

settle_per_key = True
