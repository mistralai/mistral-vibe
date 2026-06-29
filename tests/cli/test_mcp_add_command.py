from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.mcp_commands import parse_mcp_add_args, parse_mcp_subcommand
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.core.config import MCPHttp, MCPOAuth, MCPStreamableHttp, VibeConfig


def _capture_mounted_widgets(
    app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> list[object]:
    mounted_widgets: list[object] = []

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)
    return mounted_widgets


def test_parse_mcp_add_args_accepts_url_and_options() -> None:
    args = parse_mcp_add_args(
        "https://mcp.example.com/mcp --name docs --scope read --scope write "
        "--transport http --no-login"
    )

    assert args.url == "https://mcp.example.com/mcp"
    assert args.name == "docs"
    assert args.scopes == ["read", "write"]
    assert args.transport == "http"
    assert args.login is False


def test_parse_mcp_add_args_defaults_to_login() -> None:
    args = parse_mcp_add_args("https://mcp.example.com/mcp")

    assert args.transport == "streamable-http"
    assert args.login is True


@pytest.mark.parametrize(
    "raw_args",
    [
        "",
        "https://mcp.example.com/mcp extra",
        "https://mcp.example.com/mcp --unknown",
        "https://mcp.example.com/mcp --name",
        "https://mcp.example.com/mcp --name a --name b",
        "https://mcp.example.com/mcp --scope",
        "https://mcp.example.com/mcp --login",
        "https://mcp.example.com/mcp --transport",
        "https://mcp.example.com/mcp --transport sse",
        "https://mcp.example.com/mcp --transport http --transport streamable-http",
        "'unterminated",
    ],
)
def test_parse_mcp_add_args_rejects_invalid_args(raw_args: str) -> None:
    with pytest.raises(ValueError):
        parse_mcp_add_args(raw_args)


def test_parse_mcp_subcommand_recognizes_supported_subcommands() -> None:
    parsed = parse_mcp_subcommand("add https://mcp.linear.app/mcp --no-login")

    assert parsed is not None
    assert parsed.name == "add"
    assert parsed.args == "https://mcp.linear.app/mcp --no-login"


def test_parse_mcp_subcommand_ignores_unknown_subcommands() -> None:
    assert parse_mcp_subcommand("tools linear") is None


@pytest.mark.asyncio
async def test_mcp_add_saves_oauth_server_and_prints_next_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)

    await app._mcp_add("https://mcp.linear.app/mcp --no-login")

    server = VibeConfig.load().mcp_servers[0]
    assert isinstance(server, MCPStreamableHttp)
    assert server.name == "linear"
    assert isinstance(server.auth, MCPOAuth)
    assert server.auth.scopes == []
    assert any(
        isinstance(widget, UserCommandMessage)
        and "Added OAuth MCP server `linear`." in widget._content
        and "/mcp login linear" in widget._content
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_mcp_add_saves_name_and_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)

    await app._mcp_add(
        "https://mcp.example.com/mcp --name docs --scope read --scope write --no-login"
    )

    server = VibeConfig.load().mcp_servers[0]
    assert isinstance(server, MCPStreamableHttp)
    assert server.name == "docs"
    assert isinstance(server.auth, MCPOAuth)
    assert server.auth.scopes == ["read", "write"]
    assert any(
        isinstance(widget, UserCommandMessage)
        and "Added OAuth MCP server `docs`." in widget._content
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_mcp_add_saves_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    _capture_mounted_widgets(app, monkeypatch)

    await app._mcp_add("https://mcp.example.com/mcp --transport http --no-login")

    server = VibeConfig.load().mcp_servers[0]
    assert isinstance(server, MCPHttp)
    assert server.transport == "http"
    assert isinstance(server.auth, MCPOAuth)


@pytest.mark.asyncio
async def test_mcp_add_delegates_to_login_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)
    login = AsyncMock()
    monkeypatch.setattr(app, "_mcp_login", login)

    await app._mcp_add("https://mcp.linear.app/mcp")

    login.assert_awaited_once_with("linear")
    assert any(
        isinstance(widget, UserCommandMessage)
        and "Starting OAuth login..." in widget._content
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_mcp_add_no_login_skips_login(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    _capture_mounted_widgets(app, monkeypatch)
    login = AsyncMock()
    monkeypatch.setattr(app, "_mcp_login", login)

    await app._mcp_add("https://mcp.linear.app/mcp --no-login")

    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_add_mounts_parser_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)

    await app._mcp_add("https://mcp.example.com/mcp --unknown")

    assert any(
        isinstance(widget, ErrorMessage)
        and widget._error == "Unknown /mcp add option: --unknown"
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_mcp_add_help_states_oauth_only(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)

    await app._mcp_add("--help")

    assert any(
        isinstance(widget, UserCommandMessage)
        and "Usage: /mcp add <url>" in widget._content
        and "--transport <http|streamable-http>" in widget._content
        and "OAuth-only shortcut" in widget._content
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_mcp_subcommand_handler_recognizes_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    mounted_widgets = _capture_mounted_widgets(app, monkeypatch)

    handled = await app._maybe_handle_mcp_subcommand(
        "add https://mcp.linear.app/mcp --no-login"
    )

    assert handled is True
    assert any(
        isinstance(widget, UserCommandMessage)
        and "Added OAuth MCP server `linear`." in widget._content
        for widget in mounted_widgets
    )
