from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import time
from typing import Any, cast

import pytest

from tests.app_server.storage_usage._tree import Tree, write
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler, worktree_remove_response
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import (
    SessionBackendError,
    SessionBackendHost,
)
from vibe.app_server._storage_usage import files
from vibe.app_server._worktree_cleanup import WorktreeCleanup
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    STORAGE_USAGE_UPDATED,
    ClientInfo,
    EmptyResponse,
    Notification,
    ProtocolErrorCode,
    SessionArchiveParams,
    SessionArchiveResponse,
    SessionDeleteParams,
    StorageLeftoversCleanResponse,
    StorageSessionsDeleteResponse,
    StorageUsageReadResponse,
    StorageUsageRefreshParams,
    StorageUsageRefreshResponse,
    StorageUsageUpdatedParams,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.worktree import (
    ManagedWorktree,
    WorktreeRelease,
    WorktreeReleaseOutcome,
)
from vibe.core.git.worktree.record import WorktreeRecordError


async def _client(process: HarnessProcess) -> AppServerClient:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    await client.initialize(ClientInfo(name="storage-test", version="1"))
    await client.notify("initialized")
    return client


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_sessionless_client_reads_refreshes_and_gets_the_update(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: One session on disk; a connection with no session attached, as
    Desktop's catalogue connection.
    *Do*: Read, refresh, and wait for the result.
    *Assert*: No numbers first; the refresh starts; ``storage/usage/updated``
    reaches the connection with the session's row.
    """
    # Prepare
    tree.session("s", size=5000)
    process = HarnessProcess(services=process_services)
    client = await _client(process)
    incoming = client.incoming()
    try:
        # Do
        first = validate_wire(
            StorageUsageReadResponse, await client.request("storage/usage/read", {})
        )
        started = validate_wire(
            StorageUsageRefreshResponse,
            await client.request("storage/usage/refresh", {"full": True}),
        )
        result = await _next_result(incoming)

        # Assert
        assert first.snapshot is None
        assert started.started
        assert result.snapshot is not None
        assert [row.session_id for row in result.snapshot.sessions] == ["s"]
    finally:
        await incoming.aclose()
        await client.close()
        await process.close()


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_client_cleans_a_leftover_folder_and_the_numbers_follow(
    tree: Tree, process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An unknown folder in a worktree bucket, measured once.
    *Do*: Ask the server to clean it, then wait for the refresh it starts.
    *Assert*: Removed, and the next numbers no longer offer it.
    """
    # Prepare
    monkeypatch.setattr("vibe.app_server._storage_usage.cleanup.RESERVATION_GRACE_S", 0)
    folder = tree.worktrees_root / "repo-0123456789ab" / ".cargo-target"
    write(folder / "lib", 50_000)
    process = HarnessProcess(services=process_services)
    client = await _client(process)
    incoming = client.incoming()
    try:
        await client.request("storage/usage/refresh", {"full": True})
        before = await _next_result(incoming)
        assert before.snapshot is not None
        offered = before.snapshot.leftover_cleanup.unknown_folders
        assert [item.id for item in offered] == ["repo-0123456789ab/.cargo-target"]

        # Do
        response = validate_wire(
            StorageLeftoversCleanResponse,
            await client.request(
                "storage/leftovers/clean",
                {"folderIds": ["repo-0123456789ab/.cargo-target"]},
            ),
        )
        after = await _next_result(incoming)

        # Assert
        assert response.removed == ["repo-0123456789ab/.cargo-target"]
        assert not folder.exists()
        assert after.snapshot is not None
        assert after.snapshot.leftover_cleanup.unknown_folders == []
    finally:
        await incoming.aclose()
        await client.close()
        await process.close()


async def _next_result(incoming: Any) -> StorageUsageUpdatedParams:
    async with asyncio.timeout(90):
        while True:
            message = await anext(incoming)
            if not isinstance(message, Notification):
                continue
            assert message.method == STORAGE_USAGE_UPDATED
            update = validate_wire(StorageUsageUpdatedParams, message.params)
            if update.status != "refreshing":
                return update


class _ArchivingHost:
    """A session host that archives and deletes, and nothing else."""

    async def archive(self, params: SessionArchiveParams) -> SessionArchiveResponse:
        del params
        return SessionArchiveResponse(archived_at=1)

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        del params
        return EmptyResponse()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("session/archive", {"sessionId": "s", "archived": True}),
        ("session/delete", {"sessionId": "s"}),
    ],
)
async def test_archive_and_delete_touch_the_stale_marker(
    tree: Tree, process_services: ProcessServices, method: str, params: dict[str, Any]
) -> None:
    """*Prepare*: A server whose session host archives and deletes.
    *Do*: Archive, or delete, a stored session.
    *Assert*: The stale marker exists afterwards.
    """
    # Prepare
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        oidc_tenants=process_services.oidc_tenants,
        session_backend_host_factory=lambda _: cast(
            SessionBackendHost, _ArchivingHost()
        ),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        ),
    )
    assert files.stale_marker_ms(tree.home) == 0

    # Do
    await server._dispatch_request(method, params)

    # Assert
    assert files.stale_marker_ms(tree.home) > 0


