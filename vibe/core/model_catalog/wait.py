"""Holding a session's start for a catalog fetch.

With no catalog loaded, a session whose model is unpinned or missing may be
about to get it from the catalog: its readiness and first turn wait for the
fetch, for at most ``MODEL_CATALOG_WAIT_CAP_SECONDS`` from when the session
starts waiting on it. Once a fetch fails, or is still running when a cap
expires, no later session or ``/clear`` in the process waits: the process's
``ModelCatalogService`` holds that memory. A fetch cancelled with its session is
not a failure: the next session still waits for its own fetch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibe.core.config.layers.model_catalog import ModelCatalogLayer
from vibe.core.model_catalog._constants import MODEL_CATALOG_WAIT_CAP_SECONDS
from vibe.core.utils.fetch_wait import FetchFailures, FetchWait

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator
    from vibe.core.platform_auth import PlatformCredentialFactory


def model_catalog_wait_needed(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    failures: FetchFailures,
    credentials: PlatformCredentialFactory | None = None,
) -> bool:
    """Whether a session opening on *orchestrator* must wait for its catalog
    fetch.

    ``active_model`` is empty both when nothing is pinned and when the pin
    names a model no layer defines (the fallback normalizes it), and both may
    resolve to a catalog model once the fetch lands. Without a Mistral
    credential the fetch is skipped, so there is nothing to wait for.
    """
    config = orchestrator.config
    if not config.experimental_enable_model_catalog:
        return False
    if config.active_model or failures.failed:
        return False
    # Imported here, as in the fetch: it keeps the HTTP stack off this
    # module's import path.
    from vibe.core.platform_auth._mistral import mistral_credentials

    if mistral_credentials(config, credentials) is None:
        return False
    try:
        layer = orchestrator.get_layer(ModelCatalogLayer.NAME)
    except KeyError:
        # A config stack without the catalog layer (test doubles) can never
        # receive a catalog.
        return False
    return isinstance(layer, ModelCatalogLayer) and layer.catalog is None


class ModelCatalogWait(FetchWait):
    def __init__(self, task: asyncio.Task[Any], failures: FetchFailures) -> None:
        super().__init__(
            task,
            name="Model catalog",
            cap_seconds=MODEL_CATALOG_WAIT_CAP_SECONDS,
            failures=failures,
        )
