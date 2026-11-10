"""The on-disk copy of each account's model catalog, shared by every Vibe
process: a restarted app server serves it without a request.

Written by ``ModelCatalogService`` after every successful fetch, an empty
catalog included, TTL-bounded and written atomically. Unlike the eval cache it
is not gated on telemetry flags: the catalog is config, not an experiment.
"""

from __future__ import annotations

from typing import Any

from vibe.core.model_catalog._constants import MODEL_CATALOG_CACHE_TTL_SECONDS
from vibe.core.model_catalog.models import (
    ModelCatalogResponse,
    parse_model_catalog_response,
)
from vibe.core.paths import MODEL_CATALOG_CACHE_FILE
from vibe.core.utils.keyed_json_cache import load_keyed_record, store_keyed_entry
from vibe.observability.logging import logger


def load_cached_model_catalog(key: str) -> tuple[ModelCatalogResponse, int] | None:
    return load_keyed_record(
        MODEL_CATALOG_CACHE_FILE.path,
        key,
        ttl_seconds=MODEL_CATALOG_CACHE_TTL_SECONDS,
        parse=_parse_catalog_payload,
    )


def store_cached_model_catalog(
    key: str, response: ModelCatalogResponse, *, stored_at: int | None = None
) -> None:
    store_keyed_entry(
        MODEL_CATALOG_CACHE_FILE.path,
        key,
        response.model_dump(mode="json"),
        stored_at=stored_at,
    )


def _parse_catalog_payload(payload: Any) -> ModelCatalogResponse | None:
    catalog = parse_model_catalog_response(payload)
    if catalog is None:
        logger.warning("Ignoring corrupt cached model catalog payload")
    return catalog
