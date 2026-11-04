# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Read Session API state from the Runtime's public session state.

The Runtime keeps one public session state: a combined history of raw JSON
entries and the latest Turn only. The Session API needs fixed integer history
positions, a separate call and result entry for each tool, and the outcome of
every Turn. This module builds those views from the public state.

Two pieces of memory carry what one state cannot tell:

- ``HistoryIndex`` gives each entry a position the first time it is seen and
  never moves it.
- ``TurnLedger`` keeps each Turn's outcome once it is seen, because the public
  state replaces the latest Turn when the next one starts.

``SessionApiView`` saves and restores both as ``SessionApiFactsV1``.
``_session_api_entries`` converts each public entry.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    ApprovalCallback,
    CancelledTermination,
    CompletedTermination,
    FailedTermination,
    HistoryEntry,
    Page,
    PageRequest,
    ProtocolError,
    Session,
    SessionCallback,
    SessionState,
    Termination,
    TextContentBlock,
    TurnState,
    UserInputCallback,
)
from mistralai_vibe_local_harness.session_protocol import (
    BlockedSessionStatus,
    CompletedPublicTurn,
    FailedPublicTurn,
    InProgressPublicTurn,
    InterruptedPublicTurn,
    JsonObject,
    PublicSessionState,
    PublicTurn,
    RunningSessionStatus,
)
from mistralai_vibe_local_harness.vibe._session_api_entries import (
    ENTRY_BUILDERS,
    TOOL_CALL_SUFFIX,
    TOOL_RESULT_SUFFIX,
    convert_history_entry,
    model_tool_call_id,
    optional_str,
)
from mistralai_vibe_local_harness.vibe._storage import SessionApiFactsV1

_PAGE_CURSOR_PREFIX = "index:"


class SessionApiStateError(ValueError):
    """A request named a page cursor or Turn this Session cannot serve."""


@dataclass
class HistoryIndex:
    """Fixed Session API positions for public history entries.

    A position is assigned once, the first time an entry ID is seen, and never
    changes. A tool record reserves its call and result positions together, so
    a result that arrives after later entries keeps the position next to its
    call.
    """

    positions: dict[str, int] = field(default_factory=dict)
    next_index: int = 0

    def position(self, entry_id: str) -> int:
        existing = self.positions.get(entry_id)
        if existing is not None:
            return existing
        assigned = self.next_index
        self.positions[entry_id] = assigned
        self.next_index += 1
        return assigned

    def reserve_tool(self, entry_id: str) -> tuple[int, int]:
        call_id = entry_id + TOOL_CALL_SUFFIX
        result_id = entry_id + TOOL_RESULT_SUFFIX
        if call_id not in self.positions:
            self.position(call_id)
            self.position(result_id)
        return self.positions[call_id], self.positions[result_id]


@dataclass
class TurnRecord:
    """What the Session API reports for one Turn."""

    turn_id: str
    started_at: int | None
    completed_at: int | None
    termination: Termination | None

    def to_json(self) -> JsonObject:
        return {
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "termination": (
                cast(JsonValue, self.termination.model_dump(mode="json", by_alias=True))
                if self.termination is not None
                else None
            ),
        }


@dataclass
class TurnLedger:
    """Outcomes of every Turn seen, kept after the latest Turn moves on."""

    records: dict[str, TurnRecord] = field(default_factory=dict)
    # The saved form of each ended Turn. An outcome never changes once
    # recorded, so it is converted once rather than on every save.
    _ended_json: dict[str, JsonObject] = field(default_factory=dict)

    def observe(self, turn: PublicTurn | None) -> bool:
        """Record the latest Turn; True when that added a saved outcome.

        An ended Turn never goes back to running.
        """
        if turn is None:
            return False
        known = self.records.get(turn.id)
        if known is not None and known.termination is not None:
            return False
        record = _turn_record(turn)
        self._record(record)
        return record.termination is not None

    def _record(self, record: TurnRecord) -> None:
        self.records[record.turn_id] = record
        if record.termination is not None:
            self._ended_json[record.turn_id] = record.to_json()

    def to_json(self) -> dict[str, JsonObject]:
        return dict(self._ended_json)

    @classmethod
    def from_json(cls, value: Mapping[str, JsonObject]) -> "TurnLedger":
        ledger = cls()
        for turn_id, raw in value.items():
            termination = _termination_from_json(raw.get("termination"))
            if termination is None:
                continue
            ledger._record(
                TurnRecord(
                    turn_id=turn_id,
                    started_at=_optional_int(raw.get("startedAt")),
                    completed_at=_optional_int(raw.get("completedAt")),
                    termination=termination,
                )
            )
        return ledger


