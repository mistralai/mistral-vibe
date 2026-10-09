"""Bash mode owns its prefix, resets on Backspace, dispatches shell input, and folds its result."""

from __future__ import annotations

from e2e.app_server.events import manual_shell
from e2e.app_server.scenario import Timeline

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}

on_request = {
    "session/shellCommand": [
        manual_shell(
            "printf hello",
            "old\r\x1b[31mhello\x1b[0m\x1b]0;hidden\x07\x00\x08\x7f",
            entry_id="$operationId",
        )
    ]
}


# Expand the folded result to reveal its output, then collapse it again from the same
# header row, one column over so the second press does not chain into a double click.
_EXPAND = "\x1b[<0;1;12M\x1b[<0;1;12m"
_COLLAPSE = "\x1b[<0;2;12M\x1b[<0;2;12m"

timeline: Timeline = ["!", "\x7f", "!   \r", "!printf hello\r", _EXPAND, _COLLAPSE]
