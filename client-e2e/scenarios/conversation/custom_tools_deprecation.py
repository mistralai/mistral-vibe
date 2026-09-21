"""A custom tool in the runtime shows the once-per-session deprecation banner."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The fixture's runtime tools plus one custom tool; deep-merge replaces the
# list wholesale, so the built-ins must be repeated.
handshake = {
    "runtime/read": {
        "runtime": {
            "tools": [
                {"name": "skill", "isCustom": False},
                {"name": "task", "isCustom": False},
                {"name": "web_fetch", "isCustom": False},
                {"name": "bash", "isCustom": False},
                {"name": "edit", "isCustom": False},
                {"name": "grep", "isCustom": False},
                {"name": "ask_user_question", "isCustom": False},
                {"name": "read_file", "isCustom": False},
                {"name": "web_search", "isCustom": False},
                {"name": "todo", "isCustom": False},
                {"name": "write_file", "isCustom": False},
                {"name": "legacy_lint", "isCustom": True},
            ]
        }
    }
}

# The banner mounts after the initial history; the capture after typing settles it.
capture_startup = False
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    "x",
]
