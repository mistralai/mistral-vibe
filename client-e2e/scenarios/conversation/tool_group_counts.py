"""Group headers count each kind, a lone call stays ungrouped, borders align with arrows."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    loaded_skill,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
    web_search_completed,
    web_search_started,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "inspect the project"
_LONG_COMMAND = "echo three && " + " && ".join(
    f"test -f src/module_{i:02d}.py" for i in range(8)
)

screen_contains = {
    "rust": ("⏷ Loaded 1 skill, read 2 files, ran 3 …", "⏷ Ran git status")
}

# Round one folds into a counted group; round two's lone call stays ungrouped.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    loaded_skill("lint", "Run the linter."),
    read_file("notes.txt", "   1→a note", num_lines=1),
    bash("echo one", "one"),
    read_file("todo.txt", "   1→a task", num_lines=1),
    bash("echo two", "two"),
    bash(_LONG_COMMAND, "three"),
    # Kinds sharing a verb merge into one segment: "ran 3 commands and 1 web search".
    web_search_started("ruff config"),
    web_search_completed("ruff config", "Use pyproject.toml."),
    assistant_msg("Checking one more thing."),
    bash("git status", "clean"),
    assistant_msg("Done."),
    turn_completed(),
    # Each border sits under its arrow and climbs through the wrapped header.
    "\x0f",
    # A narrow terminal ellipsizes the group header to one row.
    {"resize": (40, 40)},
]
