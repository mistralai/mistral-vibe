"""Up and Down refresh file completions at the new caret position."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_QUERY = "@data/fixture.j"
_SECOND_LINE = "x" * len(_QUERY)

expected_clipboard = f"@data/fixture.json !\n{_SECOND_LINE}"

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
