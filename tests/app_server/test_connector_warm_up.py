"""The app server fetches the connector catalog before any session opens."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess

_start_connector_warm_up = HarnessProcess.start_connector_warm_up


def _process(resolve: Any, *, process_services: ProcessServices) -> HarnessProcess:
    process = HarnessProcess(services=process_services)
    process.connector_catalog.warm_up = resolve
    return process


@pytest.mark.asyncio
async def test_the_connector_catalog_is_fetched_at_start(
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A process with no session open.
    *Do*: Start the warm-up.
    *Assert*: The catalog is fetched once, from the process's own config.
    """
    # Prepare
    resolved = asyncio.Event()
    calls: list[object] = []

    async def resolve(orchestrator: object, **_: object) -> None:
        calls.append(orchestrator)
        resolved.set()

    process = _process(resolve, process_services=process_services)

    # Do
    _start_connector_warm_up(process)
    _start_connector_warm_up(process)
    await asyncio.wait_for(resolved.wait(), timeout=5)
    await process.close()

    # Assert
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_failed_warm_up_is_left_to_the_sessions(
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A process whose catalog fetch fails.
    *Do*: Warm up, then close.
    *Assert*: Nothing is raised: a session's own load reports the failure.
    """

    # Prepare
    async def resolve(_orchestrator: object, **_: object) -> None:
        raise RuntimeError("connectors down")

    process = _process(resolve, process_services=process_services)

    # Do
    _start_connector_warm_up(process)
    warm_up = process._connector_warm_up
    assert warm_up is not None
    await asyncio.wait_for(asyncio.shield(warm_up), timeout=5)
    await process.close()

    # Assert
    assert warm_up.exception() is None


@pytest.mark.asyncio
async def test_closing_the_process_cancels_the_warm_up(
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A warm-up stuck on a catalog fetch.
    *Do*: Close the process.
    *Assert*: The fetch is cancelled rather than outliving the process.
    """
    # Prepare
    started = asyncio.Event()

    async def resolve(_orchestrator: object, **_: object) -> None:
        started.set()
        await asyncio.Event().wait()

    process = _process(resolve, process_services=process_services)
    _start_connector_warm_up(process)
    await asyncio.wait_for(started.wait(), timeout=5)
    warm_up = process._connector_warm_up
    assert warm_up is not None

    # Do
    await asyncio.wait_for(process.close(), timeout=5)

    # Assert
    assert warm_up.cancelled()


@pytest.mark.asyncio
async def test_a_process_fetches_once_however_old_its_disk_copy_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A catalog another process stored a minute ago, before the
      user connected a connector, and two channels of a new process.
    *Do*: Warm both channels up.
    *Assert*: The process fetches once, despite the recent copy: the channel
      that comes second reuses that fetch.
    """
    from tests.conftest import build_test_vibe_config
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server.connector_catalog import (
        ConnectorCatalogService,
        SharedConnectorCatalog,
    )

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fetches = 0

    async def fetch(
        _base_url: str, _credentials: object, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetches
        fetches += 1
        return {"connectors": []}

    cache_path = tmp_path / "connectors.json"
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True)
    )
    earlier = SharedConnectorCatalog(cache_path=cache_path, fetch_bootstrap=fetch)
    await ConnectorCatalogService(
        earlier, implicit_source_enabled=False
    ).resolve_catalog(orchestrator)
    fetches = 0
    shared = SharedConnectorCatalog(cache_path=cache_path, fetch_bootstrap=fetch)

    # Do
    for _ in range(2):
        await ConnectorCatalogService(shared, implicit_source_enabled=False).warm_up(
            orchestrator
        )

    # Assert
    assert fetches == 1


@pytest.mark.asyncio
async def test_a_failed_warm_up_does_not_hold_back_the_first_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A process with nothing cached whose warm-up fetch fails once.
    *Do*: Open the first session's catalog right after.
    *Assert*: The session fetches again and gets the catalog, rather than
      waiting out a backoff the warm-up started.
    """
    from tests.conftest import build_test_vibe_config
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server.connector_catalog import (
        ConnectorCatalogError,
        ConnectorCatalogService,
        SharedConnectorCatalog,
    )

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    answers: list[object] = [OSError("connectors down"), {"connectors": []}]

    async def fetch(
        _base_url: str, _credentials: object, _builtins: tuple[str, ...] = ()
    ) -> object:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True)
    )
    service = ConnectorCatalogService(
        SharedConnectorCatalog(
            cache_path=tmp_path / "connectors.json", fetch_bootstrap=fetch
        ),
        implicit_source_enabled=False,
    )
    with pytest.raises(ConnectorCatalogError):
        await service.warm_up(orchestrator)

    # Do
    catalog = await service.resolve_catalog(orchestrator)

    # Assert
    assert catalog is not None
    assert not answers


