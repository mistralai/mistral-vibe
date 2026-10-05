"""`/teleport` with a saved project link goes straight to Vibe Code Web, whose link opens."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline
from e2e.app_server.teleport import (
    METHODS,
    START,
    URL,
    complete,
    event,
    handshake as teleport_handshake,
)

handshake = teleport_handshake()
request_methods = METHODS
on_request = {START: [event("summarizing_context"), event("checking_git"), *complete()]}
expected_actions = {"rust": [Action("open_url", URL)]}

_ROW, _COL = 32, 18
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

timeline: Timeline = ["/teleport\r", _CLICK]
