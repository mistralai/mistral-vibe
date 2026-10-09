from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
import vibe.core.agent_loop._loop as agent_loop_module
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response


def _catalog(model_id: str = "mistral-medium-3.5") -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({
        "models": [{"id": model_id, "label": model_id, "recommended": True}]
    })
    assert parsed is not None
    return parsed


def _loop(**kwargs: Any):
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    return build_test_agent_loop(config=config, **kwargs)


@pytest.mark.asyncio
async def test_flag_disabled_starts_no_fetch_and_never_defers() -> None:
    config = build_test_vibe_config()  # flag defaults to False
    loop = build_test_agent_loop(config=config, await_model_catalog=True)

    loop.start_fetch_model_catalog()

    assert loop._model_catalog_task is None
    assert loop.awaiting_experiment_model is False
    assert loop.model_catalog is None


@pytest.mark.asyncio
async def test_first_ever_fetch_defers_first_turn_until_it_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog()
    monkeypatch.setattr(
        agent_loop_module,
        "fetch_and_cache_model_catalog",
        AsyncMock(return_value=catalog),
    )
    loop = _loop(await_model_catalog=True)

    loop.start_fetch_model_catalog()

    assert loop.awaiting_experiment_model is True
    task = loop._model_catalog_task
    assert task is not None
    await task
    assert loop.awaiting_experiment_model is False
    assert loop.model_catalog is not None
    assert loop.model_catalog.models[0].id == "mistral-medium-3.5"


@pytest.mark.asyncio
async def test_failed_first_fetch_leaves_local_config_and_clears_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module, "fetch_and_cache_model_catalog", AsyncMock(return_value=None)
    )
    loop = _loop(await_model_catalog=True)

    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None
    await task

    assert loop.model_catalog is None
    assert loop.awaiting_experiment_model is False


@pytest.mark.asyncio
async def test_cached_catalog_applies_immediately_and_hot_swaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _catalog("cached-model")
    fresh = _catalog("fresh-model")
    monkeypatch.setattr(
        agent_loop_module,
        "fetch_and_cache_model_catalog",
        AsyncMock(return_value=fresh),
    )
    loop = _loop(model_catalog_state=cached)

    loop.start_fetch_model_catalog()

    # Available without waiting on the network, and no first-turn deferral.
    assert loop.model_catalog is cached
    assert loop.awaiting_experiment_model is False
    task = loop._model_catalog_task
    assert task is not None
    await task
    assert loop.model_catalog is fresh


@pytest.mark.asyncio
async def test_deferred_first_turn_waits_for_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()
    catalog = _catalog()

    async def _slow_fetch(_config: Any) -> ModelCatalogResponse:
        await released.wait()
        return catalog

    monkeypatch.setattr(agent_loop_module, "fetch_and_cache_model_catalog", _slow_fetch)
    loop = _loop(await_model_catalog=True)

    loop.start_fetch_model_catalog()
    ready = asyncio.create_task(loop._await_deferred_init())
    await asyncio.sleep(0)
    assert not ready.done()

    released.set()
    await ready

    assert loop.model_catalog is catalog


@pytest.mark.asyncio
async def test_failed_refresh_retains_the_cached_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _catalog("cached-model")
    monkeypatch.setattr(
        agent_loop_module, "fetch_and_cache_model_catalog", AsyncMock(return_value=None)
    )
    loop = _loop(model_catalog_state=cached)

    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None
    await task

    assert loop.model_catalog is cached


@pytest.mark.asyncio
async def test_aclose_cancels_an_inflight_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()

    async def _slow_fetch(_config: Any) -> ModelCatalogResponse:
        await released.wait()
        return _catalog()

    monkeypatch.setattr(agent_loop_module, "fetch_and_cache_model_catalog", _slow_fetch)
    loop = _loop()

    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None and not task.done()

    await loop.aclose()

    assert task.cancelled()


@pytest.mark.asyncio
async def test_rebind_discards_deferral_without_scheduling_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module,
        "fetch_and_cache_model_catalog",
        AsyncMock(return_value=_catalog()),
    )
    loop = _loop(await_model_catalog=True, is_subagent=True)
    loop.session_logger = MagicMock()

    loop.start_fetch_model_catalog()
    first = loop._model_catalog_task
    assert first is not None
    await first

    loop.rebind_to_session(
        "resumed-session",
        Path("/tmp/vibe-test-session"),
        [],
        session_metadata=MagicMock(),
    )

    # The rebind commit section stays pure assignments: the deferral is
    # discarded here, and the caller kicks the next fetch after it returns.
    assert loop._await_model_catalog is False
    assert loop._model_catalog_task is first


