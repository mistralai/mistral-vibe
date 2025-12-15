from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


class Mode(StrEnum):
    LIST = auto()
    SEARCH = auto()
    RENAME = auto()
    DELETE = auto()


class SessionManagerApp(Static):
    can_focus = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False, priority=True)
    ]

    class SessionSwitched(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class ManagerClosed(Message):
        pass

    def __init__(self, config: SessionLoggingConfig, current_session_id: str) -> None:
        super().__init__(id="session-manager-app")
        self._config = config
        self._current_session_id = current_session_id
        self._mode = Mode.LIST
        self._sessions: list[dict] = []
        self._filtered: list[dict] = []
        self._cursor = 0
        self._input_buffer = ""
        self._target_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="session-manager-content"):
            yield Static(
                "Session Manager", id="sm-title", classes="session-manager-title"
            )
            yield Static("", id="sm-input", classes="session-search")
            yield VerticalScroll(id="sm-list")
            yield Static("", id="sm-status", classes="session-status")
            yield Static(
                self._help_text(), id="sm-help", classes="session-manager-help"
            )

    def on_mount(self) -> None:
        self._load_sessions()
        self._update_ui()
        self.focus()

    def _help_text(self) -> str:
        match self._mode:
            case Mode.LIST:
                return "(↑↓) navigate · (Enter) switch · (r) rename · (d) delete · (/) search · (Esc) close"
            case Mode.SEARCH:
                return "Type to filter · (Enter) select ·    (Esc) cancel"
            case Mode.RENAME:
                return "Type new name · (Enter) save · (Esc) cancel"
            case Mode.DELETE:
                return "(Enter)/(y) confirm · (n)/(Esc) cancel"

    def _load_sessions(self) -> None:
        try:
            from vibe.core.interaction_logger import InteractionLogger

            data = InteractionLogger.get_all_sessions(self._config)
            self._sessions = []

            for _i, entry in enumerate(data[:20]):
                meta = entry["metadata"]
                sid = meta.get("session_id", "")
                raw_time = meta.get("start_time", "")

                try:
                    dt = datetime.fromisoformat(raw_time) if raw_time else None
                    fmt_time = dt.strftime("%Y-%m-%d %H:%M") if dt else "Unknown"
                except (ValueError, TypeError):
                    fmt_time = raw_time or "Unknown"

                self._sessions.append({
                    "id": sid,
                    "name": meta.get("name") or f"Session {sid[:8]}",
                    "time": fmt_time,
                    "count": entry["message_count"],
                    "path": entry["filepath"],
                    "current": sid == self._current_session_id,
                })

            self._filtered = self._sessions.copy()
            self._set_status(f"Loaded {len(self._sessions)} sessions")
        except Exception as e:
            self._set_status(f"Error: {e}")

    def _update_ui(self) -> None:
        self._update_input()
        self._update_list()
        self._update_help()

    def _update_input(self) -> None:
        widget = self.query_one("#sm-input", Static)
        match self._mode:
            case Mode.SEARCH:
                widget.update(f"Search: {self._input_buffer}▌")
                widget.add_class("active")
            case Mode.RENAME:
                widget.update(f"New name: {self._input_buffer}▌")
                widget.add_class("active")
            case Mode.DELETE:
                selected = self._get_selected()
                name = selected["name"] if selected else "?"
                widget.update(f"⚠️  Delete '{name}'? (y/n)")
                widget.add_class("active")
            case _:
                widget.update("")
                widget.remove_class("active")

    def _update_list(self) -> None:
        container = self.query_one("#sm-list", VerticalScroll)
        container.remove_children()

        items = self._filtered if self._mode == Mode.SEARCH else self._sessions

        for i, session in enumerate(items):
            is_selected = i == self._cursor
            is_current = session["current"]

            marker = "▶" if is_selected else " "
            dot = "●" if is_current else "○"

            line1 = f"{marker} {dot} {session['name']}"
            line2 = f"    {session['time']} · {session['count']} messages"
            text = f"{line1}\n{line2}"

            classes = ["session-item"]
            if is_selected:
                classes.append("selected")
            if is_current:
                classes.append("current")

            item = Static(text, classes=" ".join(classes))
            container.mount(item)

    def _update_help(self) -> None:
        self.query_one("#sm-help", Static).update(self._help_text())

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#sm-status", Static).update(text)
        except Exception:
            pass

    def _get_selected(self) -> dict | None:
        items = self._filtered if self._mode == Mode.SEARCH else self._sessions
        if 0 <= self._cursor < len(items):
            return items[self._cursor]
        return None

    def _clamp_cursor(self) -> None:
        items = self._filtered if self._mode == Mode.SEARCH else self._sessions
        max_idx = len(items) - 1
        self._cursor = max(0, min(self._cursor, max_idx)) if max_idx >= 0 else 0

    def _filter_sessions(self) -> None:
        query = self._input_buffer.lower()
        if not query:
            self._filtered = self._sessions.copy()
        else:
            self._filtered = [
                s
                for s in self._sessions
                if query in f"{s['name']} {s['id']} {s['time']}".lower()
            ]
        self._cursor = 0
        self._clamp_cursor()
        count = len(self._filtered)
        self._set_status(f"Found {count} session{'s' if count != 1 else ''}")

    def action_close(self) -> None:
        match self._mode:
            case Mode.LIST:
                self.post_message(self.ManagerClosed())
            case Mode.SEARCH | Mode.RENAME | Mode.DELETE:
                self._mode = Mode.LIST
                self._input_buffer = ""
                self._target_id = None
                self._filtered = self._sessions.copy()
                self._clamp_cursor()
                self._set_status("Cancelled")
                self._update_ui()

    def on_key(self, event: events.Key) -> None:
        handled = self._handle_key(event.key, event.character)
        if handled:
            event.stop()
            event.prevent_default()

    def _handle_key(self, key: str, char: str | None) -> bool:
        # Handle ESC key in all modes
        if key == "escape":
            self.action_close()
            return True

        match self._mode:
            case Mode.LIST:
                return self._handle_list_key(key)
            case Mode.SEARCH:
                return self._handle_search_key(key, char)
            case Mode.RENAME:
                return self._handle_rename_key(key, char)
            case Mode.DELETE:
                return self._handle_delete_key(key)
        return False

    def _handle_list_key(self, key: str) -> bool:
        handled = False
        match key:
            case "up" | "k":
                if self._cursor > 0:
                    self._cursor -= 1
                    self._update_list()
                handled = True
            case "down" | "j":
                if self._cursor < len(self._sessions) - 1:
                    self._cursor += 1
                    self._update_list()
                handled = True
            case "enter":
                self._do_switch()
                handled = True
            case "r":
                self._start_rename()
                handled = True
            case "d":
                self._start_delete()
                handled = True
            case "slash" | "f":
                self._mode = Mode.SEARCH
                self._input_buffer = ""
                self._filtered = self._sessions.copy()
                self._cursor = 0
                self._set_status("Type to search...")
                self._update_ui()
                handled = True
        return handled

    def _handle_search_key(self, key: str, char: str | None) -> bool:
        match key:
            case "enter":
                selected = self._get_selected()
                if selected:
                    self._mode = Mode.LIST
                    real_idx = next(
                        (
                            i
                            for i, s in enumerate(self._sessions)
                            if s["id"] == selected["id"]
                        ),
                        0,
                    )
                    self._cursor = real_idx
                    self._input_buffer = ""
                    self._set_status(f"Selected: {selected['name']}")
                    self._update_ui()
                return True
            case "backspace":
                self._input_buffer = self._input_buffer[:-1]
                self._filter_sessions()
                self._update_ui()
                return True
            case "up" | "k":
                if self._cursor > 0:
                    self._cursor -= 1
                    self._update_list()
                return True
            case "down" | "j":
                if self._cursor < len(self._filtered) - 1:
                    self._cursor += 1
                    self._update_list()
                return True
            case _:
                if char and char.isprintable():
                    self._input_buffer += char
                    self._filter_sessions()
                    self._update_ui()
                    return True
        return False

    def _handle_rename_key(self, key: str, char: str | None) -> bool:
        match key:
            case "enter":
                self._do_rename()
                return True
            case "backspace":
                self._input_buffer = self._input_buffer[:-1]
                self._update_input()
                return True
            case _:
                if char and char.isprintable():
                    self._input_buffer += char
                    self._update_input()
                    return True
        return False

    def _handle_delete_key(self, key: str) -> bool:
        match key:
            case "y" | "enter":
                self._do_delete()
                return True
            case "n":
                self._mode = Mode.LIST
                self._target_id = None
                self._set_status("Deletion cancelled")
                self._update_ui()
                return True
        return False

    def _start_rename(self) -> None:
        selected = self._get_selected()
        if not selected:
            return

        self._target_id = selected["id"]
        self._input_buffer = ""
        self._mode = Mode.RENAME
        self._set_status(f"Renaming: {selected['name']}")
        self._update_ui()

    def _start_delete(self) -> None:
        selected = self._get_selected()
        if not selected:
            return

        if selected["current"]:
            self._set_status("Cannot delete current session")
            return

        self._target_id = selected["id"]
        self._mode = Mode.DELETE
        self._update_ui()

    def _do_switch(self) -> None:
        selected = self._get_selected()
        if not selected:
            return

        if selected["current"]:
            self._set_status("Already in this session")
            return

        self.post_message(self.SessionSwitched(selected["id"]))

    def _do_rename(self) -> None:
        new_name = self._input_buffer.strip()
        if not new_name:
            self._set_status("Name cannot be empty")
            return

        if not self._target_id:
            self._mode = Mode.LIST
            self._update_ui()
            return

        try:
            from vibe.core.interaction_logger import InteractionLogger

            path = InteractionLogger.find_session_by_id(self._target_id, self._config)
            if path:
                success = InteractionLogger.rename_session_file(Path(path), new_name)
                if success:
                    self._set_status(f"✓ Renamed to: {new_name}")
                    self._load_sessions()
                else:
                    self._set_status("✗ Failed to rename")
            else:
                self._set_status("✗ Session not found")
        except Exception as e:
            self._set_status(f"✗ Error: {e}")

        self._mode = Mode.LIST
        self._target_id = None
        self._input_buffer = ""
        self._update_ui()

    def _do_delete(self) -> None:
        if not self._target_id:
            self._mode = Mode.LIST
            self._update_ui()
            return

        try:
            from vibe.core.interaction_logger import InteractionLogger

            path = InteractionLogger.find_session_by_id(self._target_id, self._config)
            if path:
                Path(path).unlink()
                name = next(
                    (s["name"] for s in self._sessions if s["id"] == self._target_id),
                    "?",
                )
                self._set_status(f"✓ Deleted: {name}")
                self._load_sessions()
                self._clamp_cursor()
            else:
                self._set_status("✗ Session not found")
        except Exception as e:
            self._set_status(f"✗ Error: {e}")

        self._mode = Mode.LIST
        self._target_id = None
        self._update_ui()
