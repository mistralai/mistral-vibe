"""Emit completed streaming output before the turn completes."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, complete_entry, turn_completed
from e2e.app_server.scenario import Timeline

_ENTRY_ID = "headless-streaming-assistant"
_added = assistant_msg(
    "streamed before completion", entry_id=_ENTRY_ID, generation_status="in_progress"
)

client_args = ("-p", "hello", "--output", "streaming")
timeline: Timeline = ["", _added, complete_entry(_ENTRY_ID), turn_completed()]
stdout_before_completion = (
    {**_added["params"]["entry"], "generationStatus": "completed"},
)
