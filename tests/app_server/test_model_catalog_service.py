from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
from pathlib import Path
import time

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.process_services import (
    FakeModelCatalogServer,
    locked_catalog,
    runnable_catalog,
)
from vibe.app_server._model_catalog_service import (
    LOCKED_MODEL_REFRESH_AFTER_S,
    ModelCatalogKey,
    ModelCatalogService,
)
from vibe.core.model_catalog.cache import load_cached_model_catalog
from vibe.core.utils.remote_cache import RefreshPolicy

_POLICY = RefreshPolicy(
    refresh_after_s=600,
    max_age_s=30 * 24 * 3600,
    retry_after_s=30,
    max_retry_after_s=600,
    fetch_timeout_s=5,
)


async def _key() -> ModelCatalogKey:
    key = await ModelCatalogKey.of(
        build_test_vibe_config(enable_model_catalog=True), None
    )
    assert key is not None
    return key


def _service(
    server: FakeModelCatalogServer, clock: list[float] | None = None
) -> ModelCatalogService:
    if clock is None:
        return ModelCatalogService(fetch=server.fetch, policy=_POLICY)
    return ModelCatalogService(
        fetch=server.fetch, policy=_POLICY, clock=lambda: clock[0]
    )


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_concurrent_sessions_share_one_fetch(config_dir: Path) -> None:
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"))
    server.release = asyncio.Event()
    service = _service(server)

    pending = [asyncio.create_task(service.ensure(await _key())) for _ in range(3)]
    await asyncio.sleep(0)
    server.release.set()
    held = await asyncio.gather(*pending)

    assert server.fetches == 1
    assert all(catalog == runnable_catalog("fetched-model") for catalog in held)
    await service.close()


@pytest.mark.asyncio
async def test_a_restarted_service_serves_the_catalog_from_disk_with_no_request(
    config_dir: Path,
) -> None:
    earlier_server = FakeModelCatalogServer()
    earlier_server.serve(runnable_catalog("fetched-model"))
    earlier = _service(earlier_server)
    await earlier.ensure(await _key())
    await earlier.close()
    server = FakeModelCatalogServer()
    fresh = _service(server)

    held = await fresh.ensure(await _key())

    assert server.fetches == 0
    assert held == runnable_catalog("fetched-model")
    await fresh.close()


@pytest.mark.asyncio
async def test_an_old_catalog_is_served_at_once_and_refreshed_in_the_background(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    earlier_server = FakeModelCatalogServer()
    earlier_server.serve(runnable_catalog("old-model"))
    earlier = _service(earlier_server, clock)
    await earlier.ensure(await _key())
    await earlier.close()
    clock[0] += _POLICY.refresh_after_s + 1
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("new-model"))
    server.release = asyncio.Event()
    fresh = _service(server, clock)

    served = await asyncio.wait_for(fresh.ensure(await _key()), timeout=1)

    assert served == runnable_catalog("old-model")
    await _until(lambda: server.fetches == 1)
    server.release.set()
    key = await _key()
    await _until(lambda: fresh.followed(key).held() == runnable_catalog("new-model"))
    await fresh.close()


@pytest.mark.asyncio
async def test_a_failure_backs_off_for_later_sessions(config_dir: Path) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(None, runnable_catalog("fetched-model"))
    service = _service(server, clock)

    assert await service.ensure(await _key()) is None
    # Later sessions within the backoff do not hit the endpoint again.
    assert await service.ensure(await _key()) is None
    assert await service.ensure(await _key()) is None
    assert server.fetches == 1

    clock[0] += _POLICY.retry_after_s
    assert await service.ensure(await _key()) == runnable_catalog("fetched-model")
    assert server.fetches == 2
    await service.close()


