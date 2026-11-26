"""A terminal too narrow for the info block beside the cat stacks it under the cat."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "chat", "name": "TEAM", "title": "Team"},
            "planOffer": None,
            "rateLimitAction": None,
        }
    }
}
screen_contains = ("mistral-medium-3.5[medium] · Team",)

timeline: Timeline = [resize(24, 50), take_snapshot()]
