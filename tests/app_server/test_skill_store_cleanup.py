from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import respx

from tests.conftest import build_test_vibe_config
from tests.core.skills.registry.test_service_cleanup import _stored_long_ago
from tests.skills.registry.conftest import catalog_lists
from tests.stubs.background_worker import FakeBackgroundWorker
from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._skill_store_cleanup import SkillStoreCleanup
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
)
from vibe.core.skills.registry import _store


@pytest_asyncio.fixture
async def worker() -> AsyncIterator[BackgroundWorker]:
    worker = BackgroundWorker()
    yield worker
    await worker.close()


def _signed_in(_env_var: str) -> PlatformCredentialProvider:
    return ApiKeyPlatformCredentialProvider.from_api_key("session-key")


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_the_worker_process_evicts_an_unused_version_the_registry_serves(
    worker: BackgroundWorker, respx_mock: respx.MockRouter
) -> None:
    _stored_long_ago("cid", 1, 2)
    listing = catalog_lists(respx_mock, cid=2)
    cleanup = SkillStoreCleanup(worker)

    evicted = await cleanup.run(build_test_vibe_config(), [], credentials=_signed_in)

    assert evicted == [("cid", 1)]
    assert worker.pid is not None
    assert [_store.is_materialized_sync("cid", v) for v in (1, 2)] == [False, True]
    assert listing.calls.last.request.headers["authorization"] == "Bearer session-key"


@pytest.mark.asyncio
async def test_a_cleanup_with_nothing_unused_asks_the_registry_nothing(
    respx_mock: respx.MockRouter,
) -> None:
    _stored_long_ago("cid", 1, 2, used_days_ago=1)
    listing = catalog_lists(respx_mock, cid=2)
    cleanup = SkillStoreCleanup(FakeBackgroundWorker())

    evicted = await cleanup.run(build_test_vibe_config(), [], credentials=None)

    assert evicted == []
    assert not listing.called
    assert [_store.is_materialized_sync("cid", v) for v in (1, 2)] == [True, True]


@pytest.mark.asyncio
async def test_a_version_the_registry_no_longer_serves_stays(
    respx_mock: respx.MockRouter,
) -> None:
    _stored_long_ago("cid", 1, 2)
    catalog_lists(respx_mock)
    cleanup = SkillStoreCleanup(FakeBackgroundWorker())

    evicted = await cleanup.run(build_test_vibe_config(), [], credentials=None)

    assert evicted == []
    assert [_store.is_materialized_sync("cid", v) for v in (1, 2)] == [True, True]