@dataclass
class TurnIndex:
    """Session API history entries grouped by Turn, as ``turn_states`` reads them.

    Built from a whole history in position order, or kept up to date one
    public entry at a time with ``replace``.
    """

    _turns: dict[str, dict[str, HistoryEntry]] = field(default_factory=dict)
    _turn_of: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, entries: Sequence[HistoryEntry]) -> "TurnIndex":
        """The index of ``entries``, which are in position order."""
        index = cls()
        for entry in entries:
            index._add(entry)
        return index

    def replace(self, entry_id: str, entries: Sequence[HistoryEntry]) -> set[str]:
        """Index ``entries``, the Session API form of public entry ``entry_id``.

        Returns the Turns whose entries changed, before and after.
        """
        changed: set[str] = set()
        kept = {entry.id for entry in entries}
        for api_id in (
            entry_id,
            entry_id + TOOL_CALL_SUFFIX,
            entry_id + TOOL_RESULT_SUFFIX,
        ):
            if api_id not in kept:
                changed.update(self._remove(api_id))
        for entry in entries:
            changed.update(self._remove(entry.id))
            changed.update(self._add(entry))
        return changed

    def has(self, turn_id: str) -> bool:
        return turn_id in self._turns

    def turn_ids(self) -> list[str]:
        """Every Turn, in the order of its first entry, for an index built by ``of``."""
        return list(self._turns)

    def first_index(self, turn_id: str) -> int:
        return min(entry.index for entry in self._turns[turn_id].values())

    def entry_ids(self, turn_id: str) -> list[str]:
        entries = self._turns.get(turn_id, {})
        return [entry.id for entry in sorted(entries.values(), key=_position)]

    def first_created(self, turn_id: str) -> int | None:
        entries = self._turns.get(turn_id)
        if not entries:
            return None
        return min(entry.created_at for entry in entries.values())

    def last_updated(self, turn_id: str) -> int | None:
        entries = self._turns.get(turn_id)
        if not entries:
            return None
        return max(entry.updated_at for entry in entries.values())

    def _add(self, entry: HistoryEntry) -> tuple[str, ...]:
        turn_id = entry.turn_id
        if turn_id is None:
            return ()
        self._turns.setdefault(turn_id, {})[entry.id] = entry
        self._turn_of[entry.id] = turn_id
        return (turn_id,)

    def _remove(self, api_id: str) -> tuple[str, ...]:
        turn_id = self._turn_of.pop(api_id, None)
        if turn_id is None:
            return ()
        entries = self._turns[turn_id]
        del entries[api_id]
        if not entries:
            del self._turns[turn_id]
        return (turn_id,)


def _position(entry: HistoryEntry) -> int:
    return entry.index


@dataclass(frozen=True, slots=True)
class _ConvertedEntry:
    """One public entry's Session API form, kept until the entry changes.

    The projector replaces an entry instead of mutating it, so an unchanged
    entry is usually the same object in the next state. A saved position and
    a tool call record never change, so they need no check.
    """

    source: JsonObject
    tool_call: JsonObject | None
    entries: list[HistoryEntry]

    def converts(self, raw: JsonObject, tool_call: JsonObject | None) -> bool:
        return tool_call is self.tool_call and (
            raw is self.source or raw == self.source
        )


