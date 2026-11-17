"""In `/raw`, a selection highlights and copies leading indentation and `>` quote markers."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = """\
- First item
  - Nested item
    - Deep nesting
> A quote
>> Deeper level"""

_PROMPT = "show nesting"
_SELECT_ALL = "\x1b[<0;1;11M\x1b[<32;30;15M\x1b[<0;30;15m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(expected_clipboard),
    turn_completed(),
    "/raw\r",
    _SELECT_ALL,
    take_snapshot("selected"),
]
