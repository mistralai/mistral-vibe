"""`--setup` with a litellm active model offers both the Mistral setup and the litellm-anthropic key."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
exit_after_last_step = True
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
    }
}
request_methods = {"setup/status", "setup/store-credential", "setup/submit-choices"}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> provider choice
    "\r",  # Mistral AI -> the browser / API key choice
    "\x1b",  # back to the provider choice
    "\x1b[B\r",  # Set up litellm-anthropic -> its API key screen
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the litellm-anthropic key lands, then exit
]
