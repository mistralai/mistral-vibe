"""Model catalog fetching, caching, and fallback.

Held and refreshed once per process by ``ModelCatalogService``: the catalog is
configuration-like data, not picker data. Everything here is fail-open: a
missing credential, an unreachable endpoint or an expired cache all degrade
to local config without surfacing an error.
"""

from __future__ import annotations

from vibe.core.model_catalog.cache import (
    load_cached_model_catalog,
    store_cached_model_catalog,
)
from vibe.core.model_catalog.models import (
    ModelCatalogCapabilities,
    ModelCatalogEntry,
    ModelCatalogParameters,
    ModelCatalogResponse,
    parse_model_catalog_response,
)
from vibe.core.model_catalog.wait import ModelCatalogWait, model_catalog_wait_needed

__all__ = [
    "ModelCatalogCapabilities",
    "ModelCatalogEntry",
    "ModelCatalogParameters",
    "ModelCatalogResponse",
    "ModelCatalogWait",
    "load_cached_model_catalog",
    "model_catalog_wait_needed",
    "parse_model_catalog_response",
    "store_cached_model_catalog",
]
