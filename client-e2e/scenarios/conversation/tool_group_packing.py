from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    read_file,
    reasoning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

_PROMPT = "run a few harmless commands"

# Reasoning and every effect of the run fold into one ToolGroup: the settled
# frame shows a single collapsed header (triangle + "Ran commands, read files,
# thought") instead of individual call and result rows.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    reasoning("Firing off a batch of harmless read-only calls."),
    bash("echo one", "one"),
    bash("echo two", "two"),
    read_file("notes.txt", "   1→a note", num_lines=1),
    bash("uname -a", "Darwin 25.5.0 arm64"),
    assistant_msg("Done."),
    turn_completed(),
]
