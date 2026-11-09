# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Session API events derived from the Runtime's public state updates.

The Runtime publishes its whole public state with every change. A client of the
Session API expects typed events instead: an entry was added, an entry changed,
a Turn started or ended, a callback opened or closed. ``SessionApiEventProducer``
compares each state with the previous one and emits those events.

``SessionApiEventLog`` keeps the produced events, and the ``vibe.*`` items
produced from the same updates, in memory with opaque IDs, so a client can read
the events after a bookmark and wait for new ones.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import secrets
from typing import cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    AnsweredCallbackOutcome,
    CallbackOpenedEvent,
    CallbackResolvedEvent,
    CancelledCallbackOutcome,
    Event,
    EventBatch,
    HistoryEntry,
    HistoryEntryAddedEvent,
    HistoryEntryCompletedEvent,
    HistoryEntryPatchedEvent,
    JsonPatchAppendOperation,
    JsonPatchOperation,
    JsonPatchReplaceOperation,
    Session,
    SessionCallback,
    SessionEvent,
    SessionUpdatedEvent,
    TurnStartedEvent,
    TurnState,
    TurnTerminatedEvent,
    TurnUpdatedEvent,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject, PublicSessionState
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    StreamEvent,
    StreamItem,
    VibeExtension,
    VibeExtensionEvent,
)
from mistralai_vibe_local_harness.vibe._session_api_state import (
    SessionApiView,
    TurnIndex,
    active_callbacks,
    active_turn_id,
    blocked_turn_id,
    session_callback,
    session_from_public,
)

DEFAULT_RETAINED_EVENTS = 2_000


class SessionApiEventGapError(LookupError):
    """The requested bookmark is older than the retained events or unknown.

    The client must read the Session again to get a fresh state and bookmark.
    """


