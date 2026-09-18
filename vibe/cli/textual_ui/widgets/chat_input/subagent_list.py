from __future__ import annotations

import re
from typing import Any

from textual import events
from textual.content import Content
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.app_server.models import (
    ArchivedSessionStatus,
    BlockedSessionStatus,
    FailedSessionStatus,
    IdleSessionStatus,
    PublicChildSession,
    RunningSessionStatus,
)
from vibe.cli.textual_ui.widgets.context_progress import format_token_count
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList

_MAIN_SESSION_ID = "\x00main"
_WORD_BOUNDARY = re.compile(r"[-_]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _display_name(value: str) -> str:
    if " " in value:
        return value
    return " ".join(
        word.capitalize() if word.islower() else word
        for word in _WORD_BOUNDARY.split(value)
        if word
    )


def _status(session: PublicChildSession) -> tuple[str, str]:
    match session.status:
        case RunningSessionStatus():
            return "running", "$success"
        case BlockedSessionStatus():
            return "blocked", "$warning"
        case FailedSessionStatus():
            return "failed", "$error"
        case ArchivedSessionStatus():
            return "stopped", "$text-muted"
        case IdleSessionStatus():
            return "ready", "$text-muted"
        case _:
            return "unknown", "$text-muted"


def _is_active(session: PublicChildSession) -> bool:
    return isinstance(session.status, (RunningSessionStatus, BlockedSessionStatus))


def subagent_loading_status(session: PublicChildSession) -> str | None:
    match session.status:
        case RunningSessionStatus():
            return "Running"
        case BlockedSessionStatus():
            return "Waiting for input"
        case _:
            return None


class SubagentList(NavigableOptionList):
    class Selected(Message):
        def __init__(self, session_id: str | None) -> None:
            self.session_id = session_id
            super().__init__()

    class FocusInputRequested(Message):
        pass

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.display = False
        self._sessions: tuple[PublicChildSession, ...] = ()
        self._selected_session_id: str | None = None
        self._known_session_ids: set[str] = set()
        self._batch_session_ids: set[str] = set()
        self._mouse_session_id: str | None = None
        self._marker_session_id: str | None = None

    def update_sessions(
        self,
        sessions: tuple[PublicChildSession, ...],
        *,
        selected_session_id: str | None = None,
    ) -> None:
        sessions = self._active_batch(sessions, selected_session_id)
        if (
            sessions == self._sessions
            and selected_session_id == self._selected_session_id
        ):
            return

        previous_sessions = {session.id: session for session in self._sessions}
        previous_ids = tuple(previous_sessions)
        previous_selected_session_id = self._selected_session_id
        current_ids = tuple(session.id for session in sessions)
        highlighted_id = self._highlighted_id()
        self._sessions = sessions
        self._selected_session_id = selected_session_id

        if not sessions:
            was_focused = self.has_focus
            self._mouse_session_id = None
            self._marker_session_id = None
            self.clear_options()
            self.display = False
            if was_focused:
                self.post_message(self.FocusInputRequested())
            return

        self.display = True
        if previous_ids != current_ids:
            if self._mouse_session_id not in {_MAIN_SESSION_ID, *current_ids}:
                self._mouse_session_id = None
            self.clear_options()
            self.add_options([
                Option(self._main_row(), id=_MAIN_SESSION_ID),
                *(
                    Option(self._child_row(session), id=session.id)
                    for session in sessions
                ),
            ])
        else:
            changed_ids = {
                session.id
                for session in sessions
                if previous_sessions[session.id] != session
            }
            if previous_selected_session_id != selected_session_id:
                self.replace_option_prompt(_MAIN_SESSION_ID, self._main_row())
                changed_ids.update(
                    session_id
                    for session_id in (
                        previous_selected_session_id,
                        selected_session_id,
                    )
                    if session_id is not None
                )
            for session in sessions:
                if session.id in changed_ids:
                    self.replace_option_prompt(session.id, self._child_row(session))

        selected_id = selected_session_id or _MAIN_SESSION_ID
        preferred_highlight_id = (
            highlighted_id
            if previous_selected_session_id == selected_session_id
            and highlighted_id in {_MAIN_SESSION_ID, *current_ids}
            else selected_id
        )
        highlighted_index = next(
            (
                index
                for index, session_id in enumerate((_MAIN_SESSION_ID, *current_ids))
                if session_id == preferred_highlight_id
            ),
            0,
        )
        if self.highlighted != highlighted_index:
            self.highlighted = highlighted_index
        self._sync_marker()
        selected = next(
            (session for session in sessions if session.id == selected_session_id), None
        )
        self.set_class(selected is not None, "subagent-view")
        self.border_title = (
            f" Viewing {_display_name(selected.name)} · read-only · Esc to return "
            if selected is not None
            else None
        )

    def focus_first(self) -> bool:
        if not self.display or self.option_count == 0:
            return False
        self._mouse_session_id = None
        self.highlighted = 0
        self.focus()
        return True

    def action_cursor_up(self) -> None:
        self._mouse_session_id = None
        if self.highlighted in {None, 0}:
            self._sync_marker()
            if self._selected_session_id is None:
                self.post_message(self.FocusInputRequested())
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        self._mouse_session_id = None
        if self.highlighted == self.option_count - 1:
            self._sync_marker()
            return
        super().action_cursor_down()

    def watch_highlighted(self, highlighted: int | None) -> None:
        super().watch_highlighted(highlighted)
        if self.option_count == 0:
            return
        self._sync_marker()

    def on_focus(self) -> None:
        self._set_marker(self._mouse_session_id or self._highlighted_id())

    def on_blur(self) -> None:
        self._set_marker(self._mouse_session_id)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        option_index = event.style.meta.get("option")
        if not isinstance(option_index, int) or isinstance(option_index, bool):
            self._mouse_session_id = None
            self._sync_marker()
            return
        if not 0 <= option_index < self.option_count:
            return
        option_id = self.get_option_at_index(option_index).id
        if option_id is None:
            return
        self._mouse_session_id = option_id
        self._sync_marker()

    def on_leave(self, _event: events.Leave) -> None:
        self._mouse_session_id = None
        self._sync_marker()

    def _highlighted_id(self) -> str | None:
        if self.highlighted is None or self.highlighted >= self.option_count:
            return None
        return self.get_option_at_index(self.highlighted).id

    def _active_batch(
        self, sessions: tuple[PublicChildSession, ...], selected_session_id: str | None
    ) -> tuple[PublicChildSession, ...]:
        current_ids = {session.id for session in sessions}
        new_ids = current_ids - self._known_session_ids
        self._known_session_ids = current_ids
        self._batch_session_ids.intersection_update(current_ids)

        active_ids = {session.id for session in sessions if _is_active(session)}
        if active_ids:
            self._batch_session_ids.update(active_ids)
            self._batch_session_ids.update(new_ids)
        elif selected_session_id not in self._batch_session_ids:
            self._batch_session_ids.clear()

        return tuple(
            session
            for session in sessions
            if session.id in self._batch_session_ids
            or isinstance(session.status, IdleSessionStatus)
        )

    def _main_row(self) -> Content:
        return self._row(
            Content.assemble(("Main conversation", "bold")),
            selected=self._selected_session_id is None,
            marked=self._marker_session_id == _MAIN_SESSION_ID,
        )

    def _child_row(self, session: PublicChildSession) -> Content:
        status, status_style = _status(session)
        context_tokens = (
            session.context_usage.total_tokens
            if session.context_usage is not None
            else 0
        )
        return self._row(
            Content.assemble(
                (_display_name(session.agent_type), "bold"),
                f" ({_display_name(session.name)}) ",
                (f"[{status}]", status_style),
                (f" · {format_token_count(context_tokens)} tokens", "$text-muted"),
            ),
            selected=session.id == self._selected_session_id,
            marked=self._marker_session_id == session.id,
        )

    @staticmethod
    def _row(label: Content, *, selected: bool, marked: bool) -> Content:
        marker = "> " if marked else "  "
        row = Content.assemble(marker, label)
        return row.stylize("$primary") if selected else row

    def _sync_marker(self) -> None:
        marker_session_id = self._mouse_session_id
        if marker_session_id is None and self.has_focus:
            marker_session_id = self._highlighted_id()
        self._set_marker(marker_session_id)

    def _set_marker(self, marker_session_id: str | None) -> None:
        if marker_session_id == self._marker_session_id:
            return
        previous_marker_session_id = self._marker_session_id
        self._marker_session_id = marker_session_id
        for session_id in (previous_marker_session_id, marker_session_id):
            if session_id == _MAIN_SESSION_ID and self.option_count:
                self.replace_option_prompt(_MAIN_SESSION_ID, self._main_row())
                continue
            session = next(
                (session for session in self._sessions if session.id == session_id),
                None,
            )
            if session is not None:
                self.replace_option_prompt(session.id, self._child_row(session))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        option_id = event.option.id
        if option_id is None:
            return
        session_id = None if option_id == _MAIN_SESSION_ID else option_id
        if session_id != self._selected_session_id:
            self.post_message(self.Selected(session_id))
