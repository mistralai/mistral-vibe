from __future__ import annotations

from collections.abc import Awaitable, Callable
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
from vibe.cli.textual_ui.widgets.mcp_app import MCPApp
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage

_ERROR = "Failed to load connectors (HTTP 502).\nServer response: Bad Gateway"


def _capture_mounted(app, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    mounted: list[object] = []

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted.append(widget)

    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)
    return mounted


@pytest.mark.asyncio
async def test_show_mcp_surfaces_connector_error_when_no_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    mounted = _capture_mounted(app, monkeypatch)

    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(return_value=MCPState(sources=[], connector_error=_ERROR)),
    )

    await app._show_mcp("")

    errors = [w for w in mounted if isinstance(w, ErrorMessage)]
    assert len(errors) == 1
    assert "502" in str(errors[0]._error)
    assert "Bad Gateway" in str(errors[0]._error)
    assert not any(
        isinstance(w, UserCommandMessage)
        and "No MCP servers or connectors configured" in w._content
        for w in mounted
    )


@pytest.mark.asyncio
async def test_show_mcp_surfaces_connector_error_and_opens_panel_with_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    mounted = _capture_mounted(app, monkeypatch)

    server = MCPSourceSummary(
        name="linear",
        kind=MCPSourceKind.SERVER,
        transport="streamable-http",
        status=MCPSourceStatus.CONNECTED,
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(return_value=MCPState(sources=[server], connector_error=_ERROR)),
    )
    switched: list[object] = []

    async def switch_from_input(widget: object, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)

    await app._show_mcp("")

    assert any(isinstance(w, ErrorMessage) and "502" in str(w._error) for w in mounted)
    assert len(switched) == 1


@pytest.mark.asyncio
async def test_show_mcp_no_error_when_healthy_and_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    mounted = _capture_mounted(app, monkeypatch)

    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "read",
        AsyncMock(return_value=MCPState(sources=[], connector_error=None)),
    )

    await app._show_mcp("")

    assert not any(isinstance(w, ErrorMessage) for w in mounted)
    assert any(
        isinstance(w, UserCommandMessage)
        and "No MCP servers or connectors configured" in w._content
        for w in mounted
    )


@pytest.mark.asyncio
async def test_refresh_mcp_browser_refreshes_both_owned_catalogs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    calls: list[str] = []

    def recorder(name: str) -> Callable[[], Awaitable[None]]:
        async def record() -> None:
            calls.append(name)

        return record

    monkeypatch.setattr(
        app.app_server.resources.runtime,
        "wait_until_ready",
        AsyncMock(side_effect=recorder("ready")),
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "refresh_connectors",
        AsyncMock(side_effect=recorder("connectors")),
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp, "refresh", AsyncMock(side_effect=recorder("mcp"))
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp, "read", AsyncMock(side_effect=recorder("read"))
    )
    monkeypatch.setattr(app, "_refresh_banner", lambda: calls.append("banner"))

    result = await app._refresh_mcp_browser()

    assert result == "Refreshed."
    assert calls == ["ready", "connectors", "mcp", "read", "banner"]


def _gmail_state() -> MCPState:
    return MCPState(
        sources=[
            MCPSourceSummary(
                name="gm_9c1b",
                display_name="Gmail",
                kind=MCPSourceKind.CONNECTOR,
                transport="connector",
                status=MCPSourceStatus.CONNECTED,
            )
        ]
    )


@pytest.mark.asyncio
async def test_show_mcp_opens_a_source_by_its_shown_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    _capture_mounted(app, monkeypatch)
    monkeypatch.setattr(
        app.app_server.resources.mcp, "read", AsyncMock(return_value=_gmail_state())
    )
    switched: list[MCPApp] = []

    async def switch_from_input(widget: MCPApp, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)

    await app._show_mcp("gmail")

    assert [widget._viewing_name for widget in switched] == ["gm_9c1b"]


@pytest.mark.asyncio
async def test_show_mcp_unknown_source_lists_the_shown_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    mounted = _capture_mounted(app, monkeypatch)
    monkeypatch.setattr(
        app.app_server.resources.mcp, "read", AsyncMock(return_value=_gmail_state())
    )

    await app._show_mcp("nope")

    errors = [str(w._error) for w in mounted if isinstance(w, ErrorMessage)]
    assert errors == ["Unknown MCP server or connector: nope. Known: Gmail"]


@pytest.mark.asyncio
async def test_refresh_mcp_browser_refreshes_servers_when_connectors_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    outage = AppServerResponseError(
        ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR, message="offline")
    )
    monkeypatch.setattr(
        app.app_server.resources.runtime, "wait_until_ready", AsyncMock()
    )
    monkeypatch.setattr(
        app.app_server.resources.mcp,
        "refresh_connectors",
        AsyncMock(side_effect=outage),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(app.app_server.resources.mcp, "refresh", refresh)
    monkeypatch.setattr(app.app_server.resources.mcp, "read", AsyncMock())

    result = await app._refresh_mcp_browser()

    assert result == "Refreshed."
    refresh.assert_awaited_once()
