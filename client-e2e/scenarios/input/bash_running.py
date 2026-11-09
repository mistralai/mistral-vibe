"""A manual `!` command shows its command line in a running row before it finishes."""

from __future__ import annotations

from e2e.app_server.events import manual_shell
from e2e.app_server.scenario import Timeline

_running = manual_shell("sleep 10", entry_id="$operationId")
_running["params"]["entry"]["generationStatus"] = "in_progress"
_running["params"]["entry"]["state"] = {"status": "running", "outputText": ""}

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}
on_request = {"session/shellCommand": [_running]}

timeline: Timeline = ["!sleep 10\r"]

capture_startup = False
screen_contains = ("sleep 10",)
