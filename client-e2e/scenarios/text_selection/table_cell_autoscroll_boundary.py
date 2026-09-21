"""A table-cell drag only auto-scrolls when its clamped selection reaches an edge."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
screen_contains = {"rust": ("│ Table", "Selection remains scoped to this cell")}
screen_rows = {"rust": {1: "  Earlier paragraph 08."}}

_PROMPT = "keep the table selection still"
_PREFACE = "\n\n".join(f"Earlier paragraph {index:02d}." for index in range(1, 21))
_TABLE = "\n".join([
    "| Feature | Behavior | Notes |",
    "| --- | --- | --- |",
    "| Table cells | Selection remains scoped to this cell | No viewport jump |",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(f"{_PREFACE}\n\n{_TABLE}"),
    turn_completed(),
    "\x1b[<0;18;29M",
    "\x1b[<32;18;2M",
    "\x1b[<0;18;2m",
]
