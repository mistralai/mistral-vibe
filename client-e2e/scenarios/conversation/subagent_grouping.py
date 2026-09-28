from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    read_file,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate the investigation"
_TASK = "find the transcript grouping code"

screen_contains = {
    client: (
        "Read files, ran subagents",
        f"Explored {_TASK}",
        "response: Found the code.",
        "turns_used: 1",
        "completed: True",
    )
    for client in ("rust", "python")
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("before.txt", "   1→before", num_lines=1),
    subagent(_TASK),
    subagent("summarize the findings", tool_name="task", response="Found the code."),
    read_file("after.txt", "   1→after", num_lines=1),
    assistant_msg("Done."),
    turn_completed(),
    "\x1b[<0;1;30M\x1b[<0;1;30m",
    "\x1b[<0;5;29M\x1b[<0;5;29m",
]
