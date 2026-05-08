from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import OptionList

from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from vibe.cli.textual_ui.widgets.connector_auth_app import (
    ConnectorAuthApp,
    _AuthOptionId,
)
from vibe.core.tools.connectors.connector_registry import RemoteTool


def _make_registry(*, with_disconnected: bool = True) -> FakeConnectorRegistry:
    connectors: dict[str, list[RemoteTool]] = {
        "gmail": [RemoteTool(name="search", description="Search emails")]
    }
    if with_disconnected:
        connectors["slack"] = []
    return FakeConnectorRegistry(connectors=connectors)


def _make_app(
    connector_name: str = "slack", registry: FakeConnectorRegistry | None = None
) -> ConnectorAuthApp:
    reg = registry or _make_registry()
    mgr = MagicMock()
    return ConnectorAuthApp(
        connector_name=connector_name, connector_registry=reg, tool_manager=mgr
    )


class TestConnectorAuthAppInit:
    def test_widget_id(self) -> None:
        app = _make_app()
        assert app.id == "connectorauth-app"

    def test_stores_connector_name(self) -> None:
        app = _make_app(connector_name="gmail")
        assert app._connector_name == "gmail"

    def test_initial_state(self) -> None:
        app = _make_app()
        assert app._auth_url is None
        assert app._auth_url_visible is False
        assert app._status_message is None


class TestAuthUrlFetched:
    def test_auth_url_available_shows_menu(self) -> None:
        app = _make_app()
        option_list = MagicMock()
        option_list.get_option_index.return_value = 2
        detail = MagicMock()
        help_widget = MagicMock()

        def query(sel, *a, **kw):  # type: ignore[no-untyped-def]
            if sel is OptionList:
                return option_list
            s = str(sel)
            if "detail" in s:
                return detail
            if "help" in s:
                return help_widget
            return MagicMock()

        app.query_one = query  # type: ignore[assignment]

        app._on_auth_url_fetched("https://auth.example.com/oauth")

        assert app._auth_url == "https://auth.example.com/oauth"
        assert option_list.clear_options.called
        assert option_list.add_option.call_count == 5
        option_ids = [
            call.args[0].id
            for call in option_list.add_option.call_args_list
            if hasattr(call.args[0], "id") and call.args[0].id
        ]
        assert _AuthOptionId.OPEN in option_ids
        assert _AuthOptionId.COPY in option_ids
        assert _AuthOptionId.SHOW in option_ids

    def test_auth_url_none_shows_no_auth_message(self) -> None:
        app = _make_app()
        option_list = MagicMock()
        detail = MagicMock()
        help_widget = MagicMock()

        def query(sel, *a, **kw):  # type: ignore[no-untyped-def]
            if sel is OptionList:
                return option_list
            s = str(sel)
            if "detail" in s:
                return detail
            if "help" in s:
                return help_widget
            return MagicMock()

        app.query_one = query  # type: ignore[assignment]

        app._on_auth_url_fetched(None)

        assert app._auth_url is None
        last_option = option_list.add_option.call_args_list[-1].args[0]
        assert "does not provide authentication" in str(last_option.prompt)


