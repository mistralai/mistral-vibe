"""The usage-telemetry setting a client shows without a session open.

``config/telemetry/read`` says what a session opened now would run and whether
the user config decides it; ``config/telemetry/update`` writes the user config,
refused while the environment or the org's admin config decides it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import tomllib
from typing import Any, cast

import pytest
import tomli_w

from tests.stubs.process_services import FakeOrgConfigServer
from vibe.app_server._config_changes import ConfigChanges
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._model import validate_wire
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    SERVER_METHODS,
    ClientCapabilities,
    ClientInfo,
    Notification,
    SessionOptions,
    TelemetrySettingReadResponse,
    TelemetrySettingUpdateResponse,
    TelemetrySettingView,
)
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.managed_config_cache import (
    managed_config_cache_key,
    store_cached_managed_config,
)


class _RecordingHost:
    def __init__(self) -> None:
        self.reloads = 0

    async def archive_open(self, params: Any) -> None:
        return None

    async def reload_config_files(self) -> None:
        self.reloads += 1


def _handler(
    services: ProcessServices,
    *,
    open_sessions: OpenSessions | None = None,
    config_changes: ConfigChanges | None = None,
) -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        org_config=services.org_config,
        worktrees=services.worktrees,
        experiments=services.experiments,
        model_catalog=services.model_catalog,
        open_sessions=open_sessions,
        config_changes=config_changes,
    )


async def _read(handler: HostRequestHandler) -> TelemetrySettingView:
    result = await handler.dispatch("config/telemetry/read", {})
    return cast(TelemetrySettingReadResponse, result.response).setting


async def _update(
    handler: HostRequestHandler, *, enabled: bool
) -> TelemetrySettingUpdateResponse:
    result = await handler.dispatch("config/telemetry/update", {"enabled": enabled})
    return cast(TelemetrySettingUpdateResponse, result.response)


def _user_config(config_dir: Path) -> dict[str, Any]:
    return tomllib.loads((config_dir / "config.toml").read_text())


def test_the_methods_are_advertised() -> None:
    assert {
        "config/subscribe",
        "config/telemetry/read",
        "config/telemetry/update",
    } <= set(SERVER_METHODS)


@pytest.mark.asyncio
async def test_reads_the_user_config_value_as_writable(
    config_dir: Path, process_services: ProcessServices
) -> None:
    setting = await _read(_handler(process_services))

    # The test config turns telemetry off in the user file.
    assert setting == TelemetrySettingView(
        enabled=False, origin="user-toml", locked=False
    )


@pytest.mark.asyncio
async def test_reads_the_default_when_no_file_sets_it(
    config_dir: Path, process_services: ProcessServices
) -> None:
    config = _user_config(config_dir)
    del config["enable_telemetry"]

    (config_dir / "config.toml").write_text(tomli_w.dumps(config))

    setting = await _read(_handler(process_services))

    assert setting == TelemetrySettingView(enabled=True, origin="default", locked=False)


@pytest.mark.asyncio
async def test_update_writes_the_user_config_and_tells_open_sessions(
    config_dir: Path, process_services: ProcessServices
) -> None:
    open_sessions = OpenSessions()
    host = _RecordingHost()
    open_sessions.register(cast(Any, host))
    changes = ConfigChanges()
    told: list[None] = []
    changes.subscribe(lambda: told.append(None))
    handler = _handler(
        process_services, open_sessions=open_sessions, config_changes=changes
    )

    response = await _update(handler, enabled=True)

    assert response.rejected is False
    assert response.failures == []
    assert response.setting == TelemetrySettingView(
        enabled=True, origin="user-toml", locked=False
    )
    assert _user_config(config_dir)["enable_telemetry"] is True
    assert host.reloads == 1
    assert told == [None]
    assert (await _read(handler)).enabled is True


@pytest.mark.asyncio
async def test_the_environment_variable_locks_the_setting(
    config_dir: Path, process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_ENABLE_TELEMETRY", "1")
    changes = ConfigChanges()
    told: list[None] = []
    changes.subscribe(lambda: told.append(None))
    handler = _handler(process_services, config_changes=changes)

    assert await _read(handler) == TelemetrySettingView(
        enabled=True, origin="environment", locked=True
    )
    response = await _update(handler, enabled=False)

    assert response.rejected is True
    assert response.reason is not None
    assert "VIBE_ENABLE_TELEMETRY" in response.reason
    assert response.setting.enabled is True
    # Nothing written, nobody told: the user file keeps its own value.
    assert _user_config(config_dir)["enable_telemetry"] is False
    assert told == []


@pytest.mark.asyncio
async def test_the_org_admin_config_locks_the_setting(
    config_dir: Path, process_services: ProcessServices
) -> None:
    store_cached_managed_config(
        managed_config_cache_key(VibeConfigSchema()), "enable_telemetry = true\n"
    )
    handler = _handler(process_services)

    assert await _read(handler) == TelemetrySettingView(
        enabled=True, origin="admin", locked=True
    )
    response = await _update(handler, enabled=False)

    assert response.rejected is True
    assert response.reason == "Usage telemetry is enforced by your organization."
    assert _user_config(config_dir)["enable_telemetry"] is False


@pytest.mark.asyncio
async def test_a_project_config_does_not_change_the_user_setting(
    config_dir: Path, process_services: ProcessServices, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    (project / ".vibe").mkdir(parents=True)
    (project / ".vibe" / "config.toml").write_text("enable_telemetry = true\n")
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user", "project"), cwd=project),
        org_config=process_services.org_config,
        worktrees=process_services.worktrees,
        experiments=process_services.experiments,
        model_catalog=process_services.model_catalog,
    )

    assert await _read(handler) == TelemetrySettingView(
        enabled=False, origin="user-toml", locked=False
    )


async def _connect(process: HarnessProcess) -> AppServerClient:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    await client.initialize(ClientInfo(name="telemetry-test", version="1"))
    await client.notify("initialized")
    return client


async def _next_config_changed(incoming: AsyncIterator[object]) -> Notification:
    async def wait() -> Notification:
        async for message in incoming:
            if isinstance(message, Notification) and message.method == "config/changed":
                return message
        raise AssertionError("Connection closed before config/changed")

    return await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_a_subscribed_connection_is_told_of_org_config_and_setting_changes(
    config_dir: Path,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(services=process_services)
    client = await _connect(process)
    incoming = client.incoming()
    try:
        key = OrgConfigService.key_for_config(VibeConfigSchema())
        assert key is not None
        # The first value held is never a change: hold one before subscribing.
        org_server.serve("")
        await process_services.org_config.refresh(key)
        await client.request("config/subscribe", {})
        org_server.serve("enable_telemetry = true\n")
        await process_services.org_config.refresh(key)

        await _next_config_changed(incoming)
        locked = validate_wire(
            TelemetrySettingReadResponse,
            await client.request("config/telemetry/read", {}),
        )
        assert locked.setting == TelemetrySettingView(
            enabled=True, origin="admin", locked=True
        )

        org_server.serve("")
        await process_services.org_config.refresh(key)
        await _next_config_changed(incoming)
        updated = validate_wire(
            TelemetrySettingUpdateResponse,
            await client.request("config/telemetry/update", {"enabled": True}),
        )
        assert updated.setting == TelemetrySettingView(
            enabled=True, origin="user-toml", locked=False
        )
        await _next_config_changed(incoming)
    finally:
        await incoming.aclose()
        await client.close()
        await process.close()


@pytest.mark.asyncio
async def test_an_open_session_takes_the_setting_written_by_another_connection(
    config_dir: Path,
    tmp_path: Path,
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(services=process_services)
    settings = await _connect(process)
    session_client, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    session_rpc = AppServerClient(session_client, run_peer=harness.serve)
    session = await AppServerSession.start(
        session_rpc,
        client_info=ClientInfo(name="telemetry-test", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(cwd=str(tmp_path)),
    )
    try:
        before = await session_rpc.request(
            "config/read", {"sessionId": session.session_id}
        )
        assert before["config"]["crashReportsEnabled"] is False

        await settings.request("config/telemetry/update", {"enabled": True})

        after = await session_rpc.request(
            "config/read", {"sessionId": session.session_id}
        )
        assert after["config"]["crashReportsEnabled"] is True
    finally:
        await session.close()
        await settings.close()
        await process.close()
