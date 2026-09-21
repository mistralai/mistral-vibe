"""A server-pushed warning surfaces as a toast (VIBE-4622)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    server_warning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# Python renders the warning through Textual `App.notify`, whose toast mounts on
# a later frame and is not deterministically captured; Rust draws it inline. Skip
# Py-vs-Rust parity but keep the Rust golden as a non-regression guard.
skip_terminal_parity = (
    "python's Textual notify toast mounts async; rust draws it inline"
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    server_warning("Skill 'demo' failed to load"),
]

screen_contains = {"rust": ("Skill 'demo' failed to load",)}
