"""`/whoami` ends with the switch offer for a key that bypasses the user's Vibe plan."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/whoami\r"]

request_methods = {"providerAuth/read"}
screen_contains = (
    "Plan: Free",
    "Key scope: workspace",
    "You have Vibe Pro, but this API key does not use it.",
    "quit and run vibe --setup, then choose Launch browser and sign in",
)

handshake = {
    "identity/read": {
        "identity": {"id": "user-1", "email": "ada@example.com", "firstName": "Ada"}
    },
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "api", "name": "FREE", "title": "Free"},
            "planOffer": {
                "kind": "switch_api_key",
                "url": "https://chat.mistral.ai/code/extensions?focus=key",
            },
            "rateLimitAction": None,
            "apiKey": {
                "preview": "****wxyz",
                "scope": "workspace",
                "accessScope": None,
            },
        }
    },
}
