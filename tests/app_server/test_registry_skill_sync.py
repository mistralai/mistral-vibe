from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
import os
from pathlib import Path
import threading
from typing import Any
from unittest.mock import AsyncMock

import pytest
import respx

from tests.conftest import build_test_vibe_config
from vibe.app_server._registry_skill_sync import RegistrySkillSync, RegistryStep
from vibe.app_server._skills import registry_versions_of
from vibe.core.config import VibeConfigSchema
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialFactory,
    PlatformCredentialProvider,
)
from vibe.core.skills.models import RegistryRef, SkillInfo, SkillSource
from vibe.core.skills.registry import (
    RegistrySyncResult,
    RegistrySyncStatus,
    refresh_registry_skills,
)
from vibe.core.skills.registry._leases import held_versions, leases_root

_REPO = Path("/repo")
_OTHER = Path("/other")
_SYNCED = RegistrySyncResult(RegistrySyncStatus.OK)
_RUNNING = {"deploy": ("id-deploy", 1)}


class _Session:
    def __init__(
        self,
        *,
        registry: bool = True,
        stale: bool = False,
        refresh: RegistryStep[RegistrySyncResult] | None = None,
        cleanup: RegistryStep[object] | None = None,
    ) -> None:
        self.roots = [_REPO]
        self.config = build_test_vibe_config(
            experimental_enable_registry_skills=registry
        )
        self.resolved = {"deploy": ("id-deploy", 2)} if stale else dict(_RUNNING)
        self.check_errors: list[Exception] = []
        self.checks = 0
        self.apply = AsyncMock(return_value=True)
        self.credentials: PlatformCredentialFactory | None = None
        self.sync = RegistrySkillSync(
            config=lambda: self.config,
            credentials=lambda: self.credentials,
            refresh=refresh or _fetches(),
            cleanup=cleanup,
            roots=lambda: self.roots,
            running=lambda: _RUNNING,
            resolve=self._resolve,
            apply=self.apply,
        )

    def _resolve(self) -> dict[str, tuple[str, int]]:
        self.checks += 1
        if self.check_errors:
            raise self.check_errors.pop(0)
        return dict(self.resolved)


def _fetches(*results: RegistrySyncResult) -> AsyncMock:
    """A registry fetch answering ``results`` in turn, else always synced."""
    refresh = AsyncMock(side_effect=list(results) if results else None)
    if not results:
        refresh.return_value = _SYNCED
    return refresh


async def _settled(sync: RegistrySkillSync) -> None:
    while sync._tasks or sync._lease_tasks:
        await asyncio.gather(*sync._tasks, *sync._lease_tasks)


def _synced_roots(refresh: AsyncMock) -> list[tuple[Path, ...]]:
    return [call.args[1] for call in refresh.await_args_list]


@pytest.mark.asyncio
async def test_a_derivation_before_the_host_starts_the_sync_fetches_nothing() -> None:
    refresh = _fetches()
    session = _Session(refresh=refresh)

    session.sync.follow()

    assert not session.sync._tasks
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_set_of_roots_is_synced_once() -> None:
    refresh = _fetches()
    session = _Session(refresh=refresh)
    session.sync.start()
    await _settled(session.sync)

    session.sync.follow()
    session.roots = [_OTHER]
    session.sync.follow()
    await _settled(session.sync)

    assert _synced_roots(refresh) == [(_REPO,), (_OTHER,)]
    assert refresh.await_args_list[0].args[0] is session.config


@pytest.mark.asyncio
async def test_registry_skills_turned_on_later_start_the_sync() -> None:
    refresh = _fetches()
    session = _Session(registry=False, refresh=refresh)
    session.sync.start()
    assert not session.sync._tasks

    session.config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session.sync.follow()
    await _settled(session.sync)

    assert _synced_roots(refresh) == [(_REPO,)]


