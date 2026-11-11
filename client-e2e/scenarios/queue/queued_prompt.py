"""A prompt submitted during a turn is queued under the `Queued` header."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

# One batch only: the second enqueue has nothing left to release, so its prompt
# stays queued instead of starting a turn. The four released events are the
# three below plus the queue update the harness adds after `turn/started`.

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    take_snapshot(),
]