@dataclass
class SessionApiView:
    """The memory one Session's Session API view keeps between reads.

    The Runtime saves the view's facts with its projection. ``unsaved_facts``
    returns what was added since the last save, and ``mark_saved`` records that
    a journal write now holds it.
    """

    history_index: HistoryIndex = field(default_factory=HistoryIndex)
    turns: TurnLedger = field(default_factory=TurnLedger)
    message_receipts: dict[str, JsonObject] = field(default_factory=dict)
    agent_config: JsonObject | None = None
    tool_calls: dict[str, JsonObject] = field(default_factory=dict)
    _saved_positions: set[str] = field(default_factory=set)
    _saved_turns: dict[str, JsonObject] = field(default_factory=dict)
    _saved_receipts: set[str] = field(default_factory=set)
    _saved_agent_config: JsonObject | None = None
    _saved_tool_calls: set[str] = field(default_factory=set)
    _converted: dict[str, _ConvertedEntry] = field(default_factory=dict)
    # ``all_facts`` until a fact changes: the Runtime asks for it on every
    # published state, and most states, such as streamed text, add none.
    _facts: SessionApiFactsV1 | None = None

    @classmethod
    def from_facts(cls, facts: SessionApiFactsV1 | None) -> "SessionApiView":
        """A view restored from saved facts, all of them marked saved."""
        if facts is None:
            return cls()
        view = cls(
            history_index=HistoryIndex(
                positions=dict(facts.history_positions),
                next_index=facts.next_history_index,
            ),
            turns=TurnLedger.from_json(facts.turn_outcomes),
            message_receipts=dict(facts.message_receipts),
            agent_config=facts.agent_config,
            tool_calls=dict(facts.tool_calls),
        )
        view.mark_saved(view.all_facts())
        return view

    def record_message_receipt(self, key: str, receipt: JsonObject) -> None:
        """Remember an accepted ``message/send`` so a retry returns it."""
        self.message_receipts[key] = receipt
        self._facts = None

    def record_agent_config(self, config: JsonObject) -> None:
        self.agent_config = config
        self._facts = None

    def record_tool_call(self, entry_id: str, call: JsonObject) -> None:
        """Keep a tool call's original ID and arguments before cleanup drops them."""
        if entry_id not in self.tool_calls:
            self.tool_calls[entry_id] = call
            self._facts = None

    def observe(self, state: PublicSessionState) -> None:
        """Assign positions to new entries and record the latest Turn.

        Cheaper than building the API entries: the Runtime calls it for every
        state it publishes, so the facts are ready before the state is saved.
        """
        next_index = self.history_index.next_index
        for raw in state.history.entries:
            entry_id = raw.get("id")
            if not isinstance(entry_id, str):
                continue
            if raw.get("type") == "effect":
                self.history_index.reserve_tool(entry_id)
            elif raw.get("type") in ENTRY_BUILDERS:
                self.history_index.position(entry_id)
        ended = self.turns.observe(state.latest_turn)
        if ended or self.history_index.next_index != next_index:
            self._facts = None

    def all_facts(self) -> SessionApiFactsV1:
        if self._facts is None:
            self._facts = SessionApiFactsV1(
                next_history_index=self.history_index.next_index,
                history_positions=dict(self.history_index.positions),
                turn_outcomes=self.turns.to_json(),
                message_receipts=dict(self.message_receipts),
                agent_config=self.agent_config,
                tool_calls=dict(self.tool_calls),
            )
        return self._facts

    def unsaved_facts(self) -> SessionApiFactsV1 | None:
        """The facts added since the last ``mark_saved``, or None if nothing is new."""
        positions = {
            entry_id: index
            for entry_id, index in self.history_index.positions.items()
            if entry_id not in self._saved_positions
        }
        outcomes = {
            turn_id: outcome
            for turn_id, outcome in self.turns.to_json().items()
            if self._saved_turns.get(turn_id) != outcome
        }
        receipts = {
            key: receipt
            for key, receipt in self.message_receipts.items()
            if key not in self._saved_receipts
        }
        tool_calls = {
            entry_id: call
            for entry_id, call in self.tool_calls.items()
            if entry_id not in self._saved_tool_calls
        }
        config = (
            self.agent_config if self.agent_config != self._saved_agent_config else None
        )
        if not (positions or outcomes or receipts or tool_calls or config):
            return None
        return SessionApiFactsV1(
            next_history_index=self.history_index.next_index,
            history_positions=positions,
            turn_outcomes=outcomes,
            message_receipts=receipts,
            agent_config=config,
            tool_calls=tool_calls,
        )

    def mark_saved(self, facts: SessionApiFactsV1 | None) -> None:
        if facts is None:
            return
        self._saved_positions.update(facts.history_positions)
        self._saved_turns.update(facts.turn_outcomes)
        self._saved_receipts.update(facts.message_receipts)
        self._saved_tool_calls.update(facts.tool_calls)
        if facts.agent_config is not None:
            self._saved_agent_config = facts.agent_config

    def history_entries(self, state: PublicSessionState) -> list[HistoryEntry]:
        """Every public history entry in Session API form, ordered by position.

        Read-only: positions are saved by ``observe`` only. An entry it has
        not seen, such as one in a state the Runtime has not published yet,
        is numbered after the known ones without keeping that number.
        """
        return self.numbered_history(state)[0]

    def numbered_history(
        self, state: PublicSessionState
    ) -> tuple[list[HistoryEntry], bool]:
        """``history_entries``, and whether every entry had a saved position.

        An entry numbered after the known ones may read differently once
        ``observe`` saves its position, so only a fully saved history converts
        the same way on the next read.
        """
        scratch: HistoryIndex | None = None
        converted: list[HistoryEntry] = []
        for raw in state.history.entries:
            entry_id = raw.get("id")
            if not isinstance(entry_id, str):
                continue
            positioned = self._positioned_entries(raw, entry_id)
            if positioned is not None:
                converted.extend(positioned)
                continue
            if scratch is None:
                scratch = HistoryIndex(
                    positions=dict(self.history_index.positions),
                    next_index=self.history_index.next_index,
                )
            converted.extend(convert_history_entry(raw, scratch, self.tool_calls))
        return sorted(converted, key=lambda entry: entry.index), scratch is None

    def published_entries(self, raw: JsonObject) -> list[HistoryEntry] | None:
        """One public entry in Session API form, or None if it has no position yet.

        An entry without an ID, or of a type the API does not show, has no
        Session API form. ``history_entries`` numbers an entry without a
        position after the known ones, which depends on the whole state, so
        such an entry is left to it.
        """
        entry_id = raw.get("id")
        entry_type = raw.get("type")
        if not isinstance(entry_id, str) or (
            entry_type != "effect" and entry_type not in ENTRY_BUILDERS
        ):
            return []
        return self._positioned_entries(raw, entry_id)

    def _positioned_entries(
        self, raw: JsonObject, entry_id: str
    ) -> list[HistoryEntry] | None:
        """``raw`` in Session API form, or None if it has no position yet."""
        original = self.tool_calls.get(entry_id)
        cached = self._converted.get(entry_id)
        if cached is not None and cached.converts(raw, original):
            if cached.source is not raw:
                # Keep the newer equal object so the next read matches it
                # by identity rather than by comparison.
                cached = replace(cached, source=raw)
                self._converted[entry_id] = cached
            return cached.entries
        if not self._has_position(raw, entry_id):
            return None
        cached = _ConvertedEntry(
            raw,
            original,
            convert_history_entry(raw, self.history_index, self.tool_calls),
        )
        # Kept by ID, so a read of only the latest entries keeps the rest.
        self._converted[entry_id] = cached
        return cached.entries

    def _has_position(self, raw: JsonObject, entry_id: str) -> bool:
        if raw.get("type") == "effect":
            entry_id += TOOL_CALL_SUFFIX
        return entry_id in self.history_index.positions

    def turn_states(
        self, state: PublicSessionState, entries: Sequence[HistoryEntry] | None = None
    ) -> list[TurnState]:
        """Every Turn the history knows, oldest first.

        ``entries`` is ``history_entries(state)`` when the caller has it already.
        Turn outcomes are recorded by ``observe`` only; a read of an older
        state must not change what is saved.
        """
        if entries is None:
            entries = self.history_entries(state)
        return self.turn_states_of(state, TurnIndex.of(entries))

    def turn_states_of(
        self, state: PublicSessionState, index: TurnIndex
    ) -> list[TurnState]:
        """``turn_states`` for the history ``index`` was built from with ``of``."""
        turn_ids = index.turn_ids()
        # A Turn with no history entry is listed only while it is the latest
        # Turn: one that has started but published nothing yet. A Turn whose
        # entries a rewind removed is gone, even though its outcome is saved.
        latest = state.latest_turn
        if latest is not None and not index.has(latest.id):
            turn_ids.append(latest.id)
        return [self.turn_state(state, index, turn_id) for turn_id in turn_ids]

    def turn_state(
        self, state: PublicSessionState, index: TurnIndex, turn_id: str
    ) -> TurnState:
        """One Turn of ``turn_states``, from the history grouped in ``index``.

        The state's own latest Turn is read from the state; older Turns from
        the outcomes ``observe`` saved.
        """
        latest = state.latest_turn
        record = (
            _turn_record(latest)
            if latest is not None and latest.id == turn_id
            else self.turns.records.get(turn_id)
        )
        return _turn_state(
            turn_id,
            index.entry_ids(turn_id),
            record,
            index.first_created(turn_id),
            index.last_updated(turn_id),
            blocked=turn_id == blocked_turn_id(state),
        )

    def session_state(
        self,
        state: PublicSessionState,
        *,
        history: PageRequest | None = None,
        turns: PageRequest | None = None,
    ) -> SessionState:
        entries = (
            self.history_entries(state)
            if history is not None or turns is not None
            else []
        )
        return SessionState(
            session=session_from_public(state),
            active_turn_id=active_turn_id(state),
            history=page(entries, history) if history is not None else None,
            turns=(
                page(self.turn_states(state, entries), turns)
                if turns is not None
                else None
            ),
            active_callbacks=active_callbacks(state, self.tool_calls),
        )


