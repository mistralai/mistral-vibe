"""Shared fenced Python and Rust answer that exercises every syntax token, rendered per theme."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show the code palette"
_ANSWER = "\n".join([
    "```python",
    "import os",
    "",
    "@cache",
    "class Greeter:",
    '    """Say hello."""',
    "",
    "    def greet(self, name: str, times: int = 2) -> str:",
    "        # repeat the greeting",
    '        return f"hi {name}" * times if name else None',
    "```",
    "",
    "```rust",
    "fn total(values: &[u32]) -> u32 {",
    '    let label = "sum";',
    "    values.iter().sum::<u32>() + 1",
    "}",
    "```",
    "",
    "```",
    "no language here",
    "```",
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
