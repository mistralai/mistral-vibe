"""Up and Down refresh file completions at the new caret position."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_QUERY = "@client-e2e/fixtures/fixture.j"
_SECOND_LINE = "x" * len(_QUERY)

capture_startup = False
capture_steps = {2, 3, 6, 7}
settle_per_key = True
expected_clipboard = f"@client-e2e/fixtures/fixture.json !\n{_SECOND_LINE}"

timeline: Timeline = [
    _QUERY,
    "\x01",
    "\x1b[B",
    "\t!",
    "\x03",
    paste(f"{_QUERY}\n{_SECOND_LINE}"),
    "\x1b[A",
    "\t!\x1b[18~\x19",
]
