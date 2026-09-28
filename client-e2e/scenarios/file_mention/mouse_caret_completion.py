"""Clicking or dragging the caret to another mention refreshes its suggestions."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_TEXT = "@README.md @client-e2e/fixtures/fixture.j"
_CLICK = "\x1b[<0;13;36M\x1b[<0;13;36m"
_DRAG = "\x1b[<0;41;36M\x1b[<32;13;36M\x1b[<0;13;36m"

capture_startup = False
capture_steps = {1, 2, 5, 7}
settle_per_key = True
expected_clipboard = "@README.md ! @client-e2e/fixtures/fixture.j"

timeline: Timeline = [
    paste(_TEXT),
    _CLICK,
    "\t!",
    "\x03",
    paste(_TEXT),
    _DRAG,
    "\t!",
    "\x1b[18~\x19",
]
