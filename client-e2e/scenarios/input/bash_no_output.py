"""A manual `!` command that prints nothing settles as a quiet success, with no error row."""

from __future__ import annotations

from e2e.app_server.events import manual_shell
from e2e.app_server.scenario import Timeline

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}
on_request = {"session/shellCommand": [manual_shell("true", entry_id="$operationId")]}

# The lone manual result stays ungrouped, so its own header row unfolds it.
_EXPAND = "\x1b[<0;1;7M\x1b[<0;1;7m"

timeline: Timeline = ["!true\r", _EXPAND]

capture_startup = False
screen_contains = ("Ran true", "(no content)")
screen_excludes = ("Error:", "Command failed")
