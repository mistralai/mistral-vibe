from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.process_services import (
    FakeModelCatalogServer,
    fake_process_services,
    locked_catalog,
    runnable_catalog,
)
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._model_catalog_service import (
    LOCKED_MODEL_REFRESH_AFTER_S,
    MODEL_CATALOG_POLICY,
    ModelCatalogKey,
)
from vibe.core.config.harness_files import HarnessFilesManager


@pytest.mark.asyncio
async def test_a_refreshed_model_catalog_tells_subscribed_clients_once(
    config_dir: Path,
) -> None:
    # A client re-reads on `config/changed`; that re-read must not start
    # another refresh, or the two would loop.
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("locked-model"), runnable_catalog("unlocked-model"))
    clock = [time.time()]
    services = fake_process_services(
        model_catalog_server=server, clock=lambda: clock[0]
    )
    published: list[None] = []
    services.config_changes.subscribe(lambda: published.append(None))
    key = await ModelCatalogKey.of(
        build_test_vibe_config(experimental_enable_model_catalog=True), None
    )
    assert key is not None
    try:
        await services.model_catalog.ensure(key)
        clock[0] += MODEL_CATALOG_POLICY.refresh_after_s + 1

        await services.model_catalog.check(key)
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.01)
        await services.model_catalog.check(key)
        await asyncio.sleep(0.05)

        assert len(published) == 1
        assert server.fetches == 2
    finally:
        await services.close()


@pytest.mark.asyncio
async def test_a_picker_config_read_refreshes_an_old_model_catalog(
    config_dir: Path,
) -> None:
    # A plan upgrade unlocks models: the picker's read is how an idle client
    # with no open session learns it.
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "experimental_enable_model_catalog = true\n"
        + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    server = FakeModelCatalogServer()
    server.serve(runnable_catalog("locked-model"), runnable_catalog("unlocked-model"))
    clock = [time.time()]
    services = fake_process_services(
        model_catalog_server=server, clock=lambda: clock[0]
    )
    published: list[None] = []
    services.config_changes.subscribe(lambda: published.append(None))
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        org_config=services.org_config,
        worktrees=services.worktrees,
        experiments=services.experiments,
        model_catalog=services.model_catalog,
    )
    key = await ModelCatalogKey.of(
        build_test_vibe_config(experimental_enable_model_catalog=True), None
    )
    assert key is not None
    try:
        await services.model_catalog.ensure(key)
        clock[0] += MODEL_CATALOG_POLICY.refresh_after_s + 1

        await handler.dispatch(
            "config/read", {}, client_info=ClientInfo(name="test", version="0")
        )
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.01)

        assert published == [None]
        assert server.fetches == 2
    finally:
        await services.close()


@pytest.mark.asyncio
async def test_a_picker_read_refreshes_a_catalog_locking_a_model_after_30s(
    config_dir: Path,
) -> None:
    # Back from the upgrade page: the catalog is under a minute old, too recent
    # for a background refresh, and still locks the model the plan now includes.
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "experimental_enable_model_catalog = true\n"
        + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    server = FakeModelCatalogServer()
    server.serve(locked_catalog("paid-model"), runnable_catalog("paid-model"))
    clock = [time.time()]
    services = fake_process_services(
        model_catalog_server=server, clock=lambda: clock[0]
    )
    published: list[None] = []
    services.config_changes.subscribe(lambda: published.append(None))
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        org_config=services.org_config,
        worktrees=services.worktrees,
        experiments=services.experiments,
        model_catalog=services.model_catalog,
    )
    key = await ModelCatalogKey.of(
        build_test_vibe_config(experimental_enable_model_catalog=True), None
    )
    assert key is not None
    client = ClientInfo(name="test", version="0")
    try:
        await services.model_catalog.ensure(key)
        await handler.dispatch("config/read", {}, client_info=client)
        await asyncio.sleep(0.05)
        assert server.fetches == 1
        clock[0] += LOCKED_MODEL_REFRESH_AFTER_S + 1

        await handler.dispatch("config/read", {}, client_info=client)
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.01)

        assert server.fetches == 2
        assert published == [None]
        assert await services.model_catalog.load(key) == runnable_catalog("paid-model")
    finally:
        await services.close()