@dataclass
class SessionApiEventProducer:
    """Turn successive public states into Session API events.

    The Runtime replaces a history entry instead of mutating it, so an update
    usually changes one or two entry objects of a long history. The producer
    keeps the previous public entries and converts, compares and groups only
    the ones that are not the same object any more. It reads the whole history
    instead when that shortcut cannot tell what changed: on the first update,
    when entries were removed or moved, and when an entry has no position yet.
    """

    session_id: str
    view: SessionApiView
    _entries: dict[str, HistoryEntry] = field(default_factory=dict)
    _session: Session | None = None
    _callbacks: dict[str, SessionCallback] = field(default_factory=dict)
    _blocked_turn_id: str | None = None
    _started_turns: set[str] = field(default_factory=set)
    _terminated_turns: set[str] = field(default_factory=set)
    # The public entries of the last published state, and their Session API
    # entries grouped by Turn. None until a full read found every entry with a
    # saved position.
    _published: Sequence[JsonObject] | None = None
    _published_tool_calls: int = 0
    _turn_index: TurnIndex = field(default_factory=TurnIndex)

    def seed(self, state: PublicSessionState) -> None:
        """Remember ``state`` as already published, without emitting events."""
        entries, numbered = self.view.numbered_history(state)
        self._entries = {entry.id: entry for entry in entries}
        self._session = session_from_public(state)
        self._callbacks = {
            callback.id: callback
            for callback in active_callbacks(state, self.view.tool_calls)
        }
        self._blocked_turn_id = blocked_turn_id(state)
        turns = self.view.turn_states(state, entries)
        self._started_turns = {turn.id for turn in turns}
        self._terminated_turns = {
            turn.id for turn in turns if turn.status == "terminated"
        }
        self._remember(state, entries if numbered else None)

    def state_updated(self, state: PublicSessionState) -> list[SessionEvent]:
        """The events that turn the last published state into ``state``."""
        changed = self._changed_entries(state)
        if changed is None:
            entries, numbered = self.view.numbered_history(state)
            index = TurnIndex.of(entries)
            turn_states = {
                turn.id: turn for turn in self.view.turn_states_of(state, index)
            }
            self._remember(state, entries if numbered else None)
        else:
            entries = changed
            turn_states = self._affected_turn_states(state, changed)
            self._remember(state, None, incremental=True)
        current_turn = active_turn_id(state)
        # Turns this producer may announce: those with a known outcome, and the
        # state's own latest Turn. Older Turns without one are only inferred.
        known = set(self.view.turns.records)
        if state.latest_turn is not None:
            known.add(state.latest_turn.id)
        events = self._started_turn_events(turn_states, current_turn, known)
        events.extend(self._history_events(entries))
        events.extend(self._callback_events(state))
        events.extend(self._blocked_turn_events(state, turn_states))
        events.extend(self._terminated_turn_events(turn_states, known))
        session = session_from_public(state)
        if session != self._session:
            self._session = session
            events.append(SessionUpdatedEvent(session=session))
        return events

    def _remember(
        self,
        state: PublicSessionState,
        entries: Sequence[HistoryEntry] | None,
        *,
        incremental: bool = False,
    ) -> None:
        """Remember ``state`` as the base of the next update's comparison.

        ``entries`` is the whole history in Session API form after a full read,
        or None when a full read left an entry without a saved position: the
        next update then reads the whole history again. After an incremental
        update, ``_changed_entries`` already brought the Turn index up to date.
        """
        self._published_tool_calls = len(self.view.tool_calls)
        if incremental:
            self._published = state.history.entries
            return
        if entries is None:
            self._published = None
            return
        self._published = state.history.entries
        self._turn_index = TurnIndex.of(entries)

    def _changed_entries(self, state: PublicSessionState) -> list[HistoryEntry] | None:
        """The Session API entries of the public entries ``state`` replaced.

        None when only a full read can tell: there is no previous state,
        entries were removed or moved, or a changed entry has no
        position yet. A replaced entry equal to the old one converts to the
        same entries, which ``_history_events`` then skips. An entry whose
        tool call the view recorded since converts anew too, even unreplaced.
        """
        previous = self._published
        current = state.history.entries
        if previous is None or len(current) < len(previous):
            return None
        # Tool calls are only ever added, so the new ones are the last keys.
        tool_calls = self.view.tool_calls
        recorded = (
            set(list(tool_calls)[self._published_tool_calls :])
            if len(tool_calls) != self._published_tool_calls
            else set()
        )
        changed: list[HistoryEntry] = []
        for position, raw in enumerate(current):
            if position < len(previous):
                old = previous[position]
                if raw is old and (not recorded or raw.get("id") not in recorded):
                    continue
                if raw.get("id") != old.get("id"):
                    return None
            converted = self.view.published_entries(raw)
            if converted is None:
                return None
            changed.extend(converted)
            entry_id = raw.get("id")
            if isinstance(entry_id, str):
                self._turn_index.replace(entry_id, converted)
        changed.sort(key=lambda entry: entry.index)
        return changed

    def _affected_turn_states(
        self, state: PublicSessionState, changed: Sequence[HistoryEntry]
    ) -> dict[str, TurnState]:
        """Turn states for the Turns whose events may change, oldest first.

        Those are the Turns ``changed`` touched, the state's latest, active and
        blocked Turns, the Turn blocked before, and the Turns this producer
        announced but has not seen end. Every other Turn is terminated and
        already reported, or has not changed since the last published state,
        so it produces no event.
        """
        index = self._turn_index
        turn_ids = {entry.turn_id for entry in changed if entry.turn_id is not None}
        latest = state.latest_turn
        turn_ids.update(
            turn_id
            for turn_id in (
                latest.id if latest is not None else None,
                self._blocked_turn_id,
                blocked_turn_id(state),
                active_turn_id(state),
            )
            if turn_id is not None
        )
        turn_ids.update(
            turn_id
            for turn_id in self._started_turns - self._terminated_turns
            if index.has(turn_id)
        )
        ordered = sorted(
            (turn_id for turn_id in turn_ids if index.has(turn_id)),
            key=index.first_index,
        )
        if latest is not None and not index.has(latest.id):
            ordered.append(latest.id)
        return {
            turn_id: self.view.turn_state(state, index, turn_id) for turn_id in ordered
        }

    def callback_requested(self, raw: JsonObject) -> list[SessionEvent]:
        callback = session_callback(raw, self.view.tool_calls)
        if callback is None or callback.id in self._callbacks:
            return []
        self._callbacks[callback.id] = callback
        return [CallbackOpenedEvent(callback=callback)]

    def callback_resolved(self, raw: JsonObject) -> list[SessionEvent]:
        callback_id = cast(str, raw["callbackId"])
        if (callback := self._callbacks.pop(callback_id, None)) is None:
            return []
        return [
            _callback_resolved_event(
                callback_id, cast(JsonObject, raw["state"]), callback.turn_id
            )
        ]

    def _history_events(self, entries: Sequence[HistoryEntry]) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        for entry in entries:
            previous = self._entries.get(entry.id)
            self._entries[entry.id] = entry
            if previous is None:
                events.append(HistoryEntryAddedEvent(entry=entry))
                continue
            if previous is entry or previous == entry:
                continue
            if entry.generation_status == "complete":
                events.append(HistoryEntryCompletedEvent(entry=entry))
            else:
                events.append(
                    HistoryEntryPatchedEvent(
                        entry_id=entry.id,
                        entry_index=entry.index,
                        patches=_entry_patches(previous, entry),
                    )
                )
        return events

    def _callback_events(self, state: PublicSessionState) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        current = {
            callback.id: callback
            for callback in active_callbacks(state, self.view.tool_calls)
        }
        for callback_id, callback in current.items():
            if callback_id not in self._callbacks:
                events.append(CallbackOpenedEvent(callback=callback))
        for callback_id, gone in self._callbacks.items():
            if callback_id not in current:
                # Gone without a resolution event of its own: the Runtime
                # dropped it, which reads as cancelled.
                events.append(
                    CallbackResolvedEvent(
                        callback_id=callback_id,
                        outcome=CancelledCallbackOutcome(),
                        turn_id=gone.turn_id,
                    )
                )
        self._callbacks = current
        return events

    def _blocked_turn_events(
        self, state: PublicSessionState, turn_states: Mapping[str, TurnState]
    ) -> list[SessionEvent]:
        """``turn_updated`` for each live Turn that became blocked or running again."""
        blocked = blocked_turn_id(state)
        if blocked == self._blocked_turn_id:
            return []
        changed = (self._blocked_turn_id, blocked)
        self._blocked_turn_id = blocked
        events: list[SessionEvent] = []
        for turn_id in changed:
            turn = turn_states.get(turn_id) if turn_id is not None else None
            if (
                turn is not None
                and turn.status != "terminated"
                and turn.id in self._started_turns
            ):
                events.append(TurnUpdatedEvent(turn=turn))
        return events

    def _started_turn_events(
        self,
        turn_states: Mapping[str, TurnState],
        current_turn: str | None,
        known: set[str],
    ) -> list[SessionEvent]:
        """Announce every Turn this producer has not announced yet.

        A short Turn can start and end between two published states, so the
        first state that shows it may already show it finished. Its start is
        still announced, before its end, so every terminated Turn was started.
        """
        new_turns = [
            turn_id
            for turn_id in turn_states
            if turn_id in known and turn_id not in self._started_turns
        ]
        if current_turn is not None and current_turn not in new_turns:
            if current_turn not in self._started_turns:
                new_turns.append(current_turn)
        events: list[SessionEvent] = []
        for turn_id in new_turns:
            self._started_turns.add(turn_id)
            turn = turn_states.get(turn_id)
            events.append(
                TurnStartedEvent(
                    turn=TurnState(
                        id=turn_id,
                        status="running",
                        started_at=turn.started_at if turn is not None else None,
                    )
                )
            )
        return events

    def _terminated_turn_events(
        self, turn_states: Mapping[str, TurnState], known: set[str]
    ) -> list[SessionEvent]:
        events: list[SessionEvent] = []
        for turn in turn_states.values():
            if turn.status != "terminated" or turn.id in self._terminated_turns:
                continue
            self._terminated_turns.add(turn.id)
            if turn.id in known:
                events.append(TurnTerminatedEvent(turn=turn))
        return events


