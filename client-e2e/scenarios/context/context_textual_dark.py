"""`/context` under the default truecolor theme: category hues, the hatched buffer and the threshold marker."""

from __future__ import annotations

from e2e.app_server.context import handshake as context_handshake, stats_updated
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "textual-dark"}
handshake = {
    **context_handshake(),
    "config/read": {"config": {"theme": "textual-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "textual-dark"}}},
}
request_methods = {"session/context/read"}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    "/context\r",
]

screen_contains = ("▼ auto-compact at 75%", "Compaction buffer")
