"""Fetching the remote config the new-session picker finds no cache for.

A session start fetches the org config, the GrowthBook eval and the model
catalog itself. The picker reads models and modes outside a session
(``config/read``, ``agents/list``), which only apply what earlier fetches
cached, so with nothing cached for the current API key -- first launch, a key
change, an expired entry -- it would answer from local config alone. These
reads fetch what is missing, wait for it within each fetch's limit, and answer
from the caches the fetches wrote.

Once a fetch fails, or is still running at its limit, later reads stop waiting
for it until the process restarts. The org config, the GrowthBook eval and the
model catalog are the process's ``OrgConfigService``, ``ExperimentsService``
and ``ModelCatalogService``, shared with every session.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from vibe.app_server._cached_layers import apply_cached_layers
from vibe.app_server._experiments_service import (
    ExperimentsEvalFailedError,
    ExperimentsKey,
)
from vibe.app_server._launch_context import client_launch_context
from vibe.app_server._model_catalog_service import ModelCatalogKey
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.experiments import wait as experiments_wait
from vibe.core.experiments.cache import (
    load_cached_eval_response,
    store_cached_eval_response,
)
from vibe.core.experiments.wait import ExperimentsWait, experiments_eval_runs
from vibe.core.model_catalog import wait as model_catalog_wait
from vibe.core.model_catalog.wait import ModelCatalogWait
from vibe.core.telemetry.send import credential_cache_key_is_current
from vibe.core.utils.fetch_wait import FetchFailures, FetchWait
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.app_server._connection_protocol import ClientInfo
    from vibe.app_server._experiments_service import ExperimentsService
    from vibe.app_server._model_catalog_service import ModelCatalogService
    from vibe.app_server._org_config_service import OrgConfigKey, OrgConfigService
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.config.orchestrator import ConfigOrchestrator
    from vibe.core.platform_auth import PlatformCredentialFactory


class MissingLayerFetches:
    """The picker's config reads, and the fetches they share."""

    def __init__(
        self,
        org_config: OrgConfigService,
        experiments: ExperimentsService,
        model_catalog: ModelCatalogService,
        credentials: PlatformCredentialFactory | None = None,
    ) -> None:
        self._org_config = org_config
        self._experiments = experiments
        self._model_catalog = model_catalog
        self._credentials = credentials
        # Holds each task so it is not collected mid-flight, and lets a
        # concurrent read for the same credential wait on it, not refetch.
        self._tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}

    async def load(
        self, harness_files: HarnessFilesManager, client_info: ClientInfo
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        """The config a session started now would be built from, fetching
        what nothing is cached for.
        """
        orchestrator = await build_default_orchestrator(harness_files=harness_files)
        org_key, _, _ = await apply_cached_layers(
            orchestrator,
            self._org_config,
            self._credentials,
            model_catalog=self._model_catalog,
        )
        for _ in range(2):
            catalog_was_on = orchestrator.config.experimental_enable_model_catalog
            if not await self._fetch_missing(
                orchestrator.config, org_key, harness_files, client_info
            ):
                break
            # Rebuilt rather than re-applied: the org config's cache key is
            # read from the config before the org config applies.
            orchestrator = await build_default_orchestrator(harness_files=harness_files)
            org_key, _, _ = await apply_cached_layers(
                orchestrator,
                self._org_config,
                self._credentials,
                model_catalog=self._model_catalog,
            )
            # The eval can turn the catalog on: one more pass fetches it. What
            # the first pass cached is not fetched again.
            if (
                catalog_was_on
                or not orchestrator.config.experimental_enable_model_catalog
            ):
                break
        return orchestrator

    async def _fetch_missing(
        self,
        config: VibeConfigSchema,
        org_key: OrgConfigKey | None,
        harness_files: HarnessFilesManager,
        client_info: ClientInfo,
    ) -> bool:
        """Fetch what *config* has no cache for, and wait for it. Returns
        whether anything was fetched.
        """
        if org_key is None:
            # No Mistral credential: none of the three can be fetched.
            return False
        cache_key = org_key.cache_key
        org = None
        if (
            not self._org_config.failed_recently(org_key)
            and await self._org_config.load(org_key) is None
        ):
            org = self._task(
                "org_config",
                cache_key,
                lambda: _fetch_org_config(
                    harness_files, self._org_config, self._credentials
                ),
            )

        async def with_org_config() -> VibeConfigSchema:
            # Like a session start: GrowthBook and the catalog run on the org
            # config, which can turn telemetry or the experiments off. Shielded:
            # the org task is shared, and is not this caller's to cancel.
            return config if org is None else await asyncio.shield(org)

        launch_context = client_launch_context(client_info)

        credentials = self._credentials

        async def fetch_experiments() -> None:
            fetch_config = await with_org_config()
            # The org config applied was the fetching account's: a sign-in
            # since then makes it another account's, so skip this read.
            if not credential_cache_key_is_current(
                cache_key, fetch_config, credentials
            ):
                return
            if not _experiments_missing(
                fetch_config, self._experiments.failures, credentials
            ):
                return
            _limit_this_fetch(
                lambda task: ExperimentsWait(task, self._experiments.failures)
            )
            key = await ExperimentsKey.of(
                fetch_config, launch_context, credentials=credentials
            )
            if key is None:
                return
            try:
                held = await self._experiments.evaluate(key)
            except ExperimentsEvalFailedError:
                return
            # The read answers from the disk cache; an eval held from before
            # the cache was cleared is not written there by a fetch.
            store_cached_eval_response(
                fetch_config,
                held.response,
                fetched_for=key.credential,
                credentials=credentials,
            )

        async def fetch_model_catalog() -> None:
            fetch_config = await with_org_config()
            # The org config applied was the fetching account's: a sign-in
            # since then makes it another account's, so skip this read.
            if not credential_cache_key_is_current(
                cache_key, fetch_config, credentials
            ):
                return
            if not await self._model_catalog_missing(fetch_config):
                return
            _limit_this_fetch(
                lambda task: ModelCatalogWait(task, self._model_catalog.failures)
            )
            key = await ModelCatalogKey.of(fetch_config, credentials)
            if key is not None:
                await self._model_catalog.ensure(key)

        # Decided on the read's config, then again in the task on the org
        # config, which can turn either source on or off.
        fetches: list[tuple[asyncio.Task[None], float]] = []
        if org is not None or _experiments_missing(
            config, self._experiments.failures, self._credentials
        ):
            task = self._task("experiments", cache_key, fetch_experiments)
            fetches.append((task, experiments_wait.EVAL_WAIT_CAP_SECONDS))
        if org is not None or await self._model_catalog_missing(config):
            task = self._task("model_catalog", cache_key, fetch_model_catalog)
            fetches.append((task, model_catalog_wait.MODEL_CATALOG_WAIT_CAP_SECONDS))
        if not fetches:
            return False
        # The read stops waiting at each source's limit counted from its own
        # start. Only a fetch's own limit, counted once the org config is in,
        # records a failure: the org config's time is not the fetch's.
        await asyncio.gather(
            *(asyncio.wait({task}, timeout=limit) for task, limit in fetches)
        )
        return True

    async def _model_catalog_missing(self, config: VibeConfigSchema) -> bool:
        if self._model_catalog.failures.failed:
            return False
        key = ModelCatalogKey.current(config, self._credentials)
        return key is not None and await self._model_catalog.load(key) is None

    def _task[T](
        self, source: str, cache_key: str, start: Callable[[], Coroutine[Any, Any, T]]
    ) -> asyncio.Task[T]:
        key = (source, cache_key)
        if (task := self._tasks.get(key)) is not None and not task.done():
            return task
        task = asyncio.create_task(start(), name=f"vibe-host-{source}-fetch")
        self._tasks[key] = task
        task.add_done_callback(lambda done: self._forget(key, done))
        return task

    def _forget(self, key: tuple[str, str], task: asyncio.Task[Any]) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]


