"""The CLI OIDC flag and its API-key marker, kept free of heavy imports.

Existing clients require a nonempty API key before they can make a request.
In OIDC mode they receive this non-secret marker, and the HTTP boundary
replaces it with the current ID token and tenant headers.
"""

from __future__ import annotations

import os

OIDC_API_KEY = "vibe-cli-oidc"
# Cache keys derived from an OIDC identity, never from the marker itself.
OIDC_CACHE_KEY_PREFIX = "cli-oidc:v1:"
_OIDC_AUTHORIZATION = f"Bearer {OIDC_API_KEY}"


def oidc_enabled() -> bool:
    return os.environ.get("VIBE_CLI_OIDC_ENABLED", "").lower() in {"1", "true"}


def is_oidc_marker(api_key: str | None) -> bool:
    return api_key == OIDC_API_KEY


def is_oidc_authorization(header: str | None) -> bool:
    return header == _OIDC_AUTHORIZATION
