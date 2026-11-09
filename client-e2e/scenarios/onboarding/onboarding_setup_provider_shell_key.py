"""`--setup`'s provider choice warns with the chosen provider's shell key, never the seeded one's."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
_MISTRAL = {
    "name": "mistral",
    "apiBase": "https://api.mistral.ai/v1",
    "apiKeyEnvVar": "MISTRAL_API_KEY",
    "browserAuthBaseUrl": "https://console.mistral.ai",
    "browserAuthApiBaseUrl": "https://console.mistral.ai/api",
    "browserAuthAllowOriginRewrite": False,
}
handshake = {
    "setup/status": {
        "provider": {
            "name": "litellm-anthropic",
            "apiBase": "http://localhost:4000/v1",
            "apiKeyEnvVar": "LITELLM_API_KEY",
            "browserAuthBaseUrl": None,
            "browserAuthApiBaseUrl": None,
            "browserAuthAllowOriginRewrite": False,
        },
        "consoleBaseUrl": "https://console.mistral.ai",
        "vibeBaseUrl": "https://chat.mistral.ai",
        "activeModel": "litellm-gpt",
        "theme": "ansi-dark",
        "supportsBrowserSignIn": False,
        "hasApiKey": False,
        "mistralProvider": _MISTRAL,
        # The seeded provider's shell key, and the offered Mistral one's.
        "activeCredentialSource": "process_env",
        "mistralActiveCredentialSource": "process_env",
    }
}
request_methods = {"setup/status"}
screen_contains = ("LITELLM_API_KEY is set in your shell",)

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> provider choice
    "\r",  # Mistral AI -> auth method: the Mistral shell-key warning shows
    "\x1b",  # back to the provider choice
    "\x1b[B\r",  # Set up litellm-anthropic -> its API key screen, its own warning
]
