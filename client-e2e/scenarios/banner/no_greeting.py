"""A named identity gets no greeting, even from a server that still sends `showGreeting`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

PLAN = "Team"

handshake = {
    "identity/read": {
        "identity": {
            "id": "user-1",
            "email": "ada@example.com",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "workspace": {"id": "ws-1", "name": "Analytical Engine"},
            "organization": {"id": "org-1", "name": "Mistral"},
        }
    },
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "chat", "name": "TEAM", "title": PLAN},
            "planOffer": None,
            "rateLimitAction": None,
        }
    },
    "config/read": {"config": {"showGreeting": True}},
}

timeline: Timeline = []

screen_contains = (PLAN,)
screen_excludes = ("Hello Ada", "how can I help you")
