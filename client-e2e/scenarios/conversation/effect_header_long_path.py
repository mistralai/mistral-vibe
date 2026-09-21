from __future__ import annotations

from e2e.app_server.events import turn_completed, turn_started, user_msg, write_file
from e2e.app_server.scenario import Timeline

_PROMPT = "write it"

# Longer than the header's available width, with no break opportunity: the row
# must be cut at the cell boundary, not pushed whole onto the next line.
_PATH = (
    "/private/var/folders/kk/qqyqm2p9057f1qz305zlz3qr0000gn/T/"
    "vibe-scratchpad-ff5f5f95-nl1b7xuo/deeply/nested/dir/test_sample.py"
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    write_file(_PATH, "x = 1\n"),
    turn_completed(),
]
