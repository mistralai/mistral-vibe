"""A terminal too narrow for the info block beside the cat stacks it under the cat."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "chat", "name": "TEAM", "title": "[Subscription] Pro"},
            "planOffer": None,
            "rateLimitAction": None,
            "teleportEligible": True,
            "teleportAction": None,
        }
    }
}
screen_contains = ("mistral-medium-3.5[medium] · [Subscription] Pro",)

timeline: Timeline = [resize(24, 50)]
