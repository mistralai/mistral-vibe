from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any, cast

import pytest

from tests.app_server.storage_usage._tree import Tree
from vibe.app_server._host import HostRequestHandler, worktree_remove_response
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._storage_usage import files
from vibe.app_server._worktree_cleanup import WorktreeCleanup
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    STORAGE_USAGE_UPDATED,
    ClientInfo,
    EmptyResponse,
    Notification,
    SessionArchiveParams,
    SessionArchiveResponse,
    SessionDeleteParams,
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