@dataclass(frozen=True, slots=True)
class _LoggedEvent:
    sequence: int
    append: int
    """The append that added the item: the items of one Runtime update share it."""
    event: StreamEvent


class SessionApiEventLog:
    """Recent Session API and ``vibe.*`` events with opaque IDs, kept in memory.

    Event IDs combine a random log identity with a sequence number shared by
    both kinds, so one ID orders them all. A bookmark from another log, for
    example one taken before the process restarted, is reported as a gap
    instead of being misread as a position in this log.
    """

    def __init__(
        self,
        session_id: str,
        *,
        root_session_id: str | None = None,
        retained: int = DEFAULT_RETAINED_EVENTS,
        clock: Callable[[], int],
    ) -> None:
        """``root_session_id`` names the tree's root; a root Session omits it."""
        self._session_id = session_id
        self._root_session_id = root_session_id or session_id
        self._log_id = secrets.token_hex(4)
        self._retained = retained
        self._clock = clock
        self._events: list[_LoggedEvent] = []
        self._next_sequence = 1
        self._next_append = 1
        self._changed = asyncio.Condition()
        self._closed = False

    @property
    def last_event_id(self) -> str:
        return self._event_id(self._next_sequence - 1)

    async def append(self, items: Sequence[StreamItem]) -> list[StreamEvent]:
        """Add the items of one Runtime update, in the order given."""
        if not items:
            return []
        append = self._next_append
        self._next_append += 1
        emitted_at = self._clock()
        appended: list[StreamEvent] = []
        for item in items:
            if isinstance(item, VibeExtension):
                appended.append(
                    VibeExtensionEvent(
                        event_id=self._take_event_id(),
                        emitted_at=emitted_at,
                        session_id=self._session_id,
                        type=item.type,
                        data=item.data,
                    )
                )
                continue
            appended.append(
                Event(
                    event_id=self._take_event_id(),
                    emitted_at=emitted_at,
                    session_id=self._session_id,
                    root_session_id=self._root_session_id,
                    payload=item,
                )
            )
        first = self._next_sequence - len(appended)
        self._events.extend(
            _LoggedEvent(first + offset, append, event)
            for offset, event in enumerate(appended)
        )
        self._drop_beyond_retained()
        async with self._changed:
            self._changed.notify_all()
        return appended

    def read_after(self, after_event_id: str | None, limit: int) -> list[Event]:
        """Retained API events strictly after ``after_event_id``, oldest first.

        ``vibe.*`` items are skipped, so a plain Session API client reads only
        the events it knows.
        """
        events: list[Event] = []
        for logged in self._events[self._start(after_event_id) :]:
            if len(events) == limit:
                break
            if isinstance(logged.event, Event):
                events.append(logged.event)
        return events

    def read_append_after(self, after_event_id: str | None) -> tuple[StreamEvent, ...]:
        """The items of the first append after ``after_event_id``, or none yet.

        After a bookmark inside an append, this is the rest of that append.
        """
        start = self._start(after_event_id)
        end = start
        while (
            end < len(self._events)
            and self._events[end].append == self._events[start].append
        ):
            end += 1
        return tuple(logged.event for logged in self._events[start:end])

    async def follow_appends(
        self, after_event_id: str | None
    ) -> AsyncIterator[tuple[StreamEvent, ...]]:
        """Yield each append after the bookmark, waiting for new ones until closed."""
        cursor = after_event_id
        while True:
            async with self._changed:
                while not self._closed and not self.read_append_after(cursor):
                    await self._changed.wait()
            items = self.read_append_after(cursor)
            if items:
                cursor = items[-1].event_id
                yield items
                continue
            if self._closed:
                return

    async def follow(
        self, after_event_id: str | None, batch_size: int
    ) -> AsyncIterator[EventBatch]:
        """Yield API event batches after the bookmark, waiting until closed."""
        cursor = after_event_id
        while True:
            async with self._changed:
                while not self._closed and not self.read_after(cursor, 1):
                    await self._changed.wait()
            batch = self.read_after(cursor, batch_size)
            if batch:
                cursor = batch[-1].event_id
                yield EventBatch(events=batch)
                continue
            if self._closed:
                return

    async def close(self) -> None:
        self._closed = True
        async with self._changed:
            self._changed.notify_all()

    def _event_id(self, sequence: int) -> str:
        return f"{self._log_id}:{sequence}"

    def _take_event_id(self) -> str:
        sequence = self._next_sequence
        self._next_sequence += 1
        return self._event_id(sequence)

    def _drop_beyond_retained(self) -> None:
        if len(self._events) > self._retained:
            del self._events[: len(self._events) - self._retained]

    def _start(self, after_event_id: str | None) -> int:
        """The index of the first retained item after ``after_event_id``."""
        after = self._sequence(after_event_id)
        oldest_kept = self._events[0].sequence if self._events else self._next_sequence
        if after < oldest_kept - 1:
            raise SessionApiEventGapError(
                f"Event {after_event_id!r} is older than the retained events"
            )
        # Sequences are contiguous, so the first newer item's place is known.
        return after - oldest_kept + 1

    def _sequence(self, event_id: str | None) -> int:
        if event_id is None:
            return 0
        log_id, separator, raw_sequence = event_id.partition(":")
        if (
            not separator
            or log_id != self._log_id
            or not raw_sequence.isascii()
            or not raw_sequence.isdecimal()
        ):
            raise SessionApiEventGapError(f"Unknown event ID {event_id!r}")
        sequence = int(raw_sequence)
        if sequence >= self._next_sequence:
            raise SessionApiEventGapError(f"Event {event_id!r} was never emitted")
        return sequence


