"""A text run prints the answer `turn/completed` carries, not a draft the history keeps."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed
from e2e.app_server.scenario import Timeline

_final = assistant_msg("The final answer.", entry_id="headless-final")["params"][
    "entry"
]
_draft = assistant_msg("A late draft.", entry_id="headless-draft")["params"]["entry"]
_completed = turn_completed()
_completed["params"]["turn"]["finalAnswer"] = "The final answer."

client_args = ("-p", "hello")
handshake = {"session/history/list": {"items": [_final, _draft], "nextCursor": None}}
timeline: Timeline = ["", _completed]
headless_stdout = "The final answer.\n"
headless_requests = ("session/stop",)
