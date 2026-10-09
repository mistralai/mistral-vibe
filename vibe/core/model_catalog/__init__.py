"""Model catalog fetching, caching, and fallback.

Fetched at session start rather than on picker open: the catalog is
configuration-like data, not picker data. Everything here is fail-open — a
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
from vibe.core.model_catalog.session import (
    fetch_and_cache_model_catalog,
    refresh_model_catalog,
)

__all__ = [
    "ModelCatalogCapabilities",
    "ModelCatalogEntry",
    "ModelCatalogParameters",
    "ModelCatalogResponse",
    "fetch_and_cache_model_catalog",
    "load_cached_model_catalog",
    "parse_model_catalog_response",
    "refresh_model_catalog",
    "store_cached_model_catalog",
]
