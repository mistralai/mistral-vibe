from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.cli.textual_ui.widgets.connector_auth_app import ConnectorAuthApp
from vibe.cli.textual_ui.widgets.messages import ErrorMessage


async def _empty_login(_name: str) -> AsyncGenerator[object, None]:
    return
    yield  # pragma: no cover - makes this an async generator


def _connector(name: str) -> MCPSourceSummary:
    return MCPSourceSummary(
        name=name,
        kind=MCPSourceKind.CONNECTOR,
        transport="connector",
        status=MCPSourceStatus.NEEDS_AUTH,
    )


def _server(name: str) -> MCPSourceSummary:
    return MCPSourceSummary(
        name=name,
        kind=MCPSourceKind.SERVER,
        transport="streamable-http",
        status=MCPSourceStatus.NEEDS_AUTH,
    )


@pytest.mark.asyncio
async def test_mcp_login_connector_launches_connector_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()

    monkeypatch.setattr(
        app.app_server.resources.runtime, "wait_until_ready", AsyncMock()
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(return_value=MCPState(sources=[_connector("gmail")])),
    )

    login_calls: list[str] = []

    def fake_login(name: str) -> AsyncGenerator[object, None]:
        login_calls.append(name)
        return _empty_login(name)

    monkeypatch.setattr(app.app_server.resources.mcp, "login", fake_login)

    monkeypatch.setattr(app, "_switch_to_input_app", AsyncMock())
    monkeypatch.setattr(app, "_mount_and_scroll", AsyncMock())
    switched: list[object] = []

    async def switch_from_input(widget: object, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)

    await app._mcp_login("gmail")

    assert login_calls == []
    assert len(switched) == 1
    widget = switched[0]
    assert isinstance(widget, ConnectorAuthApp)
    assert widget._connector_name == "gmail"


@pytest.mark.asyncio
async def test_mcp_login_server_uses_oauth_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()

    monkeypatch.setattr(
        app.app_server.resources.runtime, "wait_until_ready", AsyncMock()
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(return_value=MCPState(sources=[_server("linear")])),
    )

    login_calls: list[str] = []

    def fake_login(name: str) -> AsyncGenerator[object, None]:
        login_calls.append(name)
        return _empty_login(name)

    monkeypatch.setattr(app.app_server.resources.mcp, "login", fake_login)

    switched: list[object] = []

    async def switch_from_input(widget: object, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)
    monkeypatch.setattr(app, "_mount_and_scroll", AsyncMock())

    await app._mcp_login("linear")

    assert login_calls == ["linear"]
    assert switched == []


@pytest.mark.asyncio
async def test_mcp_login_server_alias_wins_over_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A configured MCP server and a connector can share an alias. The server
    # OAuth path must win so `/mcp add` auto-login isn't hijacked.
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()

    monkeypatch.setattr(
        app.app_server.resources.runtime, "wait_until_ready", AsyncMock()
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(
            return_value=MCPState(sources=[_server("linear"), _connector("linear")])
        ),
    )

    login_calls: list[str] = []

    def fake_login(name: str) -> AsyncGenerator[object, None]:
        login_calls.append(name)
        return _empty_login(name)

    monkeypatch.setattr(app.app_server.resources.mcp, "login", fake_login)
    switched: list[object] = []

    async def switch_from_input(widget: object, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)
    monkeypatch.setattr(app, "_mount_and_scroll", AsyncMock())

    await app._mcp_login("linear")

    assert login_calls == ["linear"]
    assert switched == []


@pytest.mark.asyncio
async def test_mcp_login_surfaces_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()

    monkeypatch.setattr(
        app.app_server.resources.runtime, "wait_until_ready", AsyncMock()
    )
    error = AppServerResponseError(
        ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR, message="transient boom")
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp, "read", AsyncMock(side_effect=error)
    )

    mounted: list[object] = []

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted.append(widget)

    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)

    await app._mcp_login("gmail")

    assert any(
        isinstance(w, ErrorMessage) and "transient boom" in str(w._error)
        for w in mounted
    )
