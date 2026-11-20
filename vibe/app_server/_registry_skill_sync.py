from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

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


@dataclass(frozen=True)
class PickRestore:
    """``fetch`` downloads again the session's picks a cleanup took and returns the
    names it could not. Those go to ``missing`` and are tried again after each of
    ``retry_delays``: once they arrive they go to ``restored``, and if they never do
    they go to ``missing`` again with ``final``.
    """

    fetch: Callable[[], Awaitable[Sequence[str]]]
    missing: Callable[[Sequence[str], bool], None]
    restored: Callable[[Sequence[str]], None]
    retry_delays: Sequence[float] = ()


class RegistrySkillSync:
    """A sync that did not succeed, or whose derivation failed, is tried again on the
    next derivation. While it follows the session it holds the versions the session
    loads, so a cleanup never evicts them. It also fetches again the session's picks
    a cleanup took, and applies them once they arrive.
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
        restore: PickRestore | None = None,
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
        self._restore = restore
        self._following = False
        self._synced_roots: tuple[Path, ...] | None = None
        self._cleaned_roots: tuple[Path, ...] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._lease = StoreLease()
        self._lease_order = asyncio.Lock()
        self._lease_tasks: set[asyncio.Task[None]] = set()

    def start(self, *, restore: bool = True) -> asyncio.Task[None] | None:
        """Called once the open has answered, so nothing here holds the open. The
        first restore of picks a cleanup took, when the session has picks to restore;
        its retries are not part of it.
        """
        self._following = True
        restoring = self._start_restore() if restore else None
        self.follow()
        return restoring

    def follow(self) -> None:
        """A no-op before ``start`` and after ``stop``; a sync still running for earlier
        roots is left to finish.
        """
        if not self._following:
            return
        # Session picks load without registry skills on, so they are held even then.
        held = self._hold_running()
        roots = tuple(self._roots())
        if not self._config().experimental_enable_registry_skills:
            self._clean_without_sync(roots, after=held)
            return
        if roots == self._synced_roots:
            return
        self._synced_roots = roots
        self._track(self._sync(roots), name="vibe-unified-registry-sync")

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

    def _start_restore(self) -> asyncio.Task[None] | None:
        if self._restore is None:
            return None
        return self._track(
            self._restored(self._restore), name="vibe-unified-registry-restore"
        )

    async def _restored(self, restore: PickRestore) -> None:
        """An attempt that raised says nothing of what is missing, so only names the
        restore returned are reported.
        """
        missing = await self._restore_once(restore.fetch)
        if missing is not None and not missing:
            return
        if not restore.retry_delays:
            restore.missing(missing or (), True)
            return
        if missing:
            restore.missing(missing, False)
        self._track(
            self._retried(restore, missing or ()), name="vibe-unified-registry-restore"
        )

    async def _retried(self, restore: PickRestore, reported: Sequence[str]) -> None:
        """Picks the client was told are missing are announced as they arrive."""
        pending = list(reported)
        last: Sequence[str] = reported
        for delay in restore.retry_delays:
            await asyncio.sleep(delay)
            missing = await self._restore_once(restore.fetch)
            if missing is None:
                continue
            arrived = [name for name in pending if name not in missing]
            if arrived:
                restore.restored(arrived)
                pending = [name for name in pending if name in missing]
            if not missing:
                return
            last = missing
        restore.missing(last, True)

    async def _restore_once(
        self, restore: Callable[[], Awaitable[Sequence[str]]]
    ) -> Sequence[str] | None:
        """The picks still missing, or ``None`` when the restore raised. Checks the
        session against its picks whatever the restore wrote: another session can
        have put a missing pick back after this one opened without it.
        """
        try:
            missing: Sequence[str] | None = await restore()
        except Exception:
            logger.exception("Failed to restore the session's picked skills")
            missing = None
        await self._caught_up()
        return missing

    def _track(
        self, work: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _clean_without_sync(
        self, roots: tuple[Path, ...], *, after: asyncio.Task[None]
    ) -> None:
        """Session picks fetch into the store without registry skills on, so the
        store is still cleaned, once per set of roots and after the session holds
        what it loads, and nothing is synced.
        """
        if self._cleanup is None or roots == self._cleaned_roots:
            return
        self._cleaned_roots = roots

        async def clean() -> None:
            await after
            await self._evict_unused(roots)

        self._track(clean(), name="vibe-unified-registry-cleanup")

    def _hold_running(self) -> asyncio.Task[None]:
        versions = tuple(self._running().values())
        return self._in_order(lambda: self._hold(versions))

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
