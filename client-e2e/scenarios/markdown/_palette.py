"""Shared markdown answer that exercises every color role, rendered per theme."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show the markdown palette"
_ANSWER = "\n".join([
    "# Heading one",
    "## Heading two [with a link](https://example.com/h2)",
    "### Heading three",
    "#### Heading four",
    "",
    "Body with **bold**, *italic*, `inline code` and a [link](https://example.com).",
    "",
    "> A quoted remark.",
    ">",
    "> > A nested remark.",
    ">",
    "> Back in the outer quote.",
    "",
    "- a bullet",
    "",
    "| Name | Value |",
    "| ---- | ----- |",
    "| key  | `42`  |",
    "",
    "```",
    "plain fenced code",
    "```",
    "",
    "---",
])


def theme_handshake(theme: str) -> dict[str, object]:
    """Config and runtime responses that select `theme`."""
    return {
        "config/read": {"config": {"theme": theme}},
        "runtime/read": {"runtime": {"config": {"theme": theme}}},
    }


timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
