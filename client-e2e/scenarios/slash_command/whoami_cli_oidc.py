"""`/whoami` names the CLI OIDC session as the credential source when it replaces the API key."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/whoami\r"]

request_methods = {"providerAuth/read"}
screen_contains = ("Credential source (MISTRAL_API_KEY): CLI OIDC session",)

handshake = {
    "providerAuth/read": {
        "auth": {
            "modelDisplayName": "mistral-medium-3.5",
            "providerName": "mistral",
            "apiBase": "https://api.mistral.ai/v1",
            "authKind": "api_key",
            "apiKeyEnvVar": "MISTRAL_API_KEY",
            "envFilePath": "/home/e2e-user/.vibe/.env",
            "sources": [{"kind": "cli_oidc", "state": "in_use"}],
        }
    }
}
