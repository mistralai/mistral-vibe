from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
import sys
import tomllib
from typing import Any, cast

import pytest
import tomli_w

from tests.app_server.backend_contract.conftest import BackendContractConnection
from vibe.app_server._legacy_session_backend import LegacySessionBackend
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import (
    SessionBackendError,
    SessionEventSubscription,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import MCPAuthorizationRequiredEvent
from vibe.app_server.models import MCPSourceStatus
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ClientInfo,
    MCPAddParams,
    MCPAddResponse,
    MCPCatalogMutationResponse,
    MCPLoginParams,
    MCPLogoutParams,
    MCPReadParams,
    MCPReadResponse,
    MCPRefreshParams,
    MCPRemoveParams,
    MCPRemoveResponse,
    MCPToggleParams,
    ProtocolErrorCode,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
)
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair

_HEALTHY_SERVER = "healthy"
_OAUTH_SERVER = "oauth"
_MCP_SERVER_SOURCE = """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("backend-contract")


@mcp.tool()
def echo(value: str) -> str:
    return value


if __name__ == "__main__":
    mcp.run()
"""


type BackendWithMCP = Any
type BackendWithMCPClass = type[Any]


@pytest.fixture(autouse=True)
def backend_contract_mcp_configuration(config_dir: Path, tmp_path: Path) -> None:
    server = tmp_path / "backend_contract_mcp_server.py"
    server.write_text(_MCP_SERVER_SOURCE, encoding="utf-8")
    config_path = config_dir / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config["mcp_servers"] = [
        {
            "name": _HEALTHY_SERVER,
            "transport": "stdio",
            "command": [sys.executable, str(server)],
            "startup_timeout_sec": 30.0,
            "tool_timeout_sec": 30.0,
        },
        {
            "name": _OAUTH_SERVER,
            "transport": "streamable-http",
            "url": "http://127.0.0.1:9/mcp",
            "auth": {"type": "oauth", "scopes": []},
            "disabled": True,
        },
    ]
    config_path.write_text(tomli_w.dumps(config), encoding="utf-8")


def _backend_class(experimental_harness: bool) -> BackendWithMCPClass:
    if not experimental_harness:
        return LegacySessionBackend
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

    return UnifiedHarnessBackendAdapter


def _source(response: MCPReadResponse, name: str):
    return next(source for source in response.mcp.sources if source.name == name)


@pytest.mark.asyncio
async def test_mcp_catalog_read_refresh_toggle_remove_and_compatibility_aliases(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    await backend_contract_session.resources.runtime.wait_until_ready()
    session_id = backend_contract_session.session_id
    canonical_read = MCPReadResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/read", MCPReadParams(session_id=session_id)
        )
    )
    compatibility_read = MCPReadResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp/read", MCPReadParams(session_id=session_id)
        )
    )

    assert compatibility_read == canonical_read
    healthy = _source(canonical_read, _HEALTHY_SERVER)
    assert healthy.status is MCPSourceStatus.ENABLED
    assert [(tool.name, tool.enabled) for tool in healthy.tools] == [("echo", True)]
    assert _source(canonical_read, _OAUTH_SERVER).status is MCPSourceStatus.DISABLED

    canonical_refresh = MCPCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/refresh", MCPRefreshParams(session_id=session_id)
        )
    )
    compatibility_refresh = MCPCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp/refresh", MCPRefreshParams(session_id=session_id)
        )
    )
    assert canonical_refresh.runtime is not None
    assert compatibility_refresh.runtime is not None
    assert (
        _source(
            MCPReadResponse(mcp=compatibility_refresh.runtime.mcp), _HEALTHY_SERVER
        ).status
        is MCPSourceStatus.ENABLED
    )

    disabled = MCPCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/toggle",
            MCPToggleParams(
                session_id=session_id,
                name=_HEALTHY_SERVER,
                source="server",
                disabled=True,
            ),
        )
    )
    enabled = MCPCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp/toggle",
            MCPToggleParams(
                session_id=session_id,
                name=_HEALTHY_SERVER,
                source="server",
                disabled=False,
            ),
        )
    )
    assert disabled.runtime is not None
    assert disabled.runtime.mcp.statuses[_HEALTHY_SERVER] == "disabled"
    assert enabled.runtime is not None
    assert enabled.runtime.mcp.statuses[_HEALTHY_SERVER] == "enabled"

    removed = MCPRemoveResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/remove",
            MCPRemoveParams(session_id=session_id, name=_OAUTH_SERVER),
        )
    )
    assert removed.name == _OAUTH_SERVER
    assert removed.removed is True
    assert removed.runtime is not None
    assert _OAUTH_SERVER not in removed.runtime.mcp.statuses