def _linear() -> dict[str, object]:
    return {
        "id": "c-1",
        "name": "linear",
        "display_name": "Linear",
        "protocol": "mcp",
        "status": {"is_ready": True},
        "tools": [],
    }


async def _stale_copy_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fetch: Any
) -> tuple[Any, Any]:
    """A process with a copy on disk from before a connector was connected."""
    from tests.conftest import build_test_vibe_config
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server.connector_catalog import (
        ConnectorCatalogService,
        SharedConnectorCatalog,
    )

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    cache_path = tmp_path / "connectors.json"
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True)
    )

    async def before(
        _base_url: str, _credentials: object, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {"connectors": []}

    await ConnectorCatalogService(
        SharedConnectorCatalog(cache_path=cache_path, fetch_bootstrap=before),
        implicit_source_enabled=False,
    ).resolve_catalog(orchestrator)
    service = ConnectorCatalogService(
        SharedConnectorCatalog(cache_path=cache_path, fetch_bootstrap=fetch),
        implicit_source_enabled=False,
    )
    return service, orchestrator


@pytest.mark.asyncio
async def test_a_session_opens_on_the_copy_and_the_warm_up_reaches_it_later(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A copy on disk from before the user connected a connector,
      and the process's warm-up fetch still running.
    *Do*: Load a session's catalog, then let the warm-up answer.
    *Assert*: The load answers with the copy at once, and the session's
      follower has the new catalog due once the warm-up lands.
    """
    from vibe.app_server._remote_follower import RemoteFollower

    # Prepare
    answer = asyncio.Event()

    async def fetch(
        _base_url: str, _credentials: object, _builtins: tuple[str, ...] = ()
    ) -> object:
        await answer.wait()
        return {"connectors": [_linear()]}

    service, orchestrator = await _stale_copy_service(monkeypatch, tmp_path, fetch)
    warming = asyncio.create_task(service.warm_up(orchestrator))
    await asyncio.sleep(0.01)

    # Do
    opened = await asyncio.wait_for(service.resolve_catalog(orchestrator), timeout=1)
    followed = await service.follow(orchestrator)
    assert followed is not None
    follower = RemoteFollower(followed, loaded=opened)
    due_before = follower.due
    answer.set()
    await warming

    # Assert
    assert opened is not None and opened.connectors == ()
    assert not due_before
    changed = follower.take()
    assert changed is not None
    assert [c.alias for c in changed.connectors] == ["linear"]


@pytest.mark.asyncio
async def test_a_failed_warm_up_is_retried_by_the_next_session_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A recent copy on disk from before the user connected a
      connector, and a warm-up fetch that fails.
    *Do*: Open a session on the copy (which checks it).
    *Assert*: The check fetches again rather than trusting the copy for ten
      minutes, and the session's follower has the new catalog due.
    """
    from vibe.app_server._remote_follower import RemoteFollower
    from vibe.app_server.connector_catalog import ConnectorCatalogError

    # Prepare
    answers: list[object] = [OSError("connectors down"), {"connectors": [_linear()]}]

    async def fetch(
        _base_url: str, _credentials: object, _builtins: tuple[str, ...] = ()
    ) -> object:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    service, orchestrator = await _stale_copy_service(monkeypatch, tmp_path, fetch)
    with pytest.raises(ConnectorCatalogError):
        await service.warm_up(orchestrator)

    # Do
    opened = await service.read_cached_catalog(orchestrator)
    followed = await service.follow(orchestrator)
    assert followed is not None
    follower = RemoteFollower(followed, loaded=opened)
    # The fetch answering is not the end of the refresh: the catalog is then
    # built in a worker thread, which a loaded runner can make slow.
    async with asyncio.timeout(5):
        while not follower.due:
            await asyncio.sleep(0.01)

    # Assert
    assert opened is not None and opened.connectors == ()
    changed = follower.take()
    assert changed is not None
    assert [c.alias for c in changed.connectors] == ["linear"]
