"""A turn whose final answer follows intermediate messages and tool calls."""

from __future__ import annotations

from e2e.app_server.events import (
    AppServerEvent,
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

PROMPT = "fix the flaky auth test"

HANDSHAKE = {"runtime/read": {"runtime": {"config": {"collapseTurnDetails": True}}}}


def at(event: AppServerEvent, created_ms: int) -> AppServerEvent:
    """Stamp the event's entry with its creation time, in epoch milliseconds."""
    event["params"]["entry"]["createdAt"] = created_ms
    return event


def worked_turn(prompt: str = PROMPT) -> Timeline:
    """Two intermediate messages and three calls, then the final answer 72.5 s after the prompt."""
    return [
        f"{prompt}\r",
        turn_started(),
        at(user_msg(prompt), 1_787_593_260_000),
        assistant_msg("Let me look at the test first."),
        bash("cat test_auth.py", "def test_login(): ..."),
        bash("pytest test_auth.py", "1 failed"),
        assistant_msg("The token expires during the retry. Patching it."),
        bash("pytest test_auth.py", "1 passed"),
        at(
            assistant_msg("Fixed: the token now refreshes before the retry."),
            1_787_593_332_500,
        ),
        turn_completed(),
    ]


STEP = "\n".join([
    "Some notes first.",
    "",
    "| Feature | Behavior |",
    "| --- | --- |",
    "| Cells | Stay selectable |",
    "",
    "```",
    "fn main() {",
    "    let value = 1;",
    "}",
    "```",
])


def markdown_turn() -> Timeline:
    """A turn whose intermediate message paints text, a table and a code block."""
    return [
        f"{PROMPT}\r",
        turn_started(),
        user_msg(PROMPT),
        assistant_msg(STEP),
        bash("pytest test_auth.py", "1 passed"),
        assistant_msg("Fixed."),
        turn_completed(),
    ]
