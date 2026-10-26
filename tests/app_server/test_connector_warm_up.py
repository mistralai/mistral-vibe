"""The app server fetches the connector catalog before any session opens."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vibe.app_server._runtime import HarnessProcess

_start_connector_warm_up = HarnessProcess.start_connector_warm_up


def _process(resolve: Any) -> HarnessProcess:
    process = HarnessProcess()
    process.connector_catalog.resolve_catalog = resolve
    return process


@pytest.mark.asyncio
async def test_the_connector_catalog_is_fetched_at_start() -> None:
    """*Prepare*: A process with no session open.
    *Do*: Start the warm-up.
    *Assert*: The catalog is resolved once, from the process's own config.
    """
    # Prepare
    resolved = asyncio.Event()
    calls: list[object] = []

    async def resolve(orchestrator: object, **_: object) -> None:
        calls.append(orchestrator)
        resolved.set()

    process = _process(resolve)

    # Do
    _start_connector_warm_up(process)
    _start_connector_warm_up(process)
    await asyncio.wait_for(resolved.wait(), timeout=5)
    await process.close()

    # Assert
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_failed_warm_up_is_left_to_the_sessions() -> None:
    """*Prepare*: A process whose catalog fetch fails.
    *Do*: Warm up, then close.
    *Assert*: Nothing is raised: a session's own load reports the failure.
    """

    # Prepare
    async def resolve(_orchestrator: object, **_: object) -> None:
        raise RuntimeError("connectors down")

    process = _process(resolve)

    # Do
    _start_connector_warm_up(process)
    warm_up = process._connector_warm_up
    assert warm_up is not None
    await asyncio.wait_for(asyncio.shield(warm_up), timeout=5)
    await process.close()

    # Assert
    assert warm_up.exception() is None


@pytest.mark.asyncio
async def test_closing_the_process_cancels_the_warm_up() -> None:
    """*Prepare*: A warm-up stuck on a catalog fetch.
    *Do*: Close the process.
    *Assert*: The fetch is cancelled rather than outliving the process.
    """
    # Prepare
    started = asyncio.Event()

    async def resolve(_orchestrator: object, **_: object) -> None:
        started.set()
        await asyncio.Event().wait()

    process = _process(resolve)
    _start_connector_warm_up(process)
    await asyncio.wait_for(started.wait(), timeout=5)
    warm_up = process._connector_warm_up
    assert warm_up is not None

    # Do
    await asyncio.wait_for(process.close(), timeout=5)

    # Assert
    assert warm_up.cancelled()
