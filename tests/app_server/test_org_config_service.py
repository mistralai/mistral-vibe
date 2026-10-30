from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.stubs.process_services import FakeOrgConfigServer
from vibe.app_server._org_config_service import (
    ORG_CONFIG_POLICY,
    DiskOrgConfigStore,
    OrgConfigKey,
    OrgConfigService,
)
from vibe.core.config import managed_config_cache
from vibe.core.config.admin_config import AdminConfigOutcome
from vibe.core.utils.remote_cache import Cached, CacheStore


class _Store:
    def __init__(self, entry: Cached[str] | None = None) -> None:
        self.entry = entry
        self.saved: list[str] = []

    def load(self, key: OrgConfigKey) -> Cached[str] | None:
        return self.entry

    def save(self, key: OrgConfigKey, entry: Cached[str]) -> None:
        self.saved.append(entry.value)
        self.entry = entry


_KEY = OrgConfigKey("user", base_url="https://vibe.test", api_key="key")
_NOW = 1_000_000.0


def _service(
    server: FakeOrgConfigServer,
    store: CacheStore[OrgConfigKey, str],
    *,
    first_wait_s: float = 1.0,
) -> OrgConfigService:
    return OrgConfigService(
        fetch=server.fetch,
        store=store,
        policy=replace(ORG_CONFIG_POLICY, fetch_timeout_s=first_wait_s),
        first_wait_s=first_wait_s,
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_a_held_copy_is_served_without_a_fetch() -> None:
    server = FakeOrgConfigServer()
    service = _service(server, _Store(Cached('theme = "nord"\n', stored_at=_NOW)))

    assert await service.ensure(_KEY) is None
    assert await service.load(_KEY) == 'theme = "nord"\n'
    assert server.fetches == 0
    await service.close()


@pytest.mark.asyncio
async def test_with_nothing_held_a_start_waits_for_the_first_answer() -> None:
    server = FakeOrgConfigServer()
    server.serve('theme = "nord"\n')
    store = _Store()
    service = _service(server, store)

    assert await service.ensure(_KEY) is None

    assert await service.load(_KEY) == 'theme = "nord"\n'
    assert store.saved == ['theme = "nord"\n']
    await service.close()


@pytest.mark.asyncio
async def test_a_first_answer_slower_than_the_wait_is_not_waited_for() -> None:
    server = FakeOrgConfigServer()
    server.serve('theme = "nord"\n')
    server.release = asyncio.Event()
    service = _service(server, _Store(), first_wait_s=0.05)

    failure = await service.ensure(_KEY)

    assert failure is not None
    assert failure.outcome is AdminConfigOutcome.FETCH_FAILED
    server.release.set()
    await service.close()


@pytest.mark.asyncio
async def test_an_org_that_enforces_nothing_is_held_as_an_empty_policy() -> None:
    server = FakeOrgConfigServer()
    server.serve("")
    service = _service(server, _Store())

    assert await service.ensure(_KEY) is None
    assert await service.load(_KEY) == ""
    await service.close()


@pytest.mark.asyncio
async def test_an_unparsable_answer_keeps_the_held_copy() -> None:
    server = FakeOrgConfigServer()
    server.serve('active_model = "unterminated\n')
    store = _Store(Cached('theme = "nord"\n', stored_at=_NOW))
    service = _service(server, store)

    refreshed = await service.refresh(_KEY)

    assert not isinstance(refreshed, str)
    assert refreshed.outcome is AdminConfigOutcome.PARSE_FAILED
    assert await service.load(_KEY) == 'theme = "nord"\n'
    assert store.saved == []
    await service.close()


@pytest.mark.asyncio
async def test_a_refresh_fetches_however_old_the_copy_is() -> None:
    server = FakeOrgConfigServer()
    server.serve('theme = "dark"\n')
    service = _service(server, _Store(Cached('theme = "nord"\n', stored_at=_NOW)))

    assert await service.refresh(_KEY) == 'theme = "dark"\n'
    assert server.fetches == 1
    await service.close()


@pytest.mark.asyncio
async def test_without_a_credential_there_is_nothing_to_refresh() -> None:
    service = _service(FakeOrgConfigServer(), _Store())

    refreshed = await service.refresh(None)

    assert not isinstance(refreshed, str)
    assert refreshed.outcome is AdminConfigOutcome.NO_API_KEY
    await service.close()


@pytest.mark.asyncio
async def test_a_failed_fetch_is_remembered_until_its_retry() -> None:
    server = FakeOrgConfigServer()
    service = _service(server, _Store())

    assert await service.ensure(_KEY) is not None
    assert await service.ensure(_KEY) is not None

    assert server.fetches == 1
    assert service.failed_recently(_KEY)
    await service.close()


@pytest.mark.asyncio
async def test_a_follower_reads_a_refreshed_answer() -> None:
    server = FakeOrgConfigServer()
    server.serve('theme = "dark"\n')
    service = _service(server, _Store(Cached('theme = "nord"\n', stored_at=_NOW)))
    await service.ensure(_KEY)
    followed = service.followed(_KEY)
    assert followed.held() == 'theme = "nord"\n'

    await service.refresh(_KEY)

    assert followed.held() == 'theme = "dark"\n'
    await service.close()


@pytest.mark.asyncio
async def test_a_new_endpoint_is_a_new_org_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Account reconciliation can move the endpoint under the same API key: the
    # policy held for the old endpoint is not served for the new one.
    monkeypatch.setattr(
        managed_config_cache,
        "MANAGED_CONFIG_CACHE_FILE",
        SimpleNamespace(path=tmp_path / "managed_config_cache.json"),
    )
    server = FakeOrgConfigServer()
    server.serve('theme = "old"\n')
    service = _service(server, DiskOrgConfigStore())
    await service.ensure(_KEY)
    moved = replace(_KEY, base_url="https://moved.example.test")

    assert await service.load(moved) is None
    assert DiskOrgConfigStore().load(moved) is None
    await service.close()