def _turn_state(
    turn_id: str,
    entry_ids: list[str],
    record: TurnRecord | None,
    first_created: int | None,
    last_updated: int | None,
    *,
    blocked: bool,
) -> TurnState:
    if record is not None:
        if record.termination is not None:
            status = "terminated"
        else:
            status = "blocked" if blocked else "running"
        return TurnState(
            id=turn_id,
            status=status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            history_entry_ids=entry_ids,
            termination=record.termination,
        )
    # The Runtime kept no outcome for this Turn. It ended before the latest
    # Turn, so it reads as completed, as the Runtime's own Turn list does.
    return TurnState(
        id=turn_id,
        status="terminated",
        started_at=first_created,
        completed_at=last_updated,
        history_entry_ids=entry_ids,
        termination=CompletedTermination(),
    )


def session_from_public(state: PublicSessionState) -> Session:
    public = state.session
    return Session(
        id=public.id,
        root_session_id=public.root_session_id or public.id,
        parent_session_id=public.parent_session_id,
        title=public.title,
        preview=public.preview,
        status="running" if active_turn_id(state) is not None else "waiting",
        created_at=public.created_at,
        updated_at=public.updated_at,
    )


def blocked_turn_id(state: PublicSessionState) -> str | None:
    """The Turn waiting on an approval or a user answer, if any."""
    status = state.session.status
    return status.active_turn_id if isinstance(status, BlockedSessionStatus) else None


