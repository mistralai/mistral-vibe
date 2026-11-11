from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.process_services import FakeModelCatalogServer, runnable_catalog
from vibe.app_server._model_catalog_service import ModelCatalogKey, ModelCatalogService
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
        build_test_vibe_config(experimental_enable_model_catalog=True), None
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


async def _until(predicate: object, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():  # type: ignore[operator]
        if time.monotonic() > deadline:
            raise AssertionError("condition never met")
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
    assert service.failures.failed
    # Later sessions within the backoff do not hit the endpoint again.
    assert await service.ensure(await _key()) is None
    assert await service.ensure(await _key()) is None
    assert server.fetches == 1

    clock[0] += _POLICY.retry_after_s
    assert await service.ensure(await _key()) == runnable_catalog("fetched-model")
    assert server.fetches == 2
    await service.close()


@pytest.mark.asyncio
async def test_a_failed_refresh_behind_a_held_catalog_keeps_later_sessions_waiting(
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
    assert not service.failures.failed
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
    # Not a failure of the endpoint: the new account's sessions still wait.
    assert not service.failures.failed
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
