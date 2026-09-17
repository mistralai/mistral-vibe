from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "@notes.md summarize this file"
_FILE = "notes.md"
_CONTENT = "hello world"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file(_FILE, _CONTENT, num_lines=1),
    assistant_msg("The file contains a single line: 'hello world'."),
    turn_completed(),
]
