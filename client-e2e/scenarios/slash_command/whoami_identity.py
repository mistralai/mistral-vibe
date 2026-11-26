from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/whoami\r"]

request_methods = {"providerAuth/read"}
screen_contains = (
    "Name: Ada Lovelace",
    "Email: ada@example.com",
    "Workspace: Analytical Engine",
    "Organization: Mistral",
    "Plan: Team",
    "API key: ****wxyz",
    "Access scope: shared_only",
)

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
            "plan": {"kind": "chat", "name": "TEAM", "title": "Team"},
            "planOffer": None,
            "rateLimitAction": None,
            "apiKey": {
                "preview": "****wxyz",
                "scope": "workspace",
                "accessScope": "shared_only",
            },
        }
    },
}
