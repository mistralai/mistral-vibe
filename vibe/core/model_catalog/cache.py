"""Per-user on-disk cache of the last successful model catalog fetch.

Follows the experiment-eval cache pattern: keyed by the hashed Mistral API
key, applied optimistically at session start, overwritten by every successful
fetch, TTL-bounded, written atomically. Unlike the eval cache it is not gated
on telemetry flags — the catalog is config, not an experiment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibe.core.model_catalog._constants import MODEL_CATALOG_CACHE_TTL_SECONDS
from vibe.core.model_catalog.models import (
    ModelCatalogResponse,
    parse_model_catalog_response,
)
from vibe.core.paths import MODEL_CATALOG_CACHE_FILE
from vibe.core.utils.keyed_json_cache import load_keyed_entry, store_keyed_entry
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


def load_cached_model_catalog(config: VibeConfigSchema) -> ModelCatalogResponse | None:
    """Return the cached catalog for this user's credential, if fresh enough."""
    key = _cache_key(config)
    if key is None:
        return None
    return load_keyed_entry(
        MODEL_CATALOG_CACHE_FILE.path,
        key,
        ttl_seconds=MODEL_CATALOG_CACHE_TTL_SECONDS,
        parse=_parse_catalog_payload,
    )


def store_cached_model_catalog(
    config: VibeConfigSchema, response: ModelCatalogResponse
) -> None:
    """Overwrite the cached catalog for this user's credential.

    Called on every successful fetch, including an empty catalog: a successful
    response is authoritative.
    """
    key = _cache_key(config)
    if key is None:
        return
    store_keyed_entry(
        MODEL_CATALOG_CACHE_FILE.path, key, response.model_dump(mode="json")
    )


def _parse_catalog_payload(payload: Any) -> ModelCatalogResponse | None:
    catalog = parse_model_catalog_response(payload)
    if catalog is None:
        logger.warning("Ignoring corrupt cached model catalog payload")
    return catalog


def _cache_key(config: VibeConfigSchema) -> str | None:
    from vibe.core.telemetry.send import mistral_credential_cache_key

    return mistral_credential_cache_key(config)
