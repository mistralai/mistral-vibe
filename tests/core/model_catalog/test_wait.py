from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG
from vibe.core.model_catalog import parse_model_catalog_response
import vibe.core.model_catalog.wait as wait_module
from vibe.core.model_catalog.wait import (
    ModelCatalogWait,
    mark_model_catalog_failed,
    model_catalog_failed,
    model_catalog_wait_needed,
)


async def _orchestrator(**fields: Any) -> Any:
    return await build_default_orchestrator({
        "experimental_enable_model_catalog": True,
        **fields,
    })


@pytest.mark.asyncio
async def test_an_unpinned_session_without_a_catalog_waits() -> None:
    assert model_catalog_wait_needed(await _orchestrator(active_model="")) is True


@pytest.mark.asyncio
async def test_a_pin_no_layer_defines_waits() -> None:
    orchestrator = await _orchestrator(active_model="only-in-the-catalog")

    assert model_catalog_wait_needed(orchestrator) is True


@pytest.mark.asyncio
async def test_a_pin_defined_without_the_catalog_does_not_wait() -> None:
    orchestrator = await _orchestrator(active_model=DEFAULT_ACTIVE_MODEL_CONFIG.alias)

    assert model_catalog_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_a_loaded_catalog_means_no_wait() -> None:
    orchestrator = await _orchestrator(active_model="")
    catalog = parse_model_catalog_response({"models": []})
    assert catalog is not None
    await apply_model_catalog(orchestrator, catalog)

    assert model_catalog_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_the_flag_off_means_no_wait() -> None:
    orchestrator = await build_default_orchestrator({"active_model": ""})

    assert model_catalog_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_no_wait_once_a_catalog_fetch_failed_in_this_process() -> None:
    orchestrator = await _orchestrator(active_model="")
    mark_model_catalog_failed()

    assert model_catalog_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_the_wait_ends_with_the_fetch() -> None:
    released = asyncio.Event()
    task = asyncio.create_task(released.wait())
    wait = ModelCatalogWait(task)
    waiter = asyncio.create_task(wait.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    assert wait.done is False

    released.set()
    await waiter

    assert wait.done is True
    assert model_catalog_failed() is False


@pytest.mark.asyncio
async def test_a_fetch_slower_than_the_cap_is_given_up_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = ModelCatalogWait(task)

    await wait.wait()

    assert not task.done()
    assert wait.done is True
    assert model_catalog_failed() is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_cancelled_fetch_is_not_a_failure() -> None:
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = ModelCatalogWait(task)
    task.cancel()

    await wait.wait()

    assert task.cancelled()
    assert model_catalog_failed() is False


@pytest.mark.asyncio
async def test_no_mistral_credential_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibe.core.telemetry.send as telemetry_send

    monkeypatch.setattr(
        telemetry_send, "get_mistral_provider_and_api_key", lambda _config: None
    )

    assert model_catalog_wait_needed(await _orchestrator(active_model="")) is False


@pytest.mark.asyncio
async def test_the_cap_is_recorded_even_with_nobody_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = ModelCatalogWait(task)

    await asyncio.sleep(0.05)

    assert wait.done is True
    assert model_catalog_failed() is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_each_held_fetch_gets_its_own_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A session closed during its fetch leaves nothing behind: a later held
    # fetch still waits for its own result.
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.2)
    first = asyncio.create_task(asyncio.sleep(3600))
    ModelCatalogWait(first)
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    await asyncio.sleep(0.3)
    released = asyncio.Event()
    second = asyncio.create_task(released.wait())
    wait = ModelCatalogWait(second)

    await asyncio.sleep(0.05)

    assert wait.done is False
    assert model_catalog_failed() is False
    released.set()
    await wait.wait()


@pytest.mark.asyncio
async def test_a_discarded_wait_does_not_fail_a_later_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replacing a wait leaves the fetch running. Its cap must not fail the
    # process out from under the wait that replaced it.
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.05)
    first_task = asyncio.create_task(asyncio.sleep(3600))
    first = ModelCatalogWait(first_task)
    first.discard()
    assert first._cap.cancelled()
    first._expire()

    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 30)
    released = asyncio.Event()
    second_task = asyncio.create_task(released.wait())
    second = ModelCatalogWait(second_task)
    await asyncio.sleep(0.1)

    assert first.done is True
    assert second.done is False
    assert model_catalog_failed() is False
    released.set()
    await second.wait()
    first_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first_task
