"""Server-level test: clear followed by ready/wait through the protocol."""

from __future__ import annotations

import asyncio

import pytest

from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    ClientInfo,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionReadResponse,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
    SessionStartParams,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair


@pytest.mark.asyncio
async def test_server_clear_then_ready_wait_does_not_hang() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from tests.app_server.test_unified_harness_backend_adapter import (
        _harness_backend_host,
    )

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport, session_backend_host_factory=lambda _: _harness_backend_host()
    )
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id

        cleared = SessionHistoryClearResponse.model_validate(
            await client.request(
                "session/history/clear",
                SessionHistoryClearParams(session_id=session_id),
            )
        )
        new_session_id = cleared.state.session.id
        assert new_session_id != session_id

        result = await asyncio.wait_for(
            client.request(
                "session/ready/wait", SessionReadyWaitParams(session_id=new_session_id)
            ),
            timeout=10.0,
        )
        response = SessionReadyWaitResponse.model_validate(result)
        assert response.ready is True
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_server_clear_then_ready_wait_with_slow_admin_config(
    tmp_path, monkeypatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from tests.app_server.test_unified_harness_backend_adapter import (
        _harness_backend_host,
    )
    from vibe.app_server import _admin_config
    from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult

    fetch_started = asyncio.Event()

    async def slow_fetch(_base_url: str, _api_key: str) -> ManagedConfigResult:
        fetch_started.set()
        await asyncio.sleep(0.5)
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml='theme = "nord"\n')
        )

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", slow_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport, session_backend_host_factory=lambda _: _harness_backend_host()
    )
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id

        cleared = SessionHistoryClearResponse.model_validate(
            await client.request(
                "session/history/clear",
                SessionHistoryClearParams(session_id=session_id),
            )
        )
        new_session_id = cleared.state.session.id

        result = await asyncio.wait_for(
            client.request(
                "session/ready/wait", SessionReadyWaitParams(session_id=new_session_id)
            ),
            timeout=10.0,
        )
        response = SessionReadyWaitResponse.model_validate(result)
        assert response.ready is True
    finally:
        await server.close()