@pytest.mark.asyncio
async def test_mcp_catalog_add_login_logout_aliases_and_notification_order(
    experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_type = _backend_class(experimental_harness)

    async def keep_current_state(
        backend: BackendWithMCP, *args: object, **kwargs: object
    ):
        del args, kwargs
        return await backend.read_mcp()

    async def login(service: MCPAuthenticationService, name: str, *, on_url) -> str:
        await on_url("https://auth.example.test/authorize")
        return service.descriptor_revision(name)

    async def logout(service: MCPAuthenticationService, name: str) -> str:
        return service.descriptor_revision(name)

    monkeypatch.setattr(backend_type, "reconfigure_mcp", keep_current_state)
    monkeypatch.setattr(backend_type, "authorization_changed", keep_current_state)
    monkeypatch.setattr(backend_type, "suspend_mcp", keep_current_state)
    monkeypatch.setattr(MCPAuthenticationService, "login", login)
    monkeypatch.setattr(MCPAuthenticationService, "logout", logout)
    connection = await _connect_recording_client(experimental_harness)
    session_id: str | None = None
    try:
        started = SessionStartResponse.model_validate(
            await connection.client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id

        canonical_add = MCPAddResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/add",
                MCPAddParams(
                    session_id=session_id,
                    url="https://added.example.test/mcp",
                    name="added",
                    scopes=[],
                    transport="streamable-http",
                ),
            )
        )
        compatibility_add = MCPAddResponse.model_validate(
            await connection.client.request(
                "mcp/add",
                MCPAddParams(
                    session_id=session_id,
                    url="https://added.example.test/mcp",
                    name="added",
                    scopes=[],
                    transport="streamable-http",
                ),
            )
        )
        assert canonical_add.created is True
        assert compatibility_add.created is False
        assert canonical_add.runtime is not None
        assert compatibility_add.runtime is not None

        start = len(connection.transport.timeline)
        canonical_login = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/login", MCPLoginParams(session_id=session_id, name="added")
            )
        )
        assert canonical_login.runtime is not None
        await connection.transport.wait_for(
            lambda timeline: (
                _received_method_count(timeline[start:], "runtime/updated") == 1
            )
        )
        _assert_login_wire_order(connection.transport.timeline[start:])

        compatibility_login = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp/login", MCPLoginParams(session_id=session_id, name="added")
            )
        )
        canonical_logout = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/logout",
                MCPLogoutParams(session_id=session_id, name="added"),
            )
        )
        compatibility_logout = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp/logout", MCPLogoutParams(session_id=session_id, name="added")
            )
        )
        assert compatibility_login.runtime is not None
        assert canonical_logout.runtime is not None
        assert compatibility_logout.runtime is not None
    finally:
        await connection.close(session_id)


