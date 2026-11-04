"""`/context` opens the context breakdown: gauge, per-category legend, free space and compaction buffer."""

from __future__ import annotations

from e2e.app_server.context import handshake, stats_updated
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = handshake()
request_methods = {"session/context/read"}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    "/context\r",
    {"resize": (30, 70)},
]

screen_contains = (
    "Context   21% (42.3k of 200k tokens)",
    "auto-compact at 75% ▼",
    "System prompt",
    "Tool definitions",
    "Skills",
    "Conversation",
    "Free space",
    "Compaction buffer",
    " 3% (5k tokens)",
    " 6% (12k tokens)",
    " 1% (2.5k tokens)",
    "11% (22.8k tokens)",
)
screen_excludes = ("usage appears after the first response",)
