"""File completion preserves surrounding text and inserts at the Unicode caret."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_SUFFIX = "TAIL @later unchanged"
_LEFT = "\x1b[D"

capture_startup = False
capture_steps = {1, 3}
settle_per_key = True
expected_clipboard = "pré @client-e2e/fixtures/fixture.json !TAIL @later unchanged"

timeline: Timeline = [
    paste(f"pré @client-e2e/fixtures/fixture.j{_SUFFIX}"),
    _LEFT * len(_SUFFIX),
    "\t",
    "!\x1b[18~\x19",
]