@pytest.mark.asyncio
async def test_a_session_derives_again_for_versions_another_sync_fetched() -> None:
    session = _Session(
        stale=True,
        refresh=_fetches(RegistrySyncResult(RegistrySyncStatus.OK, written=0)),
    )

    session.sync.start()
    await _settled(session.sync)

    session.apply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [RegistrySyncStatus.OK, RegistrySyncStatus.SKIPPED, RegistrySyncStatus.FAILED],
    ids=["synced", "skipped", "failed"],
)
async def test_a_session_already_running_its_pins_keeps_its_derivation(
    status: RegistrySyncStatus,
) -> None:
    session = _Session(stale=False, refresh=_fetches(RegistrySyncResult(status)))

    session.sync.start()
    await _settled(session.sync)

    assert session.checks == 1
    session.apply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "synced"),
    [
        (RegistrySyncStatus.FAILED, 2),
        (RegistrySyncStatus.SKIPPED, 2),
        (RegistrySyncStatus.OK, 1),
    ],
    ids=["failed", "skipped", "ok"],
)
async def test_a_sync_that_did_not_succeed_is_tried_again_on_the_next_derivation(
    status: RegistrySyncStatus, synced: int
) -> None:
    refresh = _fetches(RegistrySyncResult(status), _SYNCED)
    session = _Session(refresh=refresh)
    session.sync.start()
    await _settled(session.sync)

    session.sync.follow()
    await _settled(session.sync)

    assert refresh.await_count == synced


@pytest.mark.asyncio
async def test_a_retry_waits_for_a_later_derivation_than_the_syncs_own() -> None:
    failed = RegistrySyncResult(RegistrySyncStatus.FAILED)
    refresh = _fetches(failed, failed)
    session = _Session(stale=True, refresh=refresh)

    async def derive() -> bool:
        session.sync.follow()
        return True

    session.apply.side_effect = derive
    session.sync.start()
    await _settled(session.sync)

    assert (refresh.await_count, session.apply.await_count) == (1, 1)
    session.sync.follow()
    await _settled(session.sync)
    assert (refresh.await_count, session.apply.await_count) == (2, 2)


@pytest.mark.asyncio
async def test_a_sync_that_raises_is_logged_and_tried_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refresh = AsyncMock(side_effect=[RuntimeError("boom"), _SYNCED])
    session = _Session(refresh=refresh)

    with caplog.at_level("ERROR", logger="vibe"):
        session.sync.start()
        await _settled(session.sync)
    session.sync.follow()
    await _settled(session.sync)

    assert "Failed to sync registry skills" in caplog.text
    assert refresh.await_count == 2