@pytest.mark.asyncio
async def test_rebind_discards_deferral_but_lets_inflight_fetch_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()
    catalog = _catalog()

    async def _slow_fetch(_config: Any) -> ModelCatalogResponse:
        await released.wait()
        return catalog

    monkeypatch.setattr(agent_loop_module, "fetch_and_cache_model_catalog", _slow_fetch)
    loop = _loop(await_model_catalog=True, is_subagent=True)
    loop.session_logger = MagicMock()

    loop.start_fetch_model_catalog()
    assert loop.awaiting_experiment_model is True

    loop.rebind_to_session(
        "resumed-session",
        Path("/tmp/vibe-test-session"),
        [],
        session_metadata=MagicMock(),
    )

    assert loop.awaiting_experiment_model is False
    released.set()
    task = loop._model_catalog_task
    assert task is not None
    await task
    assert loop.model_catalog is catalog


@pytest.mark.asyncio
async def test_clear_after_a_successful_fetch_does_not_defer_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module,
        "fetch_and_cache_model_catalog",
        AsyncMock(return_value=_catalog()),
    )
    loop = _loop(await_model_catalog=True)
    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None
    await task
    assert loop.model_catalog is not None

    await loop._reset_session()

    # The catalog is already in hand, so the new session's first turn runs
    # immediately; the kicked refresh stays in the background.
    assert loop._await_model_catalog is False
    assert loop.awaiting_experiment_model is False
    assert loop._model_catalog_task is not task


@pytest.mark.asyncio
async def test_cancelled_fetch_skips_new_session_emit() -> None:
    # Rapid start/stop cancels the catalog fetch mid-init; wait_until_ready
    # must not emit vibe.new_session against the half-initialized session.
    loop = _loop(await_model_catalog=True)
    calls: list[int] = []
    with patch.object(loop, "emit_new_session_telemetry", lambda: calls.append(1)):

        async def _never() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_never())
        loop._model_catalog_task = task
        loop._pending_new_session_telemetry = True
        task.cancel()
        try:
            await loop.wait_until_ready()
        finally:
            await loop.aclose()
    assert calls == []


@pytest.mark.asyncio
async def test_later_sessions_do_not_defer_after_a_successful_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released = asyncio.Event()

    async def _slow_fetch(_config: Any) -> ModelCatalogResponse:
        await released.wait()
        return _catalog()

    monkeypatch.setattr(agent_loop_module, "fetch_and_cache_model_catalog", _slow_fetch)
    loop = _loop(await_model_catalog=True)

    loop.start_fetch_model_catalog()
    first = loop._model_catalog_task
    assert first is not None
    released.set()
    await first
    assert loop.model_catalog is not None
    assert loop._await_model_catalog is False

    # A later session's refresh must hot-swap in the background, never defer.
    released.clear()
    loop.start_fetch_model_catalog()
    second = loop._model_catalog_task
    assert second is not None and second is not first
    assert loop.awaiting_experiment_model is False
    released.set()
    await second


@pytest.mark.asyncio
async def test_later_sessions_do_not_defer_after_a_failed_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module, "fetch_and_cache_model_catalog", AsyncMock(return_value=None)
    )
    loop = _loop(await_model_catalog=True)

    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None
    await task

    assert loop.model_catalog is None
    assert loop._await_model_catalog is False

    loop.start_fetch_model_catalog()
    assert loop.awaiting_experiment_model is False


@pytest.mark.asyncio
async def test_clear_does_not_re_defer_after_a_failed_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strictly once per process: a deployment where the endpoint can never
    # answer (self-hosted without it) must not pay a first-turn wait on every
    # /clear. The fetch still runs; it just never gates the turn again.
    monkeypatch.setattr(
        agent_loop_module, "fetch_and_cache_model_catalog", AsyncMock(return_value=None)
    )
    loop = _loop(await_model_catalog=True)
    loop.start_fetch_model_catalog()
    task = loop._model_catalog_task
    assert task is not None
    await task

    await loop._reset_session()

    assert loop._await_model_catalog is False
    assert loop.awaiting_experiment_model is False
