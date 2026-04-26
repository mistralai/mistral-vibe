from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.session.resume_sessions import (
    ResumeSessionInfo,
    ResumeSessionSource,
    short_session_id,
)

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400
_SECONDS_PER_WEEK = 604800


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


def _build_option_text(session: ResumeSessionInfo, message: str) -> Text:
    text = Text(no_wrap=True)
    time_str = _format_relative_time(session.end_time)
    session_id = short_session_id(session.session_id, source=session.source)
    source = session.source
    text.append(f"{time_str:10}", style="dim")
    text.append("  ")
    text.append(f"{source:6}", style="cyan")
    text.append("  ")
    text.append(f"{session_id}  ", style="dim")
    text.append(message)
    return text


class SessionPickerApp(Container):
    """Session picker for /resume command."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class SessionSelected(Message):
        option_id: str
        source: ResumeSessionSource
        session_id: str

        def __init__(
            self, option_id: str, source: ResumeSessionSource, session_id: str
        ) -> None:
            self.option_id = option_id
            self.source = source
            self.session_id = session_id
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(
        self,
        sessions: list[ResumeSessionInfo],
        latest_messages: dict[str, str],
        **kwargs: Any,
    ) -> None:
        super().__init__(id="sessionpicker-app", **kwargs)
        self._sessions = sessions
        self._latest_messages = latest_messages

    def compose(self) -> ComposeResult:
        options = [
            Option(
                _build_option_text(
                    session,
                    self._latest_messages.get(session.option_id, "(empty session)"),
                ),
                id=session.option_id,
            )
            for session in self._sessions
        ]
        with Vertical(id="sessionpicker-content"):
            yield OptionList(*options, id="sessionpicker-options")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select  Esc Cancel", classes="sessionpicker-help"
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            option_id = event.option.id
            source, _, session_id = option_id.partition(":")
            self.post_message(
                self.SessionSelected(
                    option_id=option_id,
                    source=cast(ResumeSessionSource, source),
                    session_id=session_id,
                )
            )

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
