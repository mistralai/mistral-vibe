"""An oversized Unified result keeps its semantic header and counts under its kind."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    oversized_read_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "summarize the readme"

screen_contains = {
    "rust": (
        "⏷ Read 1 file, ran 1 command",
        "⏷ Read README.md (truncated)",
        "<truncated-output>",
    )
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    oversized_read_file("README.md"),
    bash("wc -l README.md", "1200 README.md"),
    assistant_msg("Done."),
    turn_completed(),
    # Unfolding shows the receipt the Harness kept as the read row body.
    "\x0f",
]