def _experiments_missing(
    config: VibeConfigSchema,
    failures: FetchFailures,
    credentials: PlatformCredentialFactory | None,
) -> bool:
    return (
        experiments_eval_runs(config, credentials)
        and not failures.failed
        and load_cached_eval_response(config, credentials) is None
    )


def _limit_this_fetch(wait: Callable[[asyncio.Task[Any]], FetchWait]) -> None:
    """Arm the fetch's limit on the running task, from now."""
    task = asyncio.current_task()
    assert task is not None
    wait(task)


async def _fetch_org_config(
    harness_files: HarnessFilesManager,
    org_config: OrgConfigService,
    credentials: PlatformCredentialFactory | None,
) -> VibeConfigSchema:
    """Fill the process's org config; returns the config it applies to."""
    orchestrator = await build_default_orchestrator(harness_files=harness_files)
    org_key, _, _ = await apply_cached_layers(
        orchestrator, None, credentials, model_catalog=None
    )
    failure = await org_config.ensure(org_key)
    if failure is not None:
        logger.info(
            "Org config not fetched for a read outside a session outcome=%s",
            failure.outcome.value,
        )
        return orchestrator.config
    await apply_cached_layers(orchestrator, org_config, credentials, model_catalog=None)
    return orchestrator.config


__all__ = ["MissingLayerFetches"]
