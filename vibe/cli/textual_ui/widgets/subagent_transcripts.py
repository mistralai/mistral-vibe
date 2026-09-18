from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

from textual.containers import VerticalGroup
from textual.widget import Widget

from vibe.app_server.models import (
    BlockedSessionStatus,
    IdleSessionStatus,
    PublicChildSession,
    PublicHistoryEntry,
    PublicMessageEntry,
    RunningSessionStatus,
)
from vibe.cli.textual_ui.widgets.messages import (
    StreamingMessageBase,
    UserMessage,
    UserMessageSeverity,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.windowing import build_history_widgets

MAX_CACHED_SUBAGENT_TRANSCRIPTS = 5


@dataclass(slots=True)
class _Transcript:
    container: VerticalGroup
    history: list[PublicHistoryEntry] | None
    history_complete: bool = False
    local_user_messages: list[tuple[str, UserMessageSeverity | None]] = field(
        default_factory=list
    )


class SubagentTranscripts(VerticalGroup):
    """A bounded LRU of stable, lazily-mounted child transcript trees."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.display = False
        self._selected_session_id: str | None = None
        self._transcripts: OrderedDict[str, _Transcript] = OrderedDict()
        self._parent_instructions: dict[str, PublicMessageEntry] = {}
        self._active_session_ids: set[str] = set()
        self._render_lock = asyncio.Lock()

    def select(self, session_id: str | None) -> bool:
        previous_session_id = self._selected_session_id
        previous = self._transcripts.get(self._selected_session_id or "")
        if previous is not None:
            previous.container.display = False
            if previous_session_id != session_id:
                previous.history_complete = False

        self._selected_session_id = session_id
        self.display = session_id is not None
        if session_id is None:
            return True

        transcript = self._transcripts.get(session_id)
        if transcript is None:
            return False
        transcript.container.display = True
        self._transcripts.move_to_end(session_id)
        return True

    async def prepare(self, session_id: str) -> None:
        if session_id in self._transcripts:
            self._transcripts.move_to_end(session_id)
            return

        container = VerticalGroup(
            NoMarkupStatic("Loading subagent transcript…"),
            classes="subagent-transcript",
        )
        container.display = session_id == self._selected_session_id
        await self.mount(container)
        self._transcripts[session_id] = _Transcript(container=container, history=None)
        await self._evict_overflow()

    async def replace_history(
        self,
        session_id: str,
        history: Sequence[PublicHistoryEntry],
        *,
        tools_collapsed: bool,
        show_thinking: bool,
        history_complete: bool = False,
    ) -> bool:
        await self.prepare(session_id)
        transcript = self._transcripts[session_id]
        latest_history = list(history)
        self._remember_parent_instruction(session_id, latest_history)
        if (
            history_complete
            or transcript.history is None
            or not transcript.history_complete
        ):
            rendered_history = latest_history
        else:
            rendered_history = _merge_history_tail(transcript.history, latest_history)
        if parent_instruction := self._parent_instructions.get(session_id):
            if all(entry.id != parent_instruction.id for entry in rendered_history):
                rendered_history = [parent_instruction, *rendered_history]
        transcript.history_complete = history_complete or transcript.history_complete
        if transcript.history is not None and transcript.history == rendered_history:
            return False

        history_widget_indices: WeakKeyDictionary[Widget, int] = WeakKeyDictionary()
        widgets = build_history_widgets(
            rendered_history,
            start_index=0,
            history_widget_indices=history_widget_indices,
            tools_collapsed=tools_collapsed,
            show_thinking=show_thinking,
        )
        async with self._render_lock:
            widgets.extend(
                UserMessage(message, severity=severity)
                for message, severity in transcript.local_user_messages
            )
            if not widgets:
                widgets = [NoMarkupStatic("No transcript yet.")]
            with self.app.batch_update():
                await transcript.container.remove_children()
                await transcript.container.mount_all(widgets)
            transcript.history = rendered_history
        for widget in widgets:
            if isinstance(widget, StreamingMessageBase):
                await widget.write_initial_content()
        return True

    def needs_complete_history(
        self, session_id: str, latest_history: Sequence[PublicHistoryEntry]
    ) -> bool:
        transcript = self._transcripts.get(session_id)
        if transcript is None or not transcript.history_complete:
            return True
        if transcript.history is None or not latest_history:
            return False
        known_ids = {entry.id for entry in transcript.history}
        return not any(entry.id in known_ids for entry in latest_history)

    def remember_parent_instruction(self, instruction: PublicMessageEntry) -> None:
        if instruction.source != "turn_start":
            raise ValueError("Parent instruction must be a turn-start message")
        self._parent_instructions.setdefault(instruction.session_id, instruction)

    async def append_local_user_message(
        self,
        session_id: str,
        content: str,
        *,
        severity: UserMessageSeverity | None = None,
    ) -> None:
        await self.prepare(session_id)
        transcript = self._transcripts[session_id]
        async with self._render_lock:
            transcript.local_user_messages.append((content, severity))
            await transcript.container.mount(UserMessage(content, severity=severity))

    async def announce_ready_transition(
        self, session: PublicChildSession, *, message: str
    ) -> bool:
        if isinstance(session.status, (RunningSessionStatus, BlockedSessionStatus)):
            self._active_session_ids.add(session.id)
            return False
        if not isinstance(session.status, IdleSessionStatus):
            self._active_session_ids.discard(session.id)
            return False
        if session.id not in self._active_session_ids:
            return False

        self._active_session_ids.remove(session.id)
        await self.append_local_user_message(
            session.id, message, severity=UserMessageSeverity.INFO
        )
        return True

    async def show_error(self, session_id: str) -> None:
        await self.prepare(session_id)
        transcript = self._transcripts[session_id]
        if transcript.history is not None:
            return
        with self.app.batch_update():
            await transcript.container.remove_children()
            await transcript.container.mount(
                NoMarkupStatic("Unable to load this subagent transcript.")
            )

    async def clear(self) -> None:
        self._selected_session_id = None
        self.display = False
        await self.remove_children()
        self._transcripts.clear()
        self._parent_instructions.clear()
        self._active_session_ids.clear()

    def transcript_container(self, session_id: str) -> VerticalGroup | None:
        transcript = self._transcripts.get(session_id)
        return transcript.container if transcript is not None else None

    async def _evict_overflow(self) -> None:
        while len(self._transcripts) > MAX_CACHED_SUBAGENT_TRANSCRIPTS:
            session_id = next(
                candidate
                for candidate in self._transcripts
                if candidate != self._selected_session_id
            )
            transcript = self._transcripts.pop(session_id)
            await transcript.container.remove()

    def _remember_parent_instruction(
        self, session_id: str, history: Sequence[PublicHistoryEntry]
    ) -> None:
        instruction = next(
            (
                entry
                for entry in history
                if isinstance(entry, PublicMessageEntry)
                and entry.role == "user"
                and entry.source == "turn_start"
            ),
            None,
        )
        if instruction is not None:
            self._parent_instructions[session_id] = instruction


def _merge_history_tail(
    history: Sequence[PublicHistoryEntry], latest: Sequence[PublicHistoryEntry]
) -> list[PublicHistoryEntry]:
    if not latest:
        return []
    history_indices = {entry.id: index for index, entry in enumerate(history)}
    overlap_index = next(
        (history_indices[entry.id] for entry in latest if entry.id in history_indices),
        len(history),
    )
    return [*history[:overlap_index], *latest]
