"""Bash mode owns its prefix, resets on Backspace, dispatches shell input, and folds its result."""

from __future__ import annotations

from e2e.app_server.events import manual_shell
from e2e.app_server.scenario import Timeline

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}

on_request = {
    "session/shellCommand": [
        manual_shell("printf hello", "hello", entry_id="$operationId")
    ]
}


# Expand the folded result to reveal its output, then collapse it again. Expanding
# adds the output line, so the header rises one row: the collapse click targets it.
_EXPAND = "\x1b[<0;1;32M\x1b[<0;1;32m"
_COLLAPSE = "\x1b[<0;1;31M\x1b[<0;1;31m"

timeline: Timeline = ["!", "\x7f", "!   \r", "!printf hello\r", _EXPAND, _COLLAPSE]