@pytest.mark.asyncio
async def test_mcp_catalog_auth_required_is_deduplicated_across_backend_events(
    experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_type = _backend_class(experimental_harness)
    original_subscribe = backend_type.subscribe

    async def subscribe_twice(
        backend: BackendWithMCP, params
    ) -> SessionEventSubscription:
        subscription = await original_subscribe(backend, params)

        async def duplicated_events() -> AsyncIterator:
            async for event in subscription.events:
                yield event
                if isinstance(event.event, MCPAuthorizationRequiredEvent):
                    yield event

        return SessionEventSubscription(
            snapshot=subscription.snapshot, events=duplicated_events()
        )

    monkeypatch.setattr(backend_type, "subscribe", subscribe_twice)
    connection = await _connect_recording_client(experimental_harness)
    session_id: str | None = None
    try:
        started = SessionStartResponse.model_validate(
            await connection.client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id
        await connection.client.request(
            "mcp_catalog/toggle",
            MCPToggleParams(
                session_id=session_id,
                name=_OAUTH_SERVER,
                source="server",
                disabled=False,
            ),
        )
        await connection.transport.wait_for(
            lambda timeline: (
                _received_method_count(timeline, "mcp_catalog/authRequired") == 1
            )
        )
        await connection.transport.wait_for(
            lambda timeline: _received_method_count(timeline, "runtime/updated") >= 1
        )
        await asyncio.sleep(0)

        auth_required = [
            message
            for direction, message in connection.transport.timeline
            if direction == "received"
            and message.get("method") == "mcp_catalog/authRequired"
        ]
        assert len(auth_required) == 1
        assert auth_required[0]["params"]["sessionId"] == session_id
        assert auth_required[0]["params"]["name"] == _OAUTH_SERVER
        assert "descriptorRevision" in auth_required[0]["params"]
    finally:
        await connection.close(session_id)


@pytest.mark.asyncio
async def test_mcp_login_for_disabled_source_performs_no_runtime_authorization(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = backend_contract_session.session_id
    resolve_calls: list[str] = []
    original_resolve = MCPAuthenticationService.resolve

    async def login(service: MCPAuthenticationService, name: str, *, on_url) -> str:
        await on_url("https://auth.example.test/disabled")
        return "descriptor-after-login"

    async def track_resolve(service: MCPAuthenticationService, reference):
        resolve_calls.append(reference.server_name)
        return await original_resolve(service, reference)

    monkeypatch.setattr(MCPAuthenticationService, "login", login)
    monkeypatch.setattr(MCPAuthenticationService, "resolve", track_resolve)

    response = MCPCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/login",
            MCPLoginParams(session_id=session_id, name=_OAUTH_SERVER),
        )
    )

    assert response.runtime is not None
    assert response.runtime.mcp.statuses[_OAUTH_SERVER] == "disabled"
    assert _OAUTH_SERVER not in resolve_calls


@pytest.mark.asyncio
async def test_sessionless_mcp_mutations_do_not_build_a_session_runtime_and_bootstrap(
    experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = HarnessProcess(experimental_harness=experimental_harness)
    build_calls = 0
    build_name = (
        "build_unified_session_context" if experimental_harness else "open_root"
    )
    original_build = getattr(process, build_name)

    async def tracked_build(*args: object, **kwargs: object):
        nonlocal build_calls
        build_calls += 1
        return await original_build(*args, **kwargs)

    async def login(service: MCPAuthenticationService, name: str, *, on_url) -> str:
        await on_url("https://auth.example.test/sessionless")
        return service.descriptor_revision(name)

    async def logout(service: MCPAuthenticationService, name: str) -> str:
        return service.descriptor_revision(name)

    monkeypatch.setattr(process, build_name, tracked_build)
    monkeypatch.setattr(MCPAuthenticationService, "login", login)
    monkeypatch.setattr(MCPAuthenticationService, "logout", logout)
    connection = await _connect_recording_client(experimental_harness, process=process)
    session_id: str | None = None
    try:
        added = MCPAddResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/add",
                MCPAddParams(
                    url="https://sessionless.example.test/mcp",
                    name="sessionless",
                    scopes=[],
                    transport="streamable-http",
                ),
            )
        )
        alias_add = MCPAddResponse.model_validate(
            await connection.client.request(
                "mcp/add",
                MCPAddParams(
                    url="https://sessionless.example.test/mcp",
                    name="sessionless",
                    scopes=[],
                    transport="streamable-http",
                ),
            )
        )
        disabled = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/toggle",
                MCPToggleParams(name="sessionless", source="server", disabled=True),
            )
        )
        enabled = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp/toggle",
                MCPToggleParams(name="sessionless", source="server", disabled=False),
            )
        )
        logged_in = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/login", MCPLoginParams(name="sessionless")
            )
        )
        alias_login = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp/login", MCPLoginParams(name="sessionless")
            )
        )
        logged_out = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/logout", MCPLogoutParams(name="sessionless")
            )
        )
        alias_logout = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp/logout", MCPLogoutParams(name="sessionless")
            )
        )
        final_disable = MCPCatalogMutationResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/toggle",
                MCPToggleParams(name="sessionless", source="server", disabled=True),
            )
        )
        removed = MCPRemoveResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/remove", MCPRemoveParams(name=_HEALTHY_SERVER)
            )
        )

        assert added.runtime is None
        assert alias_add.runtime is None
        assert disabled.runtime is None
        assert enabled.runtime is None
        assert logged_in.runtime is None
        assert alias_login.runtime is None
        assert logged_out.runtime is None
        assert alias_logout.runtime is None
        assert final_disable.runtime is None
        assert removed.runtime is None
        assert build_calls == 0
        assert (
            _received_method_count(connection.transport.timeline, "runtime/updated")
            == 0
        )

        started = SessionStartResponse.model_validate(
            await connection.client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id
        assert build_calls == 1
        read = MCPReadResponse.model_validate(
            await connection.client.request(
                "mcp_catalog/read", MCPReadParams(session_id=session_id)
            )
        )
        assert _source(read, "sessionless").status is MCPSourceStatus.DISABLED
        assert _HEALTHY_SERVER not in read.mcp.statuses
    finally:
        await connection.close(session_id)


@pytest.mark.asyncio
async def test_sessionless_mcp_mutation_conflicts_with_an_active_session(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    del backend_contract_session
    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_connection.client.request(
            "mcp_catalog/toggle",
            MCPToggleParams(name=_HEALTHY_SERVER, source="server", disabled=True),
        )

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["additive", "restrictive"])
async def test_mcp_convergence_failure_preserves_safe_public_state(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
    experimental_harness: bool,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    backend_type = _backend_class(experimental_harness)

    async def fail_convergence(*args: object, **kwargs: object):
        del args, kwargs
        raise SessionBackendError(
            ProtocolErrorCode.INTERNAL_ERROR, "injected backend convergence failure"
        )

    monkeypatch.setattr(backend_type, "reconfigure_mcp", fail_convergence)
    session_id = backend_contract_session.session_id
    with pytest.raises(AppServerResponseError) as exc_info:
        if change == "additive":
            await backend_contract_connection.client.request(
                "mcp_catalog/add",
                MCPAddParams(
                    session_id=session_id,
                    url="https://drift.example.test/mcp",
                    name="drift",
                    scopes=[],
                    transport="streamable-http",
                ),
            )
        else:
            await backend_contract_connection.client.request(
                "mcp_catalog/toggle",
                MCPToggleParams(
                    session_id=session_id,
                    name=_HEALTHY_SERVER,
                    source="server",
                    disabled=True,
                ),
            )

    assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR
    read = MCPReadResponse.model_validate(
        await backend_contract_connection.client.request(
            "mcp_catalog/read", MCPReadParams(session_id=session_id)
        )
    )
    affected = "drift" if change == "additive" else _HEALTHY_SERVER
    assert _source(read, affected).status is MCPSourceStatus.UNAVAILABLE
    assert "did not converge" in read.mcp.discovery_errors[affected]
    assert _source(read, _OAUTH_SERVER).status is MCPSourceStatus.DISABLED


class _RecordingTransport:
    def __init__(self, delegate: JsonRpcTransport) -> None:
        self._delegate = delegate
        self.timeline: list[tuple[str, dict[str, Any]]] = []
        self._changed = asyncio.Event()

    async def send(self, message: dict[str, Any]) -> None:
        self.timeline.append(("sent", message.copy()))
        self._changed.set()
        await self._delegate.send(message)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        async for message in self._delegate.messages():
            self.timeline.append(("received", message.copy()))
            self._changed.set()
            yield message

    async def close(self) -> None:
        await self._delegate.close()

    async def wait_for(self, predicate, *, timeout: float = 5.0) -> None:
        async def wait() -> None:
            while not predicate(self.timeline):
                self._changed.clear()
                if predicate(self.timeline):
                    return
                await self._changed.wait()

        await asyncio.wait_for(wait(), timeout=timeout)


@dataclass(slots=True)
class _RecordingConnection:
    client: AppServerClient
    process: HarnessProcess
    transport: _RecordingTransport

    async def close(self, session_id: str | None) -> None:
        if session_id is not None:
            try:
                await self.client.request(
                    "session/stop", SessionStopParams(session_id=session_id)
                )
            except (AppServerResponseError, RuntimeError):
                pass
        await self.client.close()
        await self.process.close()


async def _connect_recording_client(
    experimental_harness: bool, *, process: HarnessProcess | None = None
) -> _RecordingConnection:
    effective_process = process or HarnessProcess(
        experimental_harness=experimental_harness
    )
    client_transport, server_transport = memory_transport_pair()
    recording = _RecordingTransport(cast(JsonRpcTransport, client_transport))
    harness = await create_harness_server(
        server_transport, transport_kind="in_process", process=effective_process
    )
    client = AppServerClient(recording, run_peer=harness.serve)
    try:
        await client.start()
        await client.initialize(
            ClientInfo(name="mcp-backend-contract-test", version="0"),
            ClientCapabilities(),
        )
        await client.notify("initialized")
    except BaseException:
        await client.close()
        await effective_process.close()
        raise
    return _RecordingConnection(client, effective_process, recording)


def _received_method_count(
    timeline: list[tuple[str, dict[str, Any]]], method: str
) -> int:
    return sum(
        direction == "received" and message.get("method") == method
        for direction, message in timeline
    )


def _assert_login_wire_order(timeline: list[tuple[str, dict[str, Any]]]) -> None:
    login_request = next(
        message
        for direction, message in timeline
        if direction == "sent" and message.get("method") == "mcp_catalog/login"
    )
    request_id = login_request["id"]

    def received_index(predicate) -> int:
        return next(
            index
            for index, (direction, message) in enumerate(timeline)
            if direction == "received" and predicate(message)
        )

    auth_url = received_index(
        lambda message: message.get("method") == "mcp_catalog/authUrl"
    )
    compatibility_auth_url = received_index(
        lambda message: message.get("method") == "mcp/authUrl"
    )
    response = received_index(lambda message: message.get("id") == request_id)
    runtime_updated = received_index(
        lambda message: message.get("method") == "runtime/updated"
    )
    assert auth_url < response
    assert compatibility_auth_url < response
    assert response < runtime_updated
