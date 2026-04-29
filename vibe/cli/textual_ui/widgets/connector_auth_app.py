from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING, ClassVar
import webbrowser

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.events import DescendantBlur
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from textual.worker import Worker

from vibe.cli.clipboard import copy_text_to_clipboard
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.tools.connectors import ConnectorRegistry

if TYPE_CHECKING:
    from vibe.core.tools.manager import ToolManager

_HELP = "Backspace Back"
_OPTION_PADDING = "  "


class _AuthOptionId(StrEnum):
    OPEN = auto()
    COPY = auto()
    SHOW = auto()


class ConnectorAuthApp(Container):
    """Bottom-panel app for authenticating a workspace connector."""

    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "close", "Back", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    class ConnectorAuthClosed(Message):
        def __init__(
            self, *, refreshed: bool = False, connector_name: str = ""
        ) -> None:
            super().__init__()
            self.refreshed = refreshed
            self.connector_name = connector_name

    def __init__(
        self,
        connector_name: str,
        connector_registry: ConnectorRegistry,
        tool_manager: ToolManager,
    ) -> None:
        super().__init__(id="connectorauth-app")
        self._connector_name = connector_name
        self._connector_registry = connector_registry
        self._tool_manager = tool_manager
        self._auth_url: str | None = None
        self._auth_url_visible = False
        self._status_message: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="connectorauth-content"):
            yield NoMarkupStatic("", id="connectorauth-title", classes="settings-title")
            yield NoMarkupStatic("")
            yield OptionList(id="connectorauth-options")
            yield NoMarkupStatic("", id="connectorauth-detail")
            yield NoMarkupStatic("", id="connectorauth-help", classes="settings-help")

    def on_mount(self) -> None:
        self.query_one("#connectorauth-title", NoMarkupStatic).update(
            f"Connector: {self._connector_name}"
        )
        option_list = self.query_one(OptionList)
        option_list.add_option(Option("Fetching authentication info...", disabled=True))
        self._set_help_text(_HELP)
        option_list.focus()
        self.run_worker(self._fetch_auth_url(), exclusive=True, group="auth_url")

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if option_id == _AuthOptionId.OPEN:
            self._open_browser()
        elif option_id == _AuthOptionId.COPY:
            self._copy_url()
        elif option_id == _AuthOptionId.SHOW:
            self._toggle_url()

    def action_close(self) -> None:
        self.post_message(self.ConnectorAuthClosed())

    async def action_refresh(self) -> None:
        self._status_message = "Refreshing connector..."
        self._set_help_text(_HELP)
        self.run_worker(
            self._refresh_connector(), exclusive=True, group="connector_refresh"
        )

    # ── workers ──────────────────────────────────────────────────────

    async def _fetch_auth_url(self) -> str | None:
        return await self._connector_registry.get_auth_url(self._connector_name)

    async def _refresh_connector(self) -> int:
        """Refresh connector tools. Returns the number of tools discovered."""
        from vibe.core.tools.manager import ToolManager

        new_tools = await self._connector_registry.refresh_connector_async(
            self._connector_name
        )
        if isinstance(self._tool_manager, ToolManager):
            await self._tool_manager.integrate_connectors_async()
        return len(new_tools)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "auth_url" and event.worker.is_finished:
            self._on_auth_url_fetched(event.worker.result)
        elif event.worker.group == "connector_refresh" and event.worker.is_finished:
            tool_count = (
                event.worker.result if isinstance(event.worker.result, int) else 0
            )
            self._on_connector_refreshed(tool_count)

    # ── auth UI ──────────────────────────────────────────────────────

    def _on_auth_url_fetched(self, result: object) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        auth_url = result if isinstance(result, str) else None
        if auth_url:
            self._auth_url = auth_url
            option_list.add_option(
                Option(
                    Text("This connector requires authentication", no_wrap=True),
                    disabled=True,
                )
            )
            option_list.add_option(Option("", disabled=True))
            option_list.add_option(
                Option(
                    Text(
                        f"{_OPTION_PADDING}Press enter to open auth in your browser",
                        no_wrap=True,
                    ),
                    id=_AuthOptionId.OPEN,
                )
            )
            option_list.add_option(
                Option(
                    Text(f"{_OPTION_PADDING}Copy URL to clipboard", no_wrap=True),
                    id=_AuthOptionId.COPY,
                )
            )
            option_list.add_option(
                Option(
                    Text(f"{_OPTION_PADDING}Manually show the URL", no_wrap=True),
                    id=_AuthOptionId.SHOW,
                )
            )
            option_list.highlighted = option_list.get_option_index(_AuthOptionId.OPEN)
            self._update_detail_text()
        else:
            self.query_one("#connectorauth-detail", NoMarkupStatic).update("")
            option_list.add_option(
                Option("This connector does not provide authentication", disabled=True)
            )
        self._set_help_text(_HELP)

    def _on_connector_refreshed(self, tool_count: int) -> None:
        if tool_count > 0:
            self._status_message = f"{tool_count} tools discovered."
            self.post_message(
                self.ConnectorAuthClosed(
                    refreshed=True, connector_name=self._connector_name
                )
            )
        else:
            self._status_message = (
                "No tools discovered. Authentication may still be pending, "
                "try again in a moment."
            )
            self._set_help_text(_HELP)

    # ── actions ──────────────────────────────────────────────────────

    def _open_browser(self) -> None:
        if self._auth_url is None:
            return
        webbrowser.open(self._auth_url)
        self._status_message = "Opened in browser."
        self._set_help_text(_HELP)

    def _copy_url(self) -> None:
        if self._auth_url is None:
            return
        copy_text_to_clipboard(
            self.app, self._auth_url, success_message="Auth URL copied to clipboard"
        )

    def _toggle_url(self) -> None:
        if self._auth_url is None:
            return
        self._auth_url_visible = not self._auth_url_visible
        self._update_detail_text()

    # ── helpers ──────────────────────────────────────────────────────

    def _update_detail_text(self) -> None:
        detail = self.query_one("#connectorauth-detail", NoMarkupStatic)
        parts: list[str] = []
        if self._auth_url_visible and self._auth_url:
            parts.append(self._auth_url)
            parts.append("")
        parts.append("Once authenticated, press R to refresh")
        detail.update("\n".join(parts))

    def _set_help_text(self, text: str) -> None:
        if self._status_message:
            text = f"{self._status_message}  {text}"
        self.query_one("#connectorauth-help", NoMarkupStatic).update(text)
