from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/whoami\r"]

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
            "plan": {"kind": "chat", "name": "TEAM", "title": "[Subscription] Pro"},
            "planOffer": None,
            "rateLimitAction": None,
            "teleportEligible": True,
            "teleportAction": None,
        }
    },
}
