"""Two server warnings in one turn stack as separate toasts (VIBE-4622)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    server_warning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# Python renders warnings through Textual `App.notify`, whose toasts mount on a
# later frame and are not deterministically captured; Rust draws them inline.
skip_terminal_parity = (
    "python's Textual notify toast mounts async; rust draws it inline"
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    server_warning("first warning"),
    server_warning("second warning"),
]

screen_contains = {"rust": ("first warning", "second warning")}