# Harness refusals that are no live process holding the session.
_OTHER_CONFLICTS = {
    "child": "child_session_requires_parent",
    "queued": "turn_queue_pending",
    "migrating": "unfinished_work_migration",
}


class _DeletingHost:
    """A session host whose delete removes the session's folder from disk,
    refusing one a test marks live, and failing one a test marks broken, as
    the real adapter does: with ``SessionBackendError``.
    """

    def __init__(
        self, sessions_root: Path, live: set[str], broken: frozenset[str] = frozenset()
    ) -> None:
        self._sessions_root = sessions_root
        self._live = live
        self._broken = broken

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        if params.session_id in self._live:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "live", {"harnessCode": "session_busy"}
            )
        if params.session_id in _OTHER_CONFLICTS:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                "refused",
                {"harnessCode": _OTHER_CONFLICTS[params.session_id]},
            )
        if params.session_id in self._broken:
            raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, "broken")
        folder = self._sessions_root / "unified" / params.session_id
        if not folder.exists():
            raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, "gone")
        shutil.rmtree(folder)
        return EmptyResponse()


class _RequestFailingHost:
    """A session host whose delete refuses with ``RequestFailure``, as the
    host handler's ``session/delete`` does.
    """

    def __init__(self, codes: dict[str, ProtocolErrorCode]) -> None:
        self._codes = codes

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        raise RequestFailure(self._codes[params.session_id], "refused")


