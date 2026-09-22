"""A turn stopped for the limit: bare stderr text, exit 1, session/stop first."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, complete_entry, turn_completed
from e2e.app_server.scenario import Timeline

_ENTRY_ID = "headless-limit-assistant"
_added = assistant_msg(
    "limit reached before answering",
    entry_id=_ENTRY_ID,
    generation_status="in_progress",
)
_completed = {**_added["params"]["entry"], "generationStatus": "completed"}

client_args = ("-p", "hello", "--output", "streaming")
handshake = {"session/history/list": {"items": [_completed], "nextCursor": None}}
timeline: Timeline = [
    "",
    _added,
    complete_entry(_ENTRY_ID),
    turn_completed(stop_reason="limit"),
]
stdout_before_completion = (_completed,)
headless_returncode = 1
headless_stderr = "limit reached before answering\n"
headless_requests = ("session/stop",)
