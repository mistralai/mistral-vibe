from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.app_server.models import PublicSession, SavedSessionSummary
from vibe.cli.textual_ui.shortcut_hints import SHORTCUT_STYLE, shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400
_SECONDS_PER_WEEK = 604800
_DELETE_FEEDBACK_STYLE = "bold"
_DeleteStateKind = Literal["confirmation", "feedback", "pending"]
type _PickerSession = PublicSession | SavedSessionSummary


@dataclass(frozen=True)
class _DeleteState:
    kind: _DeleteStateKind
    option_id: str


def _session_datetime(timestamp: int | str | None) -> datetime | None:
    try:
        if isinstance(timestamp, int):
            return datetime.fromtimestamp(timestamp / 1000, UTC)
        if isinstance(timestamp, str):
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _format_relative_time(timestamp: int | str | None) -> str:
    if (dt := _session_datetime(timestamp)) is None:
        return "unknown"

    seconds = int((datetime.now(UTC) - dt).total_seconds())
    if seconds < _SECONDS_PER_MINUTE:
        return "just now"
    for threshold, divisor, unit in [
        (_SECONDS_PER_HOUR, _SECONDS_PER_MINUTE, "m"),
        (_SECONDS_PER_DAY, _SECONDS_PER_HOUR, "h"),
        (_SECONDS_PER_WEEK, _SECONDS_PER_DAY, "d"),
        (float("inf"), _SECONDS_PER_WEEK, "w"),
    ]:
        if seconds < threshold:
            return f"{seconds // divisor}{unit} ago"
    return "unknown"


def _session_id(session: _PickerSession) -> str:
    if isinstance(session, PublicSession):
        return session.id
    return session.session_id


def _session_updated_at(session: _PickerSession) -> int | str | None:
    if isinstance(session, PublicSession):
        return session.updated_at
    return session.end_time


def _session_sort_key(session: _PickerSession) -> float:
    if (updated_at := _session_datetime(_session_updated_at(session))) is None:
        return 0
    return updated_at.timestamp()


def _build_header_text(cwd: str | None) -> Text:
    text = Text(no_wrap=True)
    text.append("local ", style="cyan")
    text.append(cwd or "this folder", style="dim")
    return text


def _build_option_text(session: _PickerSession, message: str) -> Content:
    time_str = _format_relative_time(_session_updated_at(session))
    session_id = _session_id(session)[:8]
    return Content.assemble(
        (f"{time_str:10}", "dim"), "  ", (f"{session_id}  ", "dim"), message
    )


