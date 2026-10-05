"""The rewind highlight pads every wrapped row of a prompt to the edge, wide glyphs included."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = " ".join(["word"] * 20) + " " + "界" * 60 + " ❤️ end"

screen_contains = {"rust": ("\n  " + "界" * 59, "\n  界 ❤")}
screen_excludes = {"rust": ("\n界", "failed")}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg("Noted."),
    turn_completed(),
    "/rewind\r",
]

handshake = {"session/rewind/read": {"hasFileChanges": False, "paths": []}}
