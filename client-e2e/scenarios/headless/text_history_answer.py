"""A text run whose `turn/completed` has no final answer prints the history's last answer."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed
from e2e.app_server.scenario import Timeline

_first = assistant_msg("Looked it up.", entry_id="headless-first")["params"]["entry"]
_last = assistant_msg("From the history.", entry_id="headless-last")["params"]["entry"]

client_args = ("-p", "hello")
handshake = {"session/history/list": {"items": [_first, _last], "nextCursor": None}}
timeline: Timeline = ["", turn_completed()]
headless_stdout = "From the history.\n"
headless_requests = ("session/stop",)
