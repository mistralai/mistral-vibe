"""Text typed before the TUI starts, ending with Enter (a cooked-mode line feed), submits once ready."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance

typeahead = "fix the\rtests\r"

request_methods = ("workspace/prompt/prepare",)
screen_contains = ("> fix the", "tests", "On it.")

timeline: Timeline = [
    advance(100),
    turn_started(),
    user_msg("fix the\ntests"),
    assistant_msg("On it."),
    turn_completed(),
]