@pytest.mark.asyncio
@pytest.mark.timeout(120)
@pytest.mark.parametrize("failure", [SessionBackendError, RequestFailure])
async def test_each_delete_refusal_maps_to_its_reason_and_only_bugs_log_a_trace(
    tree: Tree,
    process_services: ProcessServices,
    caplog: pytest.LogCaptureFixture,
    failure: type[Exception],
) -> None:
    """*Prepare*: Sessions whose delete is refused as live, as not found, and
    as an internal error, raised as the real adapter raises
    (``SessionBackendError``) or as the host handler does (``RequestFailure``).
    *Do*: Ask the server to delete them.
    *Assert*: ``in_use``, ``not_found`` and ``error``; a stack trace is logged
    for the internal error only.
    """
    # Prepare
    for name in ("live", "missing", "broken"):
        tree.session(name, archived=True)
    codes = {
        "live": ProtocolErrorCode.CONFLICT,
        "missing": ProtocolErrorCode.NOT_FOUND,
        "broken": ProtocolErrorCode.INTERNAL_ERROR,
    }
    host: object
    if failure is SessionBackendError:
        shutil.rmtree(tree.sessions_root / "unified" / "missing")
        host = _DeletingHost(tree.sessions_root, {"live"}, frozenset({"broken"}))
    else:
        host = _RequestFailingHost(codes)
    server = _storage_server(tree, process_services, host)
    caplog.set_level(logging.DEBUG, logger="vibe")

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete", {"sessionIds": list(codes)}
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert [(kept.id, kept.reason) for kept in response.kept] == [
        ("live", "in_use"),
        ("missing", "not_found"),
        ("broken", "error"),
    ]
    traced = [record.getMessage() for record in caplog.records if record.exc_info]
    assert traced == ["Failed to delete stored session broken"]


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_storage_sessions_delete_deletes_each_and_answers_what_it_freed(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: Measured archived sessions, one held by a live process, one
    refused by the session host, and a session that stays.
    *Do*: Ask the server to delete them and a session that does not exist.
    *Assert*: Each goes through the session host's delete; a held one, a
    refused one and an unknown one are kept with their reason; the freed
    bytes are the removed histories as the last measurement sized them.
    """
    # Prepare
    tree.session("old-a", size=5_000, archived=True)
    tree.session("old-b", size=6_000, archived=True)
    tree.session("held", archived=True)
    tree.session("refused", archived=True)
    tree.session("kept")
    service = process_services.storage_usage
    assert service is not None
    await service.refresh(StorageUsageRefreshParams(full=True))
    while service._running():
        await asyncio.sleep(0.05)
    histories = {
        row.session_id: row.history_bytes
        for row in (await service.read()).snapshot.sessions  # type: ignore[union-attr]
    }
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        oidc_tenants=process_services.oidc_tenants,
        session_backend_host_factory=lambda _: cast(
            SessionBackendHost, _DeletingHost(tree.sessions_root, {"refused"})
        ),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        ),
        storage_usage=service,
    )
    service.open_session_ids = lambda only=None: (
        {"held"}
        & (  # type: ignore[method-assign]
            {"held"} if only is None else only
        )
    )

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete",
        {"sessionIds": ["old-a", "held", "refused", "old-b", "missing"]},
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert response.removed == ["old-a", "old-b"]
    assert {(kept.id, kept.reason) for kept in response.kept} == {
        ("held", "in_use"),
        ("refused", "in_use"),
        ("missing", "not_found"),
    }
    assert response.freed_bytes == histories["old-a"] + histories["old-b"]
    assert not (tree.sessions_root / "unified" / "old-a").exists()
    assert (tree.sessions_root / "unified" / "held").exists()


def _storage_server(
    tree: Tree, process_services: ProcessServices, host: object
) -> AppServer:
    _, server_transport = memory_transport_pair()
    return AppServer(
        server_transport,
        oidc_tenants=process_services.oidc_tenants,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, host),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        ),
        storage_usage=process_services.storage_usage,
    )


class _OpeningHost(_DeletingHost):
    """Deleting one session makes another open in a live process meanwhile."""

    def __init__(self, sessions_root: Path, opens: dict[str, str]) -> None:
        super().__init__(sessions_root, set())
        self._opens = opens
        self.opened: set[str] = set()

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        response = await super().delete(params)
        if (other := self._opens.get(params.session_id)) is not None:
            self.opened.add(other)
        return response


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_session_opened_during_a_bulk_delete_is_kept(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: Two closed sessions; deleting the first makes a live process
    open the second.
    *Do*: Ask the server to delete both.
    *Assert*: The first goes; the second, open by the time its turn comes, is
    kept as in use and stays on disk.
    """
    # Prepare
    tree.session("first", archived=True)
    tree.session("second", archived=True)
    host = _OpeningHost(tree.sessions_root, {"first": "second"})
    server = _storage_server(tree, process_services, host)
    service = process_services.storage_usage
    assert service is not None

    def open_ids(only: set[str] | None = None) -> set[str]:
        return set(host.opened) if only is None else host.opened & only

    service.open_session_ids = open_ids  # type: ignore[method-assign]

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete", {"sessionIds": ["first", "second"]}
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert response.removed == ["first"]
    assert [(kept.id, kept.reason) for kept in response.kept] == [("second", "in_use")]
    assert (tree.sessions_root / "unified" / "second").exists()


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_worktree_failing_after_the_deletes_still_answers_and_refreshes(
    tree: Tree, process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session alone in a worktree whose release raises
    ``WorktreeRecordError``, as a corrupt claim does.
    *Do*: Ask the server to delete the session.
    *Assert*: The session is reported removed, the worktree kept with
    ``error``, and a refresh follows.
    """
    # Prepare
    tree.session("old", archived=True)
    worktree = tree.worktree("w", sessions=["old"])

    def release(self: ManagedWorktree, **_kwargs: object) -> WorktreeRelease:
        raise WorktreeRecordError("Unusable worktree holder id '.removing'.")

    monkeypatch.setattr(ManagedWorktree, "release", release)
    server = _storage_server(
        tree, process_services, _DeletingHost(tree.sessions_root, set())
    )
    service = process_services.storage_usage
    assert service is not None
    refreshes: list[StorageUsageRefreshParams] = []
    real_refresh = service.refresh

    async def refresh(params: StorageUsageRefreshParams) -> StorageUsageRefreshResponse:
        refreshes.append(params)
        return await real_refresh(params)

    service.refresh = refresh  # type: ignore[method-assign]

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete", {"sessionIds": ["old"]}
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert response.removed == ["old"]
    assert [(kept.id, kept.reason) for kept in response.kept_worktrees] == [
        ("repo-0123456789ab/w", "error")
    ]
    assert worktree.is_dir()
    assert refreshes


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_the_answer_lists_deleted_sessions_even_when_the_refresh_after_fails(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: A deletable session; the refresh that follows the delete
    raises.
    *Do*: Ask the server to delete the session.
    *Assert*: The call still answers, with the session removed: its files are
    gone, so clients must forget it.
    """
    # Prepare
    tree.session("old", archived=True)
    server = _storage_server(
        tree, process_services, _DeletingHost(tree.sessions_root, set())
    )
    service = process_services.storage_usage
    assert service is not None

    async def fail_async(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    service.refresh = fail_async  # type: ignore[method-assign]

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete", {"sessionIds": ["old"]}
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert response.removed == ["old"]


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_only_a_busy_session_conflict_is_kept_as_in_use(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: Sessions whose delete the harness refuses as busy, as a
    child needing its parent, with a queued turn, and mid migration.
    *Do*: Ask the server to delete them.
    *Assert*: Only the busy one is ``in_use``; the others are ``error``: no
    process holds them, so "open elsewhere" would mislead.
    """
    # Prepare
    names = ["live", *_OTHER_CONFLICTS]
    for name in names:
        tree.session(name, archived=True)
    server = _storage_server(
        tree, process_services, _DeletingHost(tree.sessions_root, {"live"})
    )

    # Do
    result = await server._dispatch_request(
        "storage/sessions/delete", {"sessionIds": names}
    )

    # Assert
    response = validate_wire(StorageSessionsDeleteResponse, result.response)
    assert [(kept.id, kept.reason) for kept in response.kept] == [
        ("live", "in_use"),
        ("child", "error"),
        ("queued", "error"),
        ("migrating", "error"),
    ]


def _removed(root: Path) -> WorktreeRelease:
    return WorktreeRelease(outcome=WorktreeReleaseOutcome.REMOVED, root=root)


def test_a_finished_reap_touches_the_stale_marker(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A worktree whose reap removes it.
    *Do*: Run the reaper's step for it.
    *Assert*: The stale marker moved.
    """
    # Prepare
    folder = tree.worktree("w", sessions=["gone"])
    managed = ManagedWorktree.at(folder)
    assert managed is not None
    monkeypatch.setattr(
        ManagedWorktree, "reap_if_finished", lambda self, *_a, **_k: _removed(folder)
    )
    cleanup = WorktreeCleanup(session_finished=lambda _path: True)

    # Do
    done = cleanup._complete(managed, trust_store=cleanup._trust_store)

    # Assert
    assert done
    assert files.stale_marker_ms(tree.home) > 0


def test_a_worktree_remove_request_touches_the_stale_marker(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A managed worktree whose release removes it.
    *Do*: Answer ``workspace/git/worktrees/remove`` for it, then inspect only.
    *Assert*: The removal touched the marker; the inspection did not.
    """
    # Prepare
    folder = tree.worktree("w", sessions=["gone"])
    monkeypatch.setattr(ManagedWorktree, "release", lambda self, **_k: _removed(folder))
    monkeypatch.setattr(
        ManagedWorktree, "probe_release", lambda self, *_a, **_k: _removed(folder)
    )

    # Do
    worktree_remove_response(folder, inspect=True)
    after_inspect = files.stale_marker_ms(tree.home)
    response = worktree_remove_response(folder)

    # Assert
    assert response.outcome == "removed"
    assert after_inspect == 0
    assert files.stale_marker_ms(tree.home) > 0


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_a_full_re_measure_does_not_stall_the_event_loop(
    tree: Tree, process_services: ProcessServices
) -> None:
    """*Prepare*: Sessions and worktrees holding thousands of files; the real
    background worker process.
    *Do*: Run a full re-measure while a task on the event loop measures how
    late its 10 ms ticks wake, as a streaming turn's would.
    *Assert*: The scan finishes and no tick is 100 ms late.
    """
    # Prepare
    for index in range(20):
        tree.session(f"s{index}", size=100)
        folder = tree.worktree(f"w{index}", sessions=[f"s{index}"], size=100)
        for part in range(400):
            (folder / f"f{part}.txt").write_bytes(b"x" * 64)
    service = process_services.storage_usage
    finished = asyncio.Event()
    lags: list[float] = []

    async def notify(_method: str, params: Any) -> None:
        if params.status != "refreshing":
            finished.set()

    service.listen(notify)

    async def ticker() -> None:
        while not finished.is_set():
            expected = time.perf_counter() + 0.01
            await asyncio.sleep(0.01)
            lags.append(time.perf_counter() - expected)

    # Do
    ticking = asyncio.create_task(ticker())
    await service.refresh(StorageUsageRefreshParams(full=True))
    await asyncio.wait_for(finished.wait(), 90)
    await ticking
    read = await service.read()

    # Assert
    assert read.snapshot is not None and len(read.snapshot.worktrees) == 20
    assert process_services.background.pid is not None
    assert max(lags) < 0.1, f"worst lag {max(lags) * 1000:.0f} ms"


def test_a_prune_that_removed_worktrees_touches_the_stale_marker(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A retention prune that removes two worktrees, then one that
    removes none.
    *Do*: Prune.
    *Assert*: Only the first touched the marker.
    """
    # Prepare
    removed = iter([0, 2])
    monkeypatch.setattr(ManagedWorktree, "prune", lambda *_a, **_k: next(removed))
    cleanup = WorktreeCleanup()

    # Do
    cleanup.prune(15)
    after_none = files.stale_marker_ms(tree.home)
    cleanup.prune(15)

    # Assert
    assert after_none == 0
    assert files.stale_marker_ms(tree.home) > 0