def active_turn_id(state: PublicSessionState) -> str | None:
    status = state.session.status
    if isinstance(status, RunningSessionStatus | BlockedSessionStatus):
        return status.active_turn_id
    if isinstance(state.latest_turn, InProgressPublicTurn):
        return state.latest_turn.id
    return None


def active_callbacks(
    state: PublicSessionState, tool_calls: Mapping[str, JsonObject]
) -> list[SessionCallback]:
    return [
        callback
        for raw in state.active_callbacks
        if (callback := session_callback(raw, tool_calls)) is not None
    ]


def session_callback(
    raw: JsonObject, tool_calls: Mapping[str, JsonObject]
) -> SessionCallback | None:
    """The Session API form of one Runtime callback entry, or None if unknown."""
    detail = raw.get("detail")
    callback_id = raw.get("callbackId") or raw.get("id")
    if not isinstance(detail, Mapping) or not isinstance(callback_id, str):
        return None
    turn_id = optional_str(raw.get("turnId"))
    kind = detail.get("kind")
    if kind == "approval":
        effect = detail.get("effect")
        effect = effect if isinstance(effect, Mapping) else {}
        related = optional_str(detail.get("relatedEntryId")) or ""
        tool_name = optional_str(effect.get("toolName"))
        return ApprovalCallback(
            id=callback_id,
            turn_id=turn_id,
            tool_call_id=model_tool_call_id(related, tool_calls),
            name=tool_name,
            title=optional_str(raw.get("title")) or "Approval required",
            description=optional_str(detail.get("reason")) or "",
            input=cast(JsonValue, effect.get("input")),
        )
    if kind == "user_input":
        # The structured question request has no API field; it travels in
        # ``vibe.callback_details``.
        return UserInputCallback(
            id=callback_id,
            turn_id=turn_id,
            message=[TextContentBlock(text=optional_str(raw.get("title")) or "")],
        )
    return None


