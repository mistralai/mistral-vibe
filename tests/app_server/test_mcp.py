from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import (
    attach_test_app_server_session,
    create_test_app_server_session,
    start_test_app_server,
)
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._session_backend_port import (
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
)
from vibe.app_server.models import MCPSourceKind, MCPSourceStatus
from vibe.app_server.protocol import (
    AppServerResponseError,
    MCPReadParams,
    MCPReadResponse,
    Notification,
    ProtocolErrorCode,
)
from vibe.core.config import ConnectorConfig, MCPHttp, MCPOAuth, MCPStdio
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.tools.mcp.tools import RemoteTool


class FailingMCPRegistry(FakeMCPRegistry):
    """Registry that always fails discovery for a named server."""

    def __init__(self, failing_server: str) -> None:
        super().__init__()
        self._failing_server = failing_server

    async def get_tools_async(self, servers):
        working = [s for s in servers if s.name != self._failing_server]
        for s in servers:
            if s.name == self._failing_server:
                self._failed[s.name] = "connection refused"
        return await super().get_tools_async(working)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "toggle"])
async def test_mcp_config_conflicts_are_public_conflicts(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """*Prepare*: The authoritative catalog persistence helper reports a conflict.
    *Do*: Submit add and toggle through the public MCP resource.
    *Assert*: The app server returns the stable public conflict code.
    """
    # Prepare
    conflict = ConcurrencyConflictError("expected", "actual")
    target = "persist_oauth_mcp_server" if operation == "add" else "persist_mcp_toggle"
    monkeypatch.setattr(
        f"vibe.app_server.mcp_catalog.{target}", AsyncMock(side_effect=conflict)
    )
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        with pytest.raises(AppServerResponseError) as exc_info:
            if operation == "add":
                await session.resources.mcp.add(
                    url="https://mcp.example.com/mcp",
                    name=None,
                    scopes=[],
                    transport="streamable-http",
                )
            else:
                await session.resources.mcp.toggle(
                    "search", source="server", disabled=True
                )
    finally:
        await session.close()

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_mcp_login_streams_typed_auth_url_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: The process authentication service begins an interactive login.
    *Do*: Consume the public login stream.
    *Assert*: The typed catalog auth URL reaches the MCP resource.
    """
    # Prepare
    login_calls: list[str] = []

    async def login(
        _service: MCPAuthenticationService,
        name: str,
        *,
        on_url: Callable[[str], Awaitable[None]],
    ) -> str:
        login_calls.append(name)
        await on_url("https://auth.example.com/oauth")
        return _service.descriptor_revision(name)

    monkeypatch.setattr(MCPAuthenticationService, "login", login)
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
            )
        ]
    )
    agent_loop = build_test_agent_loop(config=config, mcp_registry=FakeMCPRegistry())
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        events = [event async for event in session.resources.mcp.login("search")]
    finally:
        await session.close()

    # Assert
    assert login_calls == ["search"]
    assert [(event.name, event.url) for event in events] == [
        ("search", "https://auth.example.com/oauth")
    ]


@pytest.mark.asyncio
async def test_unknown_notifications_do_not_enter_mcp_login_stream() -> None:
    """*Prepare*: A connected MCP resource has no matching notification stream.
    *Do*: Deliver an unknown future notification.
    *Assert*: The MCP resource leaves it unconsumed.
    """
    # Prepare
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        consumed = await session.resources.consume_notification(
            Notification(method="future/event", params={})
        )
    finally:
        await session.close()

    # Assert
    assert consumed is False


@pytest.mark.asyncio
async def test_mcp_catalog_read_preserves_legacy_connector_projection() -> None:
    """*Prepare*: A legacy session projects one connector and a bootstrap error.
    *Do*: Read the public MCP catalog through the app-server facade.
    *Assert*: The MCP-only catalog projection preserves both connector fields.
    """
    # Prepare
    config = build_test_vibe_config(
        connectors=[ConnectorConfig(name="gmail", disabled=False)]
    )
    registry = FakeConnectorRegistry(
        connectors={"gmail": [RemoteTool(name="search", description="Search Gmail")]},
        bootstrap_error="Connector bootstrap unavailable",
    )
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.connector_registry = registry
    agent_loop.tool_manager.set_connector_registry(registry)
    agent_loop.tool_manager.integrate_connectors()
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)

    try:
        # Do
        response = MCPReadResponse.model_validate(
            await client.request(
                "mcp_catalog/read", MCPReadParams(session_id=session.session_id)
            )
        )
    finally:
        await session.close()

    # Assert
    assert [
        (source.name, source.kind, source.status, [tool.name for tool in source.tools])
        for source in response.mcp.sources
    ] == [("gmail", MCPSourceKind.CONNECTOR, MCPSourceStatus.CONNECTED, ["search"])]
    assert response.mcp.connector_error == "Connector bootstrap unavailable"


@pytest.mark.asyncio
async def test_mcp_toggle_enable_reuses_valid_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An enabled server and observable legacy reconfiguration paths.
    *Do*: Enable the configured server through the catalog facade.
    *Assert*: Ordinary convergence retains valid descriptors instead of forcing refresh.
    """
    # Prepare
    monkeypatch.setattr("vibe.app_server.mcp_catalog.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    reconfigure_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    agent_loop.tool_manager.reconfigure_mcp_async = reconfigure_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        await session.resources.mcp.toggle("search", source="server", disabled=False)
    finally:
        await session.close()

    # Assert
    reconfigure_mock.assert_awaited_once()
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_toggle_disable_does_not_rediscover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A configured server and observable legacy remote refresh.
    *Do*: Disable the server through the catalog facade.
    *Assert*: Restrictive convergence withdraws routes without rediscovery.
    """
    # Prepare
    monkeypatch.setattr("vibe.app_server.mcp_catalog.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        await session.resources.mcp.toggle("search", source="server", disabled=True)
    finally:
        await session.close()

    # Assert
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_toggle_tool_does_not_rediscover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: One server tool and observable legacy remote refresh.
    *Do*: Disable only that tool through the catalog facade.
    *Assert*: Tool-level withdrawal does not rediscover the source.
    """
    # Prepare
    monkeypatch.setattr("vibe.app_server.mcp_catalog.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        await session.resources.mcp.toggle(
            "search", source="server", disabled=True, tool_name="search_web"
        )
    finally:
        await session.close()

    # Assert
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_login_rediscovers_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """*Prepare*: A successful app-server login and observable legacy discovery.
    *Do*: Complete the public login stream.
    *Assert*: The affected legacy descriptors are rediscovered once.
    """

    # Prepare
    async def login(
        _service: MCPAuthenticationService,
        name: str,
        *,
        on_url: Callable[[str], Awaitable[None]],
    ) -> str:
        await on_url("https://auth.example.com/oauth")
        return _service.descriptor_revision(name)

    monkeypatch.setattr(MCPAuthenticationService, "login", login)

    async def resolve(_service, reference):
        return MCPAuthorizationSnapshot(
            headers={"Authorization": "Bearer token"},
            connection_revision="connection-1",
            descriptor_revision=reference.descriptor_revision,
        )

    monkeypatch.setattr(MCPAuthenticationService, "resolve", resolve)
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
            )
        ]
    )
    agent_loop = build_test_agent_loop(config=config, mcp_registry=FakeMCPRegistry())
    reconfigure_mock = AsyncMock()
    agent_loop.tool_manager.reconfigure_mcp_async = reconfigure_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        async for _event in session.resources.mcp.login("search"):
            pass
    finally:
        await session.close()

    # Assert
    reconfigure_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_login_keeps_disabled_source_transport_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A disabled OAuth source completes app-owned interactive login.
    *Do*: Complete the public login stream with a live legacy session target.
    *Assert*: Runtime convergence does not resolve credentials or rediscover the source.
    """

    # Prepare
    async def login(
        _service: MCPAuthenticationService,
        name: str,
        *,
        on_url: Callable[[str], Awaitable[None]],
    ) -> str:
        await on_url("https://auth.example.com/oauth")
        return "descriptor-2"

    resolve = AsyncMock(side_effect=AssertionError("disabled provider resolution"))
    monkeypatch.setattr(MCPAuthenticationService, "login", login)
    monkeypatch.setattr(MCPAuthenticationService, "resolve", resolve)
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
                disabled=True,
            )
        ]
    )
    agent_loop = build_test_agent_loop(config=config, mcp_registry=FakeMCPRegistry())
    refresh_mock = AsyncMock()
    reconfigure_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    agent_loop.tool_manager.reconfigure_mcp_async = reconfigure_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        async for _event in session.resources.mcp.login("search"):
            pass
    finally:
        await session.close()

    # Assert
    resolve.assert_not_awaited()
    refresh_mock.assert_not_awaited()
    reconfigure_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_logout_authorization_required_does_not_restore_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: Logout withdraws an enabled OAuth source before deleting credentials.
    *Do*: Runtime convergence observes the new authorization-required revision.
    *Assert*: The source remains withdrawn without any remote rediscovery path.
    """

    # Prepare
    async def logout(_service: MCPAuthenticationService, name: str) -> str:
        return "descriptor-2"

    async def resolve(_service, reference):
        return MCPAuthorizationRequired(
            reason="missing", descriptor_revision=reference.descriptor_revision
        )

    monkeypatch.setattr(MCPAuthenticationService, "logout", logout)
    monkeypatch.setattr(MCPAuthenticationService, "resolve", resolve)
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
            )
        ]
    )
    registry = FakeMCPRegistry()
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)
    refresh_mock = AsyncMock()
    reconfigure_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    agent_loop.tool_manager.reconfigure_mcp_async = reconfigure_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        state = await session.resources.mcp.logout("search")
    finally:
        await session.close()

    # Assert
    source = next(item for item in state.sources if item.name == "search")
    assert source.status is MCPSourceStatus.NEEDS_AUTH
    assert registry.needs_auth == {"search"}
    refresh_mock.assert_not_awaited()
    reconfigure_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_toggle_enable_broken_server_stays_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: One configured source whose isolated discovery fails.
    *Do*: Enable the source through the catalog facade.
    *Assert*: Its public state remains unavailable without failing the request.
    """
    # Prepare
    monkeypatch.setattr("vibe.app_server.mcp_catalog.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    registry = FailingMCPRegistry(failing_server="search")
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        # Do
        mcp_state = await session.resources.mcp.toggle(
            "search", source="server", disabled=False
        )
    finally:
        await session.close()

    # Assert
    broken = next(s for s in mcp_state.sources if s.name == "search")
    assert broken.status == MCPSourceStatus.UNAVAILABLE