class TestAuthActions:
    def test_open_browser_calls_webbrowser(self) -> None:
        app = _make_app()
        app._auth_url = "https://auth.example.com/oauth"
        app.query_one = MagicMock()

        with patch("vibe.cli.textual_ui.widgets.connector_auth_app.webbrowser") as wb:
            app._open_browser()
            wb.open.assert_called_once_with("https://auth.example.com/oauth")

        assert app._status_message == "Opened in browser."

    def test_open_browser_noop_without_url(self) -> None:
        app = _make_app()
        app._auth_url = None
        with patch("vibe.cli.textual_ui.widgets.connector_auth_app.webbrowser") as wb:
            app._open_browser()
            wb.open.assert_not_called()

    def test_copy_url_calls_clipboard(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com/oauth"

        with (
            patch.object(
                type(app), "app", new_callable=lambda: property(lambda s: MagicMock())
            ),
            patch(
                "vibe.cli.textual_ui.widgets.connector_auth_app.copy_text_to_clipboard"
            ) as copy_fn,
        ):
            app._copy_url()
            copy_fn.assert_called_once()
            assert copy_fn.call_args.args[1] == "https://auth.example.com/oauth"
            assert (
                copy_fn.call_args.kwargs["success_message"]
                == "Auth URL copied to clipboard"
            )

    def test_copy_url_noop_without_url(self) -> None:
        app = _make_app()
        app._auth_url = None
        with patch(
            "vibe.cli.textual_ui.widgets.connector_auth_app.copy_text_to_clipboard"
        ) as copy_fn:
            app._copy_url()
            copy_fn.assert_not_called()

    def test_toggle_url_shows_then_hides(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com/oauth"
        detail = MagicMock()
        app.query_one = lambda sel, *a, **kw: detail

        assert app._auth_url_visible is False

        app._toggle_url()
        assert app._auth_url_visible is True
        text = detail.update.call_args.args[0]
        assert "https://auth.example.com/oauth" in text
        assert "Once authenticated" in text

        app._toggle_url()
        assert app._auth_url_visible is False
        text = detail.update.call_args.args[0]
        assert "https://auth.example.com/oauth" not in text

    def test_toggle_url_noop_without_url(self) -> None:
        app = _make_app()
        app._auth_url = None
        app._toggle_url()
        assert app._auth_url_visible is False


class TestDetailText:
    def test_without_url_visible(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com/oauth"
        app._auth_url_visible = False
        detail = MagicMock()
        app.query_one = lambda sel, *a, **kw: detail

        app._update_detail_text()

        text = detail.update.call_args.args[0]
        assert "Once authenticated, press R to refresh" in text
        assert "https://auth.example.com" not in text

    def test_with_url_visible(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com/oauth"
        app._auth_url_visible = True
        detail = MagicMock()
        app.query_one = lambda sel, *a, **kw: detail

        app._update_detail_text()

        text = detail.update.call_args.args[0]
        assert "https://auth.example.com/oauth" in text
        assert "Once authenticated, press R to refresh" in text
        # URL should come before the footer
        url_pos = text.index("https://auth.example.com/oauth")
        footer_pos = text.index("Once authenticated")
        assert url_pos < footer_pos


class TestCloseAndRefresh:
    def test_action_close_posts_message(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()

        app.action_close()

        msg = app.post_message.call_args.args[0]
        assert isinstance(msg, ConnectorAuthApp.ConnectorAuthClosed)
        assert msg.refreshed is False

    def test_on_connector_refreshed_with_tools_posts_closed(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()

        app._on_connector_refreshed(3)

        msg = app.post_message.call_args.args[0]
        assert isinstance(msg, ConnectorAuthApp.ConnectorAuthClosed)
        assert msg.refreshed is True
        assert msg.connector_name == "slack"
        assert "3 tools" in (app._status_message or "")

    def test_on_connector_refreshed_without_tools_stays_on_page(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()
        app.query_one = MagicMock()

        app._on_connector_refreshed(0)

        app.post_message.assert_not_called()
        assert app._status_message is not None
        assert "pending" in app._status_message

    @pytest.mark.asyncio
    async def test_action_refresh_dispatches_worker(self) -> None:
        app = _make_app()
        app.run_worker = MagicMock()
        app.query_one = MagicMock()

        await app.action_refresh()

        assert app._status_message == "Refreshing connector..."
        app.run_worker.assert_called_once()
        _, kwargs = app.run_worker.call_args
        assert kwargs["group"] == "connector_refresh"


class TestOptionSelection:
    def test_auth_open_dispatches(self) -> None:
        app = _make_app()
        app._auth_url = "https://auth.example.com"
        app.query_one = MagicMock()
        event = MagicMock()
        event.option.id = _AuthOptionId.OPEN

        with patch("vibe.cli.textual_ui.widgets.connector_auth_app.webbrowser") as wb:
            app.on_option_list_option_selected(event)
            wb.open.assert_called_once()

    def test_auth_copy_dispatches(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com"
        event = MagicMock()
        event.option.id = _AuthOptionId.COPY

        with (
            patch.object(
                type(app), "app", new_callable=lambda: property(lambda s: MagicMock())
            ),
            patch(
                "vibe.cli.textual_ui.widgets.connector_auth_app.copy_text_to_clipboard"
            ) as copy_fn,
        ):
            app.on_option_list_option_selected(event)
            copy_fn.assert_called_once()

    def test_auth_show_dispatches(self) -> None:
        app = cast(Any, _make_app())
        app._auth_url = "https://auth.example.com"
        app.query_one = MagicMock()
        event = MagicMock()
        event.option.id = _AuthOptionId.SHOW

        app.on_option_list_option_selected(event)
        assert app._auth_url_visible is True

    def test_unknown_option_is_noop(self) -> None:
        app = _make_app()
        event = MagicMock()
        event.option.id = "something:else"
        # Should not raise
        app.on_option_list_option_selected(event)


class TestWorkerStateChanged:
    def test_auth_url_worker_dispatches(self) -> None:
        app = _make_app()
        app.query_one = MagicMock()
        worker = MagicMock()
        worker.group = "auth_url"
        worker.is_finished = True
        worker.result = "https://auth.example.com"
        event = MagicMock()
        event.worker = worker

        app.on_worker_state_changed(event)

        assert app._auth_url == "https://auth.example.com"

    def test_connector_refresh_worker_with_tools_posts_closed(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()
        worker = MagicMock()
        worker.group = "connector_refresh"
        worker.is_finished = True
        worker.result = 5
        event = MagicMock()
        event.worker = worker

        app.on_worker_state_changed(event)

        msg = app.post_message.call_args.args[0]
        assert isinstance(msg, ConnectorAuthApp.ConnectorAuthClosed)
        assert msg.refreshed is True

    def test_connector_refresh_worker_without_tools_stays(self) -> None:
        app = _make_app()
        app.post_message = MagicMock()
        app.query_one = MagicMock()
        worker = MagicMock()
        worker.group = "connector_refresh"
        worker.is_finished = True
        worker.result = 0
        event = MagicMock()
        event.worker = worker

        app.on_worker_state_changed(event)

        app.post_message.assert_not_called()
        assert "pending" in (app._status_message or "")

    def test_unrelated_worker_is_ignored(self) -> None:
        app = _make_app()
        worker = MagicMock()
        worker.group = "other"
        worker.is_finished = True
        event = MagicMock()
        event.worker = worker

        # Should not raise
        app.on_worker_state_changed(event)
