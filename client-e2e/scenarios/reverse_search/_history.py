"""Shared prompts and handshake for the reverse history search scenarios."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

VOICE_OFF = {
    "config/read": {"config": {"voiceModeEnabled": False}},
    "runtime/read": {"runtime": {"config": {"voiceModeEnabled": False}}},
}


def sent(*prompts: str) -> Timeline:
    """Submit each prompt and finish its turn, so it lands in the history."""
    timeline: Timeline = []
    for prompt in prompts:
        timeline += [
            f"{prompt}\r",
            turn_started(),
            user_msg(prompt),
            assistant_msg("ok"),
            turn_completed(),
        ]
    return timeline
