from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.model_catalog.cache import store_cached_model_catalog
from vibe.core.model_catalog.client import ModelCatalogClient
from vibe.core.model_catalog.models import ModelCatalogResponse
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


async def refresh_model_catalog(
    config: VibeConfigSchema,
) -> ModelCatalogResponse | None:
    """Fetch the catalog once, gated on the experimental flag and a Mistral
    credential — never on the active model's provider.

    Returns None when the feature is disabled, no Mistral credential is held,
    or the fetch failed; the caller then keeps whatever it already has.
    """
    if not config.experimental_enable_model_catalog:
        logger.debug("Model catalog fetch skipped: experimental flag off")
        return None
    from vibe.core.telemetry.send import get_mistral_provider_and_api_key

    provider_and_key = get_mistral_provider_and_api_key(config)
    if provider_and_key is None:
        logger.debug("Model catalog fetch skipped: no Mistral credential")
        return None
    provider, api_key = provider_and_key
    client = ModelCatalogClient.from_provider(provider)
    try:
        return await client.fetch(api_key)
    finally:
        await client.aclose()


async def fetch_and_cache_model_catalog(
    config: VibeConfigSchema,
) -> ModelCatalogResponse | None:
    """Fetch the catalog and persist it; the single session-start step both
    harnesses run. Fail-open: never raises, returns None on skip or failure.

    A successful response — including an empty one — is authoritative and
    overwrites the on-disk cache.
    """
    try:
        response = await refresh_model_catalog(config)
    except Exception:
        logger.exception("Model catalog fetch failed unexpectedly")
        return None
    if response is None:
        return None
    logger.info(
        "Model catalog fetched: %d models, recommended=%s",
        len(response.models),
        next((entry.id for entry in response.models if entry.recommended), "none"),
    )
    try:
        store_cached_model_catalog(config, response)
    except Exception:
        logger.exception("Failed to cache the model catalog")
    return response
