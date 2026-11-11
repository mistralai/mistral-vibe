from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from vibe.core.config._defaults import PUBLIC_REGIONAL_MISTRAL_API_HOSTS
from vibe.core.config.layers.default import DefaultConfigLayer

if TYPE_CHECKING:
    from vibe.core.config.vibe_schema import VibeConfigSchema


def resolve_vibe_base_url(config: VibeConfigSchema) -> str:
    # Where sign-in cannot resolve the deployment's chat URL, users set only
    # api_base by hand: the default would fetch the org config elsewhere.
    if config.origin_of("vibe_base_url") not in {None, DefaultConfigLayer.NAME}:
        return config.vibe_base_url
    provider = config.get_mistral_provider()
    if provider is None:
        return config.vibe_base_url
    return vibe_base_url_from_api_base(provider.api_base) or config.vibe_base_url


def vibe_base_url_from_api_base(api_base: str) -> str | None:
    # Only the dedicated-deployment shape (api.X / chat.X) is trusted: the
    # result receives the user's API key, and self-hosted subdomains vary.
    # Mistral's public regional API hosts have no chat.X counterpart.
    try:
        parts = urlsplit(api_base.strip())
    except ValueError:
        return None
    netloc = parts.netloc.lower().removesuffix(":443")
    if parts.scheme != "https" or "@" in netloc or not netloc.startswith("api."):
        return None
    if netloc in PUBLIC_REGIONAL_MISTRAL_API_HOSTS:
        return None
    tenant = netloc.removeprefix("api.")
    return f"https://chat.{tenant}" if tenant else None
