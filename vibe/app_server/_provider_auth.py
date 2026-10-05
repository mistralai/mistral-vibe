"""Builds the redacted provider snapshot behind ``providerAuth/read``.

A read-only view for ``/status``: it never sends a provider request and never
lets credential material into the view. The API base is untrusted config text,
so the credential the provider would send is redacted from it before display.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import os
import re
import unicodedata
from urllib.parse import urlsplit

from vibe.app_server.provider_auth import ProviderAuthView
from vibe.core.config import VibeConfigSchema
from vibe.core.config.models import ModelConfig, ProviderConfig
from vibe.utils.url import display_url

__all__ = ["build_provider_auth_view", "read_provider_auth", "sanitize_api_base"]

_REDACTED = "[redacted]"


def _has_control_characters(value: str) -> bool:
    # Control characters (C0, DEL, and the C1 range) can move a cursor or
    # clear a screen; they have no business in a URL the user reads.
    return any(unicodedata.category(char) == "Cc" for char in value)


def _secret_pattern(secret: str) -> str:
    """A pattern matching ``secret`` raw or in any percent-encoded spelling.

    A pasted base can encode the credential with any hex case and can
    percent-encode characters ``quote`` leaves bare (RFC 3986 unreserved), so
    each character matches its raw form or its UTF-8 percent encoding. A space
    also matches ``+``, the form-style spelling a pasted value can carry.
    """
    parts = []
    for char in secret:
        encoded = "".join(f"%{byte:02x}" for byte in char.encode())
        alternatives = [re.escape(char), encoded]
        if char == " ":
            alternatives.append(r"\+")
        parts.append(f"(?:{'|'.join(alternatives)})")
    return "".join(parts)


def _redact_secrets(value: str, secrets: Collection[str]) -> str:
    # Longest first: a secret that contains another secret as a substring must
    # redact whole, or the shorter one would split its occurrences and leave
    # the remainder on screen.
    for secret in sorted(secrets, key=len, reverse=True):
        if not secret:
            continue
        # Case-insensitive so a lookalike credential redacts too — the safe
        # direction for a display path.
        value = re.sub(_secret_pattern(secret), _REDACTED, value, flags=re.IGNORECASE)
    return value


def sanitize_api_base(api_base: str, secrets: Collection[str]) -> str | None:
    """The displayable API base, or ``None`` when it is not displayable.

    Keeps the HTTP or HTTPS scheme, host, optional port, and path. Usernames,
    passwords, query parameters, and fragments are dropped, and any known
    credential value is redacted in raw or URL-encoded form.
    """
    try:
        parsed = urlsplit(api_base)
        destination = display_url(parsed)
    except ValueError:
        # ``display_url`` re-reads the port, so an invalid one lands here.
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    destination = _redact_secrets(destination, secrets)
    if _has_control_characters(destination):
        return None
    return destination


def _active_provider(config: VibeConfigSchema) -> tuple[ModelConfig, ProviderConfig]:
    model = config.get_active_model()
    return model, config.get_provider_for_model(model)


def build_provider_auth_view(
    config: VibeConfigSchema, *, environ: Mapping[str, str]
) -> ProviderAuthView:
    """Project the active model's provider into the redacted public view."""
    model, provider = _active_provider(config)
    # The credential the provider would send when it comes from the
    # environment; a key stored only in the keyring is not collected here —
    # reading it can block, and bounded source inspection arrives later.
    secrets: list[str] = []
    if provider.api_key_env_var and (value := environ.get(provider.api_key_env_var)):
        secrets.append(value)
    return ProviderAuthView(
        model_display_name=model.display_name or model.alias,
        provider_name=provider.name,
        api_base=sanitize_api_base(provider.api_base, secrets),
    )


async def read_provider_auth(config: VibeConfigSchema) -> ProviderAuthView:
    """The ``/status`` snapshot: no network, no credential material."""
    return build_provider_auth_view(config, environ=os.environ)
