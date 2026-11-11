from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from vibe.core.skills.registry import RegistrySyncResult, RegistrySyncStatus, StoreLease
from vibe.core.utils.concurrency import cancel_pending
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.app_server._skills import RegistryVersions
    from vibe.core.config import VibeConfigSchema
    from vibe.core.platform_auth import PlatformCredentialFactory


class RegistryStep[T](Protocol):
    def __call__(
        self,
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> Awaitable[T]: ...


class RegistrySkillSync:
    """A sync that did not succeed, or whose derivation failed, is tried again on the
    next derivation. While it follows the session it holds the versions the session
    loads, so a cleanup never evicts them.
    """

    def __init__(
        self,
        *,
        config: Callable[[], VibeConfigSchema],
        credentials: Callable[[], PlatformCredentialFactory | None],
        refresh: RegistryStep[RegistrySyncResult],
        cleanup: RegistryStep[object] | None,
        roots: Callable[[], Sequence[Path]],
        running: Callable[[], RegistryVersions],
        resolve: Callable[[], RegistryVersions],
        apply: Callable[[], Awaitable[bool]],
    ) -> None:
        """``apply`` logs its own failure and never raises. Without ``cleanup`` this
        process never cleans the store.
        """
        self._config = config
        self._credentials = credentials
        self._refresh = refresh
        self._cleanup = cleanup
        self._roots = roots
        self._running = running
        self._resolve = resolve
        self._apply = apply
        self._following = False
        self._synced_roots: tuple[Path, ...] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._lease = StoreLease()
        self._lease_order = asyncio.Lock()
        self._lease_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        self._following = True
        self.follow()

    def follow(self) -> None:
        """A no-op before ``start`` and after ``stop``; a sync still running for earlier
        roots is left to finish.
        """
        if not self._following:
            return
        if not self._config().experimental_enable_registry_skills:
            return
        self._hold_running()
        roots = tuple(self._roots())
        if roots == self._synced_roots:
            return
        self._synced_roots = roots
        task = asyncio.create_task(self._sync(roots), name="vibe-unified-registry-sync")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def stop(self) -> asyncio.Future[list[BaseException | None]]:
        """The cancelled syncs and the lease's release come back to await; the release
        runs after every hold asked for before it.
        """
        self._following = False
        released = self._in_order(self._lease.release)
        cancelled = [task for task in self._tasks if cancel_pending(task) is not None]
        self._tasks.clear()
        return asyncio.gather(*cancelled, released, return_exceptions=True)

    async def _sync(self, roots: tuple[Path, ...]) -> None:
        """Roots whose sync or check did not succeed are forgotten once this sync's own
        derivation is done: the next derivation tries them again, this one does not, and
        an exception never leaves them marked as synced.
        """
        done = False
        try:
            refreshed = await self._refreshed(roots)
            done = await self._caught_up() and refreshed
            if refreshed:
                await self._evict_unused(roots)
        finally:
            if not done and self._synced_roots == roots:
                self._synced_roots = None

    def _hold_running(self) -> None:
        versions = tuple(self._running().values())
        self._in_order(lambda: self._hold(versions))

    def _hold(self, versions: Iterable[tuple[str, int]]) -> None:
        try:
            self._lease.hold(versions)
        except OSError:
            logger.warning(
                "Could not record the registry skills this session loads", exc_info=True
            )

    def _in_order(self, operation: Callable[[], None]) -> asyncio.Task[None]:
        """In a worker thread, after every lease operation asked for before it, so a
        later hold or the release never overtakes an earlier one.
        """

        async def run() -> None:
            async with self._lease_order:
                await asyncio.to_thread(operation)

        task = asyncio.create_task(run(), name="vibe-unified-registry-lease")
        self._lease_tasks.add(task)
        task.add_done_callback(self._lease_tasks.discard)
        return task

    async def _evict_unused(self, roots: tuple[Path, ...]) -> None:
        if self._cleanup is None:
            return
        try:
            await self._cleanup(self._config(), roots, credentials=self._credentials())
        except Exception:
            logger.exception("Failed to clear unused registry skill versions")

    async def _refreshed(self, roots: tuple[Path, ...]) -> bool:
        try:
            result = await self._refresh(
                self._config(), roots, credentials=self._credentials()
            )
        except Exception:
            logger.exception("Failed to sync registry skills")
            return False
        return result.status is RegistrySyncStatus.OK

    async def _caught_up(self) -> bool:
        """Whether the session now runs what its pins resolve to; false when the check
        or the derivation failed.
        """
        try:
            resolved = await asyncio.to_thread(self._resolve)
        except Exception:
            logger.exception("Failed to check the session's registry skills")
            return False
        if dict(resolved) == dict(self._running()):
            return True
        return await self._apply()