def _entry_patches(
    previous: HistoryEntry, current: HistoryEntry
) -> list[JsonPatchOperation]:
    """Append only the new text when streaming grew it; otherwise replace fields.

    Streaming changes one text field and ``updatedAt``. Sending the appended
    text keeps each event small however long the answer gets.
    """
    before = previous.model_dump(mode="json", by_alias=True)
    after = current.model_dump(mode="json", by_alias=True)
    patches: list[JsonPatchOperation] = []
    for path, old, new in _changed_fields(before, after):
        if isinstance(old, str) and isinstance(new, str) and new.startswith(old):
            patches.append(JsonPatchAppendOperation(path=path, value=new[len(old) :]))
        else:
            patches.append(JsonPatchReplaceOperation(path=path, value=new))
    return patches


def _changed_fields(
    before: JsonValue, after: JsonValue, path: str = ""
) -> list[tuple[str, JsonValue, JsonValue]]:
    if (
        isinstance(before, dict)
        and isinstance(after, dict)
        and before.keys() == after.keys()
    ):
        changed: list[tuple[str, JsonValue, JsonValue]] = []
        for key, value in after.items():
            changed.extend(
                _changed_fields(before[key], value, f"{path}/{_escape(key)}")
            )
        return changed
    if (
        isinstance(before, list)
        and isinstance(after, list)
        and len(before) == len(after)
    ):
        changed = []
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            changed.extend(_changed_fields(old, new, f"{path}/{index}"))
        return changed
    return [] if before == after else [(path, before, after)]


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _callback_resolved_event(
    callback_id: str, state: JsonObject, turn_id: str | None
) -> SessionEvent:
    """``state`` is the Runtime's settled callback state: answered or cancelled.

    ``turn_id`` is the turn the callback's opening named.
    """
    if state["status"] == "cancelled":
        outcome = CancelledCallbackOutcome(reason=cast(str, state["reason"]))
    else:
        outcome = AnsweredCallbackOutcome(output=state.get("output"))
    return CallbackResolvedEvent(
        callback_id=callback_id, outcome=outcome, turn_id=turn_id
    )
