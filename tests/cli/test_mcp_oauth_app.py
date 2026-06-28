from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import OptionList

from vibe.cli.textual_ui.widgets.mcp_oauth_app import (
    MCPOAuthApp,
    _LoginResult,
    _OAuthOptionId,
)


class FakeLoginRegistry:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.login_calls: list[str] = []

    async def login(
        self, alias: str, *, on_url: Callable[[str], Awaitable[None]]
    ) -> None:
        self.login_calls.append(alias)
        await on_url("https://auth.example.com/oauth")
        if self.error:
            raise self.error


def _make_app(registry: FakeLoginRegistry | None = None) -> MCPOAuthApp:
    return MCPOAuthApp(
        server_name="oauth", mcp_registry=cast(Any, registry or FakeLoginRegistry())
    )


def _wire_query(app: MCPOAuthApp) -> tuple[MagicMock, MagicMock, MagicMock]:
    option_list = MagicMock()
    option_list.get_option_index.return_value = 2
    detail = MagicMock()
    help_widget = MagicMock()

    def query(sel: object, *a: object, **kw: object) -> MagicMock:
        if sel is OptionList:
            return option_list
        s = str(sel)
        if "detail" in s:
            return detail
        if "help" in s:
            return help_widget
        return MagicMock()

    app.query_one = cast(Any, query)
    return option_list, detail, help_widget


class TestMCPOAuthApp:
    def test_widget_id(self) -> None:
        app = _make_app()
        assert app.id == "mcpoauth-app"

    def test_action_close_posts_cancelled_message(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()

        app.action_close()

        msg = app.post_message.call_args.args[0]
        assert isinstance(msg, MCPOAuthApp.MCPOAuthClosed)
        assert msg.refreshed is False
        assert msg.server_name == ""

    def test_auth_url_available_shows_menu(self) -> None:
        app = _make_app()
        option_list, _detail, _help = _wire_query(app)

        app._on_auth_url_available("https://auth.example.com/oauth")

        assert app._auth_url == "https://auth.example.com/oauth"
        assert option_list.clear_options.called
        assert option_list.add_option.call_count == 5
        option_ids = [
            call.args[0].id
            for call in option_list.add_option.call_args_list
            if hasattr(call.args[0], "id") and call.args[0].id
        ]
        assert _OAuthOptionId.OPEN in option_ids
        assert _OAuthOptionId.COPY in option_ids
        assert _OAuthOptionId.SHOW in option_ids

    @pytest.mark.asyncio
    async def test_run_login_starts_registry_login(self) -> None:
        registry = FakeLoginRegistry()
        app = _make_app(registry)
        _wire_query(app)

        result = await app._run_login()

        assert result == _LoginResult(authenticated=True)
        assert registry.login_calls == ["oauth"]
        assert app._auth_url == "https://auth.example.com/oauth"

    @pytest.mark.asyncio
    async def test_run_login_returns_error(self) -> None:
        registry = FakeLoginRegistry(error=ValueError("bad auth"))
        app = _make_app(registry)
        _wire_query(app)

        result = await app._run_login()

        assert result == _LoginResult(authenticated=False, error="bad auth")

    def test_worker_success_posts_closed(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()
        worker = MagicMock()
        worker.group = "mcp_oauth_login"
        worker.is_finished = True
        worker.result = _LoginResult(authenticated=True)
        event = MagicMock()
        event.worker = worker

        app.on_worker_state_changed(event)

        msg = app.post_message.call_args.args[0]
        assert isinstance(msg, MCPOAuthApp.MCPOAuthClosed)
        assert msg.refreshed is True
        assert msg.server_name == "oauth"

    def test_worker_failure_shows_retry_message(self) -> None:
        app = _make_app()
        _wire_query(app)
        app.post_message = MagicMock()
        worker = MagicMock()
        worker.group = "mcp_oauth_login"
        worker.is_finished = True
        worker.result = _LoginResult(authenticated=False, error="bad auth")
        event = MagicMock()
        event.worker = worker

        app.on_worker_state_changed(event)

        app.post_message.assert_not_called()
        assert app._status_message == "bad auth"

    def test_open_browser_calls_webbrowser(self) -> None:
        app = _make_app()
        app._auth_url = "https://auth.example.com/oauth"
        app.query_one = MagicMock()

        with patch("vibe.cli.textual_ui.widgets.mcp_oauth_app.webbrowser") as wb:
            app._open_browser()
            wb.open.assert_called_once_with("https://auth.example.com/oauth")

        assert app._status_message == "Opened in browser."

    def test_copy_url_calls_clipboard(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com/oauth"

        with (
            patch.object(
                type(app), "app", new_callable=lambda: property(lambda s: MagicMock())
            ),
            patch(
                "vibe.cli.textual_ui.widgets.mcp_oauth_app.copy_text_to_clipboard"
            ) as copy_fn,
        ):
            app._copy_url()

        copy_fn.assert_called_once()
        assert copy_fn.call_args.args[1] == "https://auth.example.com/oauth"