class SessionPickerApp(Container):
    """Session picker for /resume command."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("d", "request_delete", "Delete", show=False),
    ]

    class SessionSelected(Message):
        option_id: str
        session_id: str

        def __init__(self, option_id: str, session_id: str) -> None:
            self.option_id = option_id
            self.session_id = session_id
            super().__init__()

    class Cancelled(Message):
        pass

    class SessionHighlighted(Message):
        session_id: str | None

        def __init__(self, session_id: str | None) -> None:
            self.session_id = session_id
            super().__init__()

    class SessionDeleteRequested(Message):
        option_id: str
        session_id: str

        def __init__(self, option_id: str, session_id: str) -> None:
            self.option_id = option_id
            self.session_id = session_id
            super().__init__()

    def __init__(
        self,
        sessions: Sequence[_PickerSession],
        latest_messages: dict[str, str],
        current_session_id: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(id="sessionpicker-app", **kwargs)
        self._sessions = list(sessions)
        self._latest_messages = latest_messages
        self._current_session_id = current_session_id
        self._cwd = cwd
        self._delete_state: _DeleteState | None = None
        self._initial_highlighted: int | None = next(
            (i for i, s in enumerate(sessions) if _session_id(s) == current_session_id),
            None,
        )

    @property
    def has_sessions(self) -> bool:
        return bool(self._sessions)

    def _option_list(self) -> OptionList:
        return self.query_one(OptionList)

    def _session_by_option_id(self, option_id: str | None) -> _PickerSession | None:
        if option_id is None:
            return None

        return next(
            (
                session
                for session in self._sessions
                if _session_id(session) == option_id
            ),
            None,
        )

    def _highlighted_option_id(self) -> str | None:
        option = self._option_list().highlighted_option
        if option is None or option.id is None:
            return None

        return str(option.id)

    def _highlighted_session(self) -> _PickerSession | None:
        return self._session_by_option_id(self._highlighted_option_id())

    def _session_message(self, session: _PickerSession) -> str:
        return self._latest_messages.get(_session_id(session), "(empty session)")

    def _normal_option_text(self, session: _PickerSession) -> Content:
        return _build_option_text(session, self._session_message(session))

    def _option_text(self, session: _PickerSession) -> Content:
        state = self._delete_state
        if state is None or state.option_id != _session_id(session):
            return self._normal_option_text(session)
        match state.kind:
            case "confirmation":
                return self._delete_confirmation_option_text(session)
            case "feedback":
                return self._delete_feedback_option_text(session)
            case "pending":
                return self._delete_pending_option_text(session)

    def _delete_confirmation_option_text(self, session: _PickerSession) -> Content:
        return _build_option_text(session, "") + Content.assemble(
            "Press ", ("d", SHORTCUT_STYLE), " again to delete"
        )

    def _delete_feedback_option_text(self, session: _PickerSession) -> Content:
        return _build_option_text(session, "") + Content.styled(
            self._delete_feedback_message(session), _DELETE_FEEDBACK_STYLE
        )

    def _delete_feedback_message(self, session: _PickerSession) -> str:
        if _session_id(session) == self._current_session_id:
            return "Can't delete current session"

        return "Can't delete session"

    def _delete_pending_option_text(self, session: _PickerSession) -> Content:
        return _build_option_text(session, "") + Content("Deleting...")

    def _restore_option_text(self, session: _PickerSession) -> None:
        self._option_list().replace_option_prompt(
            _session_id(session), self._normal_option_text(session)
        )

    def _delete_state_matches(
        self, option_id: str, kind: _DeleteStateKind | None = None
    ) -> bool:
        if self._delete_state is None or self._delete_state.option_id != option_id:
            return False
        if kind is not None and self._delete_state.kind != kind:
            return False
        return True

    def _delete_is_pending(self) -> bool:
        return self._delete_state is not None and self._delete_state.kind == "pending"

    def _clear_delete_state(self) -> None:
        state = self._delete_state
        if state is None:
            return

        self._delete_state = None
        if session := self._session_by_option_id(state.option_id):
            self._restore_option_text(session)

    def _show_delete_state(
        self, session: _PickerSession, kind: _DeleteStateKind, prompt: Content
    ) -> None:
        self._clear_delete_state()
        session_id = _session_id(session)
        self._delete_state = _DeleteState(kind=kind, option_id=session_id)
        self._option_list().replace_option_prompt(session_id, prompt)

    def remove_session(self, option_id: str) -> bool:
        session = self._session_by_option_id(option_id)
        if session is None:
            return False

        self._sessions = [
            session for session in self._sessions if _session_id(session) != option_id
        ]
        self._latest_messages.pop(option_id, None)
        if self._delete_state_matches(option_id):
            self._delete_state = None
        option_list = self._option_list()
        option_list.remove_option(option_id)
        # Textual doesn't fire OptionHighlighted when the highlight moves due to
        # removal, so notify the app manually.
        option = option_list.highlighted_option
        new_id = (
            str(option.id) if option is not None and option.id is not None else None
        )
        self.post_message(self.SessionHighlighted(session_id=new_id))
        return True

    def add_sessions(
        self, sessions: list[PublicSession], latest_messages: dict[str, str]
    ) -> None:
        existing = {_session_id(session) for session in self._sessions}
        new_sessions = [
            session for session in sessions if _session_id(session) not in existing
        ]
        if not new_sessions:
            return

        self._sessions = sorted(
            [*self._sessions, *new_sessions], key=_session_sort_key, reverse=True
        )
        self._latest_messages.update(latest_messages)

        option_list = self._option_list()
        highlighted = self._highlighted_option_id()
        option_list.clear_options()
        option_list.add_options([
            Option(self._option_text(session), id=_session_id(session))
            for session in self._sessions
        ])
        self._refresh_header()
        if highlighted is None:
            return
        for index, session in enumerate(self._sessions):
            if _session_id(session) == highlighted:
                option_list.highlighted = index
                return

    def load_sessions(
        self, sessions: list[PublicSession], latest_messages: dict[str, str]
    ) -> None:
        """Populate the picker after initial mount. Highlights current session if present."""
        self.add_sessions(sessions, latest_messages)
        option_list = self._option_list()
        if option_list.highlighted is not None:
            return
        target_id = self._current_session_id
        for index, session in enumerate(self._sessions):
            if target_id is not None and _session_id(session) == target_id:
                option_list.highlighted = index
                return
        if self._sessions:
            option_list.highlighted = 0

    def _refresh_header(self) -> None:
        header = self.query_one(".sessionpicker-header", NoMarkupStatic)
        header.update(_build_header_text(self._cwd))

    def clear_pending_delete(self, option_id: str) -> bool:
        if not self._delete_state_matches(option_id, "pending"):
            return False

        self._clear_delete_state()
        return True

    def compose(self) -> ComposeResult:
        options = [
            Option(self._normal_option_text(session), id=_session_id(session))
            for session in self._sessions
        ]
        with Vertical(id="sessionpicker-content"):
            yield NoMarkupStatic(
                _build_header_text(self._cwd), classes="sessionpicker-header"
            )
            option_list = NavigableOptionList(*options, id="sessionpicker-options")
            if self._initial_highlighted is not None:
                option_list.highlighted = self._initial_highlighted
            yield option_list
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                    f"{shortcut('d')} Delete  {shortcut('Esc')} Cancel"
                ),
                classes="sessionpicker-help",
            )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.focus()
        option = option_list.highlighted_option
        initial_id = (
            str(option.id) if option is not None and option.id is not None else None
        )
        self.post_message(self.SessionHighlighted(session_id=initial_id))

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if self._delete_is_pending():
            return

        option_id = str(event.option.id) if event.option.id is not None else None
        if self._delete_state is not None and self._delete_state.option_id != option_id:
            self._clear_delete_state()
        self.post_message(self.SessionHighlighted(session_id=option_id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._delete_is_pending():
            return

        if event.option.id:
            option_id = event.option.id
            if self._delete_state_matches(option_id, "confirmation"):
                return

            self.post_message(
                self.SessionSelected(option_id=option_id, session_id=option_id)
            )

    def action_cancel(self) -> None:
        if self._delete_is_pending():
            return

        if self._delete_state is not None:
            self._clear_delete_state()
            return

        self.post_message(self.Cancelled())

    def action_request_delete(self) -> None:
        if self._delete_is_pending():
            return

        session = self._highlighted_session()
        if session is None:
            return

        session_id = _session_id(session)
        if session_id == self._current_session_id:
            self._show_delete_state(
                session, "feedback", self._delete_feedback_option_text(session)
            )
            return

        if self._delete_state_matches(session_id, "confirmation"):
            self._show_delete_state(
                session, "pending", self._delete_pending_option_text(session)
            )
            self.post_message(
                self.SessionDeleteRequested(option_id=session_id, session_id=session_id)
            )
            return

        self._show_delete_state(
            session, "confirmation", self._delete_confirmation_option_text(session)
        )
