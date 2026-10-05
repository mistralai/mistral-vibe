"""Consecutive call rounds separated by widget-less entries fold into one block."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    reasoning,
    session_title_updated,
    tool_approval_added,
    tool_approval_answered,
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

# Call rounds arrive back to back, separated only by entries that paint no
# transcript row (an answered approval and a session-title notice). All calls
# collapse into the same folded block instead of one header per round.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    reasoning("Firing off a first round of harmless calls."),
    bash("echo one", "one"),
    tool_approval_added("bash", "shell", {"command": "echo two"}),
    tool_approval_answered(),
    bash("echo two", "two"),
    session_title_updated("harmless commands"),
    bash("uname -a", "Darwin 25.5.0 arm64"),
    assistant_msg("Done."),
    turn_completed(),
]

screen_contains = {"rust": ("Ran commands, thought",)}
