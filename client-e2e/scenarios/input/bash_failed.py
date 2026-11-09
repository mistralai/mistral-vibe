"""A manual `!` command that exits non-zero settles as a failed shell result."""

from __future__ import annotations

from e2e.app_server.events import manual_shell
from e2e.app_server.scenario import Timeline

_COMMAND = "bash -c 'exit 7'"
_MESSAGE = "Command exited with status 7"

_failed = manual_shell(_COMMAND, entry_id="$operationId")
_failed["params"]["entry"]["state"] = {
    "status": "failed",
    "error": {"message": _MESSAGE, "code": None, "details": None},
    "outputText": "",
    "durationMs": 0.0,
    "display": {"success": False, "message": _MESSAGE},
}

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}
on_request = {"session/shellCommand": [_failed]}

# The lone manual result stays ungrouped, so its own header row unfolds the error.
_EXPAND = "\x1b[<0;1;7M\x1b[<0;1;7m"

timeline: Timeline = [f"!{_COMMAND}\r", _EXPAND]

capture_startup = False
screen_contains = (_COMMAND, f"Error: {_MESSAGE}")