@pytest.mark.asyncio
async def test_roots_that_move_mid_sync_leave_it_running() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def refresh(
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> RegistrySyncResult:
        if roots == (_REPO,):
            entered.set()
            await release.wait()
        return _SYNCED

    session = _Session(refresh=refresh)
    session.sync.start()
    first = next(iter(session.sync._tasks))
    await entered.wait()

    session.roots = [_OTHER]
    session.sync.follow()
    release.set()
    await _settled(session.sync)

    assert not first.cancelled()
    assert session.checks == 2


@pytest.mark.asyncio
async def test_a_failure_of_roots_the_session_left_is_not_tried_again() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[Sequence[Path]] = []

    async def refresh(
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> RegistrySyncResult:
        calls.append(roots)
        if roots == (_REPO,):
            entered.set()
            await release.wait()
            return RegistrySyncResult(RegistrySyncStatus.FAILED)
        return _SYNCED

    session = _Session(refresh=refresh)
    session.sync.start()
    await entered.wait()
    session.roots = [_OTHER]
    session.sync.follow()
    release.set()
    await _settled(session.sync)

    session.sync.follow()
    await _settled(session.sync)

    assert calls == [(_REPO,), (_OTHER,)]


@pytest.mark.asyncio
async def test_a_stopped_sync_is_cancelled_and_follows_nothing_more() -> None:
    entered = asyncio.Event()

    async def refresh(
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> RegistrySyncResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    session = _Session(refresh=refresh)
    session.sync.start()
    running = next(iter(session.sync._tasks))
    await entered.wait()

    stopped = session.sync.stop()
    session.roots = [_OTHER]
    session.sync.follow()

    assert stopped is not None
    await stopped
    assert running.cancelled()
    assert not session.sync._tasks


def _skills(versions: dict[str, int | None]) -> list[SkillInfo]:
    return [
        SkillInfo(
            name=name,
            description="",
            prompt="",
            source=SkillSource.LOCAL if version is None else SkillSource.REGISTRY,
            registry=None
            if version is None
            else RegistryRef(skill_id=f"id-{name}", version=version),
        )
        for name, version in versions.items()
    ]


@pytest.mark.parametrize(
    ("running", "resolved", "differ"),
    [
        ({"deploy": 1, "lint": None}, {"deploy": 1, "lint": None}, False),
        ({"deploy": 1}, {"deploy": 2}, True),
        ({"deploy": 1}, {}, True),
        ({}, {"deploy": 1}, True),
        ({"deploy": 1}, {"deploy": None}, True),
        ({"lint": None}, {}, False),
    ],
    ids=[
        "same",
        "other-version",
        "pin-gone",
        "pin-fetched",
        "a-file-wins-now",
        "files-only",
    ],
)
def test_only_registry_versions_tell_a_session_it_is_out_of_date(
    running: dict[str, int | None], resolved: dict[str, int | None], differ: bool
) -> None:
    before = registry_versions_of(_skills(running))
    after = registry_versions_of(_skills(resolved))
    assert (before != after) is differ


@pytest.mark.asyncio
async def test_a_check_that_raises_is_logged_and_tried_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refresh = _fetches()
    session = _Session(refresh=refresh)
    session.check_errors.append(FileNotFoundError("skills directory gone"))

    with caplog.at_level("ERROR", logger="vibe"):
        session.sync.start()
        await _settled(session.sync)
    session.sync.follow()
    await _settled(session.sync)

    assert "Failed to check the session's registry skills" in caplog.text
    assert "Failed to apply" not in caplog.text
    assert (refresh.await_count, session.checks) == (2, 2)
    session.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_derivation_that_failed_after_a_sync_is_tried_again() -> None:
    refresh = _fetches()
    session = _Session(stale=True, refresh=refresh)
    session.apply.side_effect = [False, True]

    session.sync.start()
    await _settled(session.sync)
    session.sync.follow()
    await _settled(session.sync)

    assert (refresh.await_count, session.apply.await_count) == (2, 2)


@pytest.mark.asyncio
async def test_a_sync_with_nothing_to_fetch_counts_as_done(
    respx_mock: respx.MockRouter,
) -> None:
    registry = respx_mock.route(host="api.mistral.ai")
    results: list[RegistrySyncResult] = []

    async def recorded(
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> RegistrySyncResult:
        results.append(
            await refresh_registry_skills(config, roots, credentials=credentials)
        )
        return results[-1]

    session = _Session(refresh=recorded)

    session.sync.start()
    await _settled(session.sync)
    session.sync.follow()
    session.sync.follow()
    await _settled(session.sync)

    assert [result.status for result in results] == [RegistrySyncStatus.OK]
    assert not registry.called


@pytest.mark.asyncio
async def test_a_followed_session_holds_the_versions_it_loads_until_it_stops() -> None:
    session = _Session(refresh=_fetches())

    session.sync.start()
    await _settled(session.sync)
    held = held_versions()
    await asyncio.gather(*filter(None, [session.sync.stop()]))

    assert held == {("id-deploy", 1)}
    assert held_versions() == set()


class _NewestFirst(ThreadPoolExecutor):
    """Worker threads at their least kind to order: a job runs only when the test says,
    the newest first.
    """

    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self._queued: list[tuple[Callable[[], Any], Future[Any]]] = []

    def submit[**P, T](
        self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[T]:
        future: Future[T] = Future()
        self._queued.append((partial(fn, *args, **kwargs), future))
        return future

    def run_newest_first(self) -> None:
        while self._queued:
            job, future = self._queued.pop()
            try:
                future.set_result(job())
            except Exception as error:
                future.set_exception(error)


@pytest.mark.asyncio
async def test_a_lease_change_never_overtakes_one_asked_for_before_it() -> None:
    threads = _NewestFirst()
    asyncio.get_running_loop().set_default_executor(threads)
    session = _Session(refresh=_fetches())

    session.sync.start()
    stopped = session.sync.stop()
    while not stopped.done():
        await asyncio.sleep(0)
        threads.run_newest_first()

    assert held_versions() == set()


@pytest.mark.asyncio
async def test_the_lease_is_written_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace = os.replace
    writers: list[threading.Thread] = []

    def replaced(source: Path, target: Path) -> None:
        writers.append(threading.current_thread())
        replace(source, target)

    monkeypatch.setattr(os, "replace", replaced)
    session = _Session(refresh=_fetches())

    session.sync.start()
    written_at_once = held_versions()
    await _settled(session.sync)

    assert written_at_once == set()
    assert held_versions() == {("id-deploy", 1)}
    assert writers
    assert threading.current_thread() not in writers
    await session.sync.stop()


@pytest.mark.asyncio
async def test_a_lease_that_cannot_be_taken_is_logged_with_its_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    leases_root().parent.mkdir(parents=True, exist_ok=True)
    leases_root().write_text("not a directory", encoding="utf-8")
    session = _Session(refresh=_fetches())

    with caplog.at_level("WARNING", logger="vibe"):
        session.sync.start()
        await _settled(session.sync)

    (record,) = [
        record
        for record in caplog.records
        if "Could not record the registry skills" in record.getMessage()
    ]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], FileExistsError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "cleared"),
    [
        (RegistrySyncStatus.OK, True),
        (RegistrySyncStatus.SKIPPED, False),
        (RegistrySyncStatus.FAILED, False),
    ],
)
async def test_only_a_sync_that_succeeded_clears_unused_versions(
    status: RegistrySyncStatus, cleared: bool
) -> None:
    cleanup = AsyncMock()
    session = _Session(refresh=_fetches(RegistrySyncResult(status)), cleanup=cleanup)

    session.sync.start()
    await _settled(session.sync)

    cleanups = [call.args for call in cleanup.await_args_list]
    assert cleanups == ([(session.config, (_REPO,))] if cleared else [])


@pytest.mark.asyncio
async def test_the_sync_and_its_cleanup_carry_the_session_s_credentials() -> None:
    def signed_in(_target: str) -> PlatformCredentialProvider:
        return ApiKeyPlatformCredentialProvider.from_api_key("key")

    refresh = _fetches()
    cleanup = AsyncMock()
    session = _Session(refresh=refresh, cleanup=cleanup)
    session.credentials = signed_in

    session.sync.start()
    await _settled(session.sync)

    assert refresh.await_args is not None
    assert refresh.await_args.kwargs == {"credentials": signed_in}
    assert cleanup.await_args is not None
    assert cleanup.await_args.kwargs == {"credentials": signed_in}


@pytest.mark.asyncio
async def test_a_cleanup_that_raises_is_logged_and_the_roots_stay_synced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refresh = _fetches()
    session = _Session(refresh=refresh, cleanup=AsyncMock(side_effect=OSError("busy")))

    session.sync.start()
    await _settled(session.sync)
    session.sync.follow()
    await _settled(session.sync)

    assert "Failed to clear unused registry skill versions" in caplog.text
    assert _synced_roots(refresh) == [(_REPO,)]
