from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.shortcut_hints import SHORTCUT_STYLE, shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.session.resume_sessions import ResumeSessionInfo, short_session_id

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400
_SECONDS_PER_WEEK = 604800
_DELETE_FEEDBACK_STYLE = "bold"
_DeleteStateKind = Literal["confirmation", "feedback", "pending"]


@dataclass(frozen=True)
class _DeleteState:
    kind: _DeleteStateKind
    option_id: str


def _format_relative_time(iso_time: str | None) -> str:
    if not iso_time:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = now - dt
        seconds = int(delta.total_seconds())

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
    except (ValueError, OSError):
        pass
    return "unknown"


def _build_header_text(cwd: str | None) -> Text:
    text = Text(no_wrap=True)
    text.append("local ", style="cyan")
    text.append(cwd or "this folder", style="dim")
    return text


def _build_option_text(session: ResumeSessionInfo, message: str) -> Text:
    text = Text(no_wrap=True)
    time_str = _format_relative_time(session.end_time)
    session_id = short_session_id(session.session_id)
    text.append(f"{time_str:10}", style="dim")
    text.append("  ")
    text.append(f"{session_id}  ", style="dim")
    text.append(message)
    return text


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

    class SessionDeleteRequested(Message):
        option_id: str
        session_id: str

        def __init__(self, option_id: str, session_id: str) -> None:
            self.option_id = option_id
            self.session_id = session_id
            super().__init__()

    def __init__(
        self,
        sessions: list[ResumeSessionInfo],
        latest_messages: dict[str, str],
        current_session_id: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(id="sessionpicker-app", **kwargs)
        self._sessions = sessions
        self._latest_messages = latest_messages
        self._current_session_id = current_session_id
        self._cwd = cwd
        self._delete_state: _DeleteState | None = None

    @property
    def has_sessions(self) -> bool:
        return bool(self._sessions)

    def _option_list(self) -> OptionList:
        return self.query_one(OptionList)

    def _session_by_option_id(self, option_id: str | None) -> ResumeSessionInfo | None:
        if option_id is None:
            return None

        return next(
            (session for session in self._sessions if session.option_id == option_id),
            None,
        )

    def _highlighted_option_id(self) -> str | None:
        option = self._option_list().highlighted_option
        if option is None or option.id is None:
            return None

        return str(option.id)

    def _highlighted_session(self) -> ResumeSessionInfo | None:
        return self._session_by_option_id(self._highlighted_option_id())

    def _session_message(self, session: ResumeSessionInfo) -> str:
        return self._latest_messages.get(session.option_id, "(empty session)")

    def _normal_option_text(self, session: ResumeSessionInfo) -> Text:
        return _build_option_text(session, self._session_message(session))

    def _option_text(self, session: ResumeSessionInfo) -> Text:
        state = self._delete_state
        if state is None or state.option_id != session.option_id:
            return self._normal_option_text(session)
        match state.kind:
            case "confirmation":
                return self._delete_confirmation_option_text(session)
            case "feedback":
                return self._delete_feedback_option_text(session)
            case "pending":
                return self._delete_pending_option_text(session)

    def _delete_confirmation_option_text(self, session: ResumeSessionInfo) -> Text:
        text = _build_option_text(session, "")
        text.append("Press ")
        text.append("d", style=SHORTCUT_STYLE)
        text.append(" again to delete")
        return text

    def _delete_feedback_option_text(self, session: ResumeSessionInfo) -> Text:
        text = _build_option_text(session, "")
        text.append(
            self._delete_feedback_message(session), style=_DELETE_FEEDBACK_STYLE
        )
        return text

    def _delete_feedback_message(self, session: ResumeSessionInfo) -> str:
        if session.session_id == self._current_session_id:
            return "Can't delete current session"

        return "Can't delete session"

    def _delete_pending_option_text(self, session: ResumeSessionInfo) -> Text:
        text = _build_option_text(session, "")
        text.append("Deleting...")
        return text

    def _restore_option_text(self, session: ResumeSessionInfo) -> None:
        self._option_list().replace_option_prompt(
            session.option_id, self._normal_option_text(session)
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
        self, session: ResumeSessionInfo, kind: _DeleteStateKind, prompt: Text
    ) -> None:
        self._clear_delete_state()
        self._delete_state = _DeleteState(kind=kind, option_id=session.option_id)
        self._option_list().replace_option_prompt(session.option_id, prompt)

    def remove_session(self, option_id: str) -> bool:
        session = self._session_by_option_id(option_id)
        if session is None:
            return False

        self._sessions = [s for s in self._sessions if s.option_id != option_id]
        self._latest_messages.pop(option_id, None)
        if self._delete_state_matches(option_id):
            self._delete_state = None
        self._option_list().remove_option(option_id)
        return True

    def add_sessions(
        self, sessions: list[ResumeSessionInfo], latest_messages: dict[str, str]
    ) -> None:
        existing = {s.option_id for s in self._sessions}
        new_sessions = [s for s in sessions if s.option_id not in existing]
        if not new_sessions:
            return

        self._sessions = sorted(
            [*self._sessions, *new_sessions],
            key=lambda s: s.end_time or "",
            reverse=True,
        )
        self._latest_messages.update(latest_messages)

        option_list = self._option_list()
        highlighted = self._highlighted_option_id()
        option_list.clear_options()
        option_list.add_options([
            Option(self._option_text(session), id=session.option_id)
            for session in self._sessions
        ])
        self._refresh_header()
        if highlighted is None:
            return
        for index, session in enumerate(self._sessions):
            if session.option_id == highlighted:
                option_list.highlighted = index
                return

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
            Option(self._normal_option_text(session), id=session.option_id)
            for session in self._sessions
        ]
        with Vertical(id="sessionpicker-content"):
            yield NoMarkupStatic(
                _build_header_text(self._cwd), classes="sessionpicker-header"
            )
            yield NavigableOptionList(*options, id="sessionpicker-options")
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                    f"{shortcut('d')} Delete  {shortcut('Esc')} Cancel"
                ),
                classes="sessionpicker-help",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if self._delete_is_pending():
            return

        option_id = str(event.option.id) if event.option.id is not None else None
        if self._delete_state is not None and self._delete_state.option_id != option_id:
            self._clear_delete_state()

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

        if session.session_id == self._current_session_id:
            self._show_delete_state(
                session, "feedback", self._delete_feedback_option_text(session)
            )
            return

        if self._delete_state_matches(session.option_id, "confirmation"):
            self._show_delete_state(
                session, "pending", self._delete_pending_option_text(session)
            )
            self.post_message(
                self.SessionDeleteRequested(
                    option_id=session.option_id, session_id=session.session_id
                )
            )
            return

        self._show_delete_state(
            session, "confirmation", self._delete_confirmation_option_text(session)
        )
