"""A snapshot's `retrying` swaps the loading label to Retrying and back while the turn runs."""

from __future__ import annotations

import json
from typing import Any

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import (
    SESSION_ID,
    TURN_ID,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import AppServerEvent, Timeline

# The loading label only shows while a turn runs, so settle on busy frames.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# Each release must land on its own settled frame.
settle_per_key = True


def _snapshot(retrying: dict[str, Any] | None) -> AppServerEvent:
    """A live snapshot without history, so only its `retrying` field changes the UI."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    state = fixture["handshake"]["session/start"]["state"]
    state["history"] = None
    state["retrying"] = retrying
    return {
        "method": "session/snapshot",
        "params": {"sessionId": SESSION_ID, "state": state},
    }


_RETRYING = {"turnId": TURN_ID, "category": "rate_limited", "detail": "Rate limited"}

# The first release is the three events below plus the queue update the
# harness adds after `turn/started`; each later release is one event.
timeline: Timeline = [
    "retry me\r",
    turn_started(),
    user_msg("retry me"),
    _snapshot(_RETRYING),
    _snapshot(None),
    assistant_msg("Done after a retry."),
    turn_completed(),
    {"release": 4},
    {"release": 1},
    {"release": 2},
]
