"""Config issues the session reports are printed on stderr before the turn."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, complete_entry, turn_completed
from e2e.app_server.scenario import Timeline

_ENTRY_ID = "headless-config-issues-assistant"
_added = assistant_msg("answered", entry_id=_ENTRY_ID, generation_status="in_progress")
_REJECTED = "Agent 'auditor': safety: 'bogus' is not one of 'safe', 'neutral'"

client_args = ("-p", "hello", "--output", "streaming")
handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {"file": "/project/.vibe/agents/auditor.toml", "message": _REJECTED},
                {"file": "", "message": "A process-wide issue."},
            ]
        }
    }
}
timeline: Timeline = ["", _added, complete_entry(_ENTRY_ID), turn_completed()]
stdout_before_completion = (
    {**_added["params"]["entry"], "generationStatus": "completed"},
)
headless_stderr = (
    f"/project/.vibe/agents/auditor.toml: {_REJECTED}\nA process-wide issue.\n"
)
headless_requests = ("runtime/read",)
