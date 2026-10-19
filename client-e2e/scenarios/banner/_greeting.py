"""Shared greeting fixtures: an identity and a planned account the post-ready reads return."""

from __future__ import annotations

from typing import Any

PLAN = "[Subscription] Pro"


def identity_handshake(first_name: str) -> dict[str, Any]:
    """Answer the post-ready reads with `first_name` and a plan, so the banner shows they landed."""
    return {
        "identity/read": {
            "identity": {
                "id": "user-1",
                "email": "ada@example.com",
                "firstName": first_name,
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
                "teleportEligible": True,
                "teleportAction": None,
            }
        },
    }