@pytest.mark.asyncio
async def test_a_failed_refresh_behind_a_held_catalog_still_serves_it(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"), None)
    service = _service(server, clock)
    await service.ensure(await _key())
    clock[0] += _POLICY.refresh_after_s + 1

    served = await service.ensure(await _key())
    await _until(lambda: server.fetches == 2)
    await asyncio.sleep(0.05)

    assert served == runnable_catalog("fetched-model")
    await service.close()


@pytest.mark.asyncio
async def test_a_catalog_for_an_account_signed_out_during_its_fetch_is_not_held(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CLI OIDC sign-in during the fetch makes its answer another account's:
    # neither held for the key it started under nor written to disk.
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"))
    server.release = asyncio.Event()
    service = _service(server)
    key = await _key()
    signed_in = [True]
    monkeypatch.setattr(ModelCatalogKey, "is_current", lambda _self: signed_in[0])

    pending = asyncio.create_task(service.ensure(key))
    await asyncio.sleep(0)
    signed_in[0] = False
    server.release.set()

    assert await pending is None
    signed_in[0] = True
    assert await service.load(key) is None
    assert load_cached_model_catalog(key.disk_key) is None
    await service.close()


@pytest.mark.asyncio
async def test_each_endpoint_gets_its_own_catalog(config_dir: Path) -> None:
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"))
    service = _service(server)
    key = await _key()
    other = ModelCatalogKey(key.credential, "https://other.test/v1", key.credentials)

    await service.ensure(key)
    await service.ensure(other)
    await service.ensure(key)

    assert server.api_bases == [key.api_base, "https://other.test/v1"]
    assert key.disk_key != other.disk_key
    await service.close()


@pytest.mark.asyncio
async def test_a_check_of_an_old_catalog_refreshes_it_and_tells_listeners(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("locked-model"), runnable_catalog("unlocked-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    changed: list[ModelCatalogKey] = []
    service.on_changed(changed.append)
    clock[0] += _POLICY.refresh_after_s + 1

    await service.check(key)

    assert await service.load(key) == runnable_catalog("locked-model")
    await _until(lambda: changed == [key])
    assert await service.load(key) == runnable_catalog("unlocked-model")
    assert server.fetches == 2
    await service.close()


@pytest.mark.asyncio
async def test_a_check_of_a_recent_catalog_fetches_nothing(config_dir: Path) -> None:
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"))
    service = _service(server)
    key = await _key()
    await service.ensure(key)

    await service.check(key)
    await asyncio.sleep(0)

    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_a_load_of_an_old_catalog_fetches_nothing(config_dir: Path) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("fetched-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += _POLICY.refresh_after_s + 1

    assert await service.load(key) == runnable_catalog("fetched-model")
    await asyncio.sleep(0)

    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_a_check_with_no_catalog_held_fetches_nothing(config_dir: Path) -> None:
    server = FakeModelCatalogServer()
    service = _service(server)

    await service.check(await _key())
    await asyncio.sleep(0)

    assert server.fetches == 0
    await service.close()


@pytest.mark.asyncio
async def test_a_check_for_a_locked_model_refreshes_the_catalog_after_30s(
    config_dir: Path,
) -> None:
    # A plan upgrade unlocks it: the user back from checkout must not wait
    # out the full refresh interval.
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"), runnable_catalog("paid-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

    await service.checked_for_locked_model(key, timeout_s=math.inf)

    assert server.fetches == 2
    assert await service.load(key) == runnable_catalog("paid-model")
    await service.close()


@pytest.mark.asyncio
async def test_a_plain_check_keeps_a_catalog_listing_locked_models_for_the_full_interval(
    config_dir: Path,
) -> None:
    # Every plan below the top one lists models it locks: a session on a
    # runnable one must not refetch every 30 s.
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

    await service.check(key)
    await service._cache.settled(key, timeout_s=1.0)

    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_a_picker_check_refreshes_a_catalog_showing_an_upsell_after_30s(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"), runnable_catalog("paid-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

    await service.check_for_picker(key)
    await _until(lambda: server.fetches == 2)

    await service.close()


@pytest.mark.asyncio
async def test_a_picker_check_keeps_a_catalog_locking_nothing_for_the_full_interval(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("free-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

    await service.check_for_picker(key)
    await service._cache.settled(key, timeout_s=1.0)

    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_a_picker_opening_fetches_a_catalog_showing_an_upsell_at_once(
    config_dir: Path,
) -> None:
    # The user may come back from upgrading their plan seconds after the last
    # fetch: the picker opening does not wait out the 30 s.
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"), runnable_catalog("paid-model"))
    service = _service(server)
    key = await _key()
    await service.ensure(key)

    await service.refreshed_for_picker(key)

    assert server.fetches == 2
    assert await service.load(key) == runnable_catalog("paid-model")
    await service.close()


@pytest.mark.asyncio
async def test_each_picker_opening_fetches_a_catalog_showing_an_upsell(
    config_dir: Path,
) -> None:
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"))
    service = _service(server)
    key = await _key()
    await service.ensure(key)

    await service.refreshed_for_picker(key)
    await service.refreshed_for_picker(key)

    assert server.fetches == 3
    await service.close()


@pytest.mark.asyncio
async def test_a_picker_opening_keeps_a_catalog_locking_nothing_for_the_full_interval(
    config_dir: Path,
) -> None:
    clock = [time.time()]
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("free-model"))
    service = _service(server, clock)
    key = await _key()
    await service.ensure(key)
    clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

    await service.refreshed_for_picker(key)
    await service._cache.settled(key, timeout_s=1.0)

    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_a_picker_opening_that_fails_to_fetch_keeps_the_held_catalog(
    config_dir: Path,
) -> None:
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"), None)
    service = _service(server)
    key = await _key()
    await service.ensure(key)

    await service.refreshed_for_picker(key)

    assert server.fetches == 2
    assert await service.load(key) == locked_catalog("paid-model")
    await service.close()


@pytest.mark.asyncio
async def test_a_picker_opening_with_no_catalog_held_fetches_nothing(
    config_dir: Path,
) -> None:
    server = FakeModelCatalogServer()
    service = _service(server)

    await service.refreshed_for_picker(await _key())

    assert server.fetches == 0
    await service.close()
