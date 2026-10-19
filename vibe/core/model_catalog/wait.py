"""Holding a session's start for a catalog fetch.

With no catalog loaded, a session whose model is unpinned or missing may be
about to get it from the catalog: its readiness and first turn wait for the
fetch, for at most ``MODEL_CATALOG_WAIT_CAP_SECONDS`` from when the session
starts waiting on it. Once a fetch fails, or is still running when a cap
expires, no later session or ``/clear`` in the process waits. A wait the
session has dropped does not count: the next one held on that fetch keeps its
own cap.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibe.core.config.layers.model_catalog import ModelCatalogLayer
from vibe.core.model_catalog._constants import MODEL_CATALOG_WAIT_CAP_SECONDS
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator

_failed = False


def model_catalog_failed() -> bool:
    return _failed


def mark_model_catalog_failed() -> None:
    global _failed
    _failed = True


def reset_model_catalog_failed() -> None:
    """Forget a failed fetch; for test isolation."""
    global _failed
    _failed = False


def model_catalog_wait_needed(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
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
    if config.active_model or model_catalog_failed():
        return False
    # Imported here, as in the fetch: it keeps the telemetry stack off this
    # module's import path.
    from vibe.core.telemetry.send import get_mistral_provider_and_api_key

    if get_mistral_provider_and_api_key(config) is None:
        return False
    try:
        layer = orchestrator.get_layer(ModelCatalogLayer.NAME)
    except KeyError:
        # A config stack without the catalog layer (test doubles) can never
        # receive a catalog.
        return False
    return isinstance(layer, ModelCatalogLayer) and layer.catalog is None


class ModelCatalogWait:
    """A session waiting on a fetch: done once the fetch's task ends or the
    cap expires. The cap counts from creation; create it in the running loop.
    """

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task
        self._done = asyncio.Event()
        self._discarded = False
        # Armed whether or not anything waits, so a fetch still running at
        # the cap is recorded even when every client skipped the wait.
        self._cap = asyncio.get_running_loop().call_later(
            MODEL_CATALOG_WAIT_CAP_SECONDS, self._expire
        )
        task.add_done_callback(self._settle)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    async def wait(self) -> None:
        await self._done.wait()

    def discard(self) -> None:
        """Drop this wait while its fetch may still be running.

        Resume and ``/clear`` replace the wait and hold a new one on the
        same fetch. The cap armed here must not fail that later wait, and
        anyone already awaiting this one is released instead of hanging
        after the cap is disarmed.
        """
        self._discarded = True
        self._cap.cancel()
        self._done.set()

    def _settle(self, _task: asyncio.Task[Any]) -> None:
        self._cap.cancel()
        self._done.set()

    def _expire(self) -> None:
        if self._task.done():
            return
        # Discard races the timer: a callback already queued must not fail
        # the process after the session has moved on to another wait.
        if self._discarded:
            self._done.set()
            return
        if not model_catalog_failed():
            logger.info(
                "Model catalog not applied within %ss; continuing on local config",
                MODEL_CATALOG_WAIT_CAP_SECONDS,
            )
        if self._task.done():
            # The fetch landed between the first check and the mark; a
            # success must not become a process-wide failure.
            return
        mark_model_catalog_failed()
        self._done.set()
