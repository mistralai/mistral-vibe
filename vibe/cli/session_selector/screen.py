from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from vibe.core.interaction_logger import InteractionLogger, SessionListEntry

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


class SessionSelectorScreen(Screen[Path | None]):
    """Screen for selecting a session to resume."""

    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
    ]

    def __init__(self, config: SessionLoggingConfig, limit: int = 20) -> None:
        super().__init__()
        self._config = config
        self._limit = limit
        self._sessions: list[SessionListEntry] = []
        self._selected_index = 0
        self._session_widgets: list[Static] = []

    def compose(self) -> ComposeResult:
        with Container(id="selector-outer"):
            yield Static("Select a session to resume", id="selector-title")
            with VerticalScroll(id="session-list"):
                pass
            yield Static(
                "↑↓ navigate  Enter select  ESC cancel", id="selector-help"
            )

    def on_mount(self) -> None:
        self._sessions = InteractionLogger.list_sessions(self._config, self._limit)
        if not self._sessions:
            self.app.exit(None)
            return
        self._build_session_widgets()
        self._update_display()
        self.focus()

    def _build_session_widgets(self) -> None:
        session_list = self.query_one("#session-list", VerticalScroll)
        for _ in self._sessions:
            widget = Static("", classes="session-item")
            self._session_widgets.append(widget)
            session_list.mount(widget)

    def _shorten_path(self, path: str | None, max_length: int = 35) -> str:
        if not path:
            return "-"
        try:
            p = Path(path)
            home = Path.home()
            if p.is_relative_to(home):
                path = "~/" + str(p.relative_to(home))
        except (ValueError, RuntimeError):
            pass

        if len(path) > max_length:
            return "..." + path[-(max_length - 3) :]
        return path

    def _format_session(self, session: SessionListEntry, selected: bool) -> str:
        cursor = "> " if selected else "  "
        date_str = session.start_time.strftime("%Y-%m-%d %H:%M")
        workdir = self._shorten_path(session.working_directory)
        branch = f" [{session.git_branch}]" if session.git_branch else ""
        msgs = f"({session.message_count} msgs)"
        return f"{cursor}{session.session_id}  {date_str}  {workdir}{branch}  {msgs}"

    def _update_display(self) -> None:
        for i, (session, widget) in enumerate(
            zip(self._sessions, self._session_widgets, strict=True)
        ):
            is_selected = i == self._selected_index
            text = self._format_session(session, is_selected)
            widget.update(text)

            widget.remove_class("selected", "unselected")
            widget.add_class("selected" if is_selected else "unselected")

        if self._session_widgets:
            self._session_widgets[self._selected_index].scroll_visible()

    def _navigate(self, direction: int) -> None:
        self._selected_index = (self._selected_index + direction) % len(self._sessions)
        self._update_display()

    def action_move_up(self) -> None:
        self._navigate(-1)

    def action_move_down(self) -> None:
        self._navigate(1)

    def action_select(self) -> None:
        if self._sessions:
            self.app.exit(self._sessions[self._selected_index].filepath)

    def action_cancel(self) -> None:
        self.app.exit(None)

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)