def page[ItemT: Any](items: Sequence[ItemT], request: PageRequest) -> Page[ItemT]:
    """One page of ``items``, which are ordered oldest first.

    A cursor names the position just past the page edge, so it stays valid
    while new items are appended.
    """
    offset = len(items) if request.direction == "backward" else 0
    if request.cursor is not None:
        offset = _cursor_offset(request.cursor, len(items))
    if request.direction == "backward":
        start = max(0, offset - request.limit)
        selected = list(reversed(items[start:offset]))
        return Page(
            items=selected,
            next_cursor=_cursor(start) if start > 0 else None,
            previous_cursor=_cursor(offset) if offset < len(items) else None,
        )
    selected = list(items[offset : offset + request.limit])
    end = offset + len(selected)
    return Page(
        items=selected,
        next_cursor=_cursor(end) if end < len(items) else None,
        previous_cursor=_cursor(offset) if offset > 0 else None,
    )


def _turn_record(turn: PublicTurn) -> TurnRecord:
    match turn:
        case InProgressPublicTurn():
            return TurnRecord(turn.id, turn.started_at, None, None)
        case CompletedPublicTurn():
            termination: Termination = CompletedTermination(
                stop_reason=(
                    "iteration_limit"
                    if turn.stop_reason == "limit"
                    else "skipped"
                    if turn.skip_reason is not None
                    else "rejected"
                    if turn.reject_reason is not None
                    else None
                ),
                # A skipped or rejected Turn has no answer: the hook's reason is
                # its output, never an earlier assistant entry.
                output=(
                    turn.skip_reason
                    if turn.skip_reason is not None
                    else turn.reject_reason
                ),
            )
        case FailedPublicTurn():
            termination = FailedTermination(
                error=ProtocolError(
                    message=turn.error.message,
                    code=turn.error.code,
                    details=turn.error.details,
                )
            )
        case InterruptedPublicTurn():
            termination = CancelledTermination(reason=turn.reason)
    return TurnRecord(turn.id, turn.started_at, turn.completed_at, termination)


def _termination_from_json(value: object) -> Termination | None:
    if not isinstance(value, Mapping):
        return None
    outcome = value.get("outcome")
    model = {
        "completed": CompletedTermination,
        "cancelled": CancelledTermination,
        "failed": FailedTermination,
    }.get(cast(str, outcome))
    if model is None:
        return None
    try:
        return model.model_validate(value)
    except ValueError:
        return None


def _cursor(offset: int) -> str:
    return f"{_PAGE_CURSOR_PREFIX}{offset}"


def _cursor_offset(cursor: str, length: int) -> int:
    raw = cursor.removeprefix(_PAGE_CURSOR_PREFIX)
    if raw == cursor or not raw.isascii() or not raw.isdecimal():
        raise SessionApiStateError(f"Invalid page cursor: {cursor!r}")
    return min(int(raw), length)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
