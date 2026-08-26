from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vibe.app_server._model import validate_wire
from vibe.app_server._patch import apply_json_patch, make_json_patch
from vibe.app_server.models import (
    JsonPatchOperation,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    ConnectorAuthRequiredParams,
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    MCPAuthRequiredParams,
    Notification,
    ServerErrorParams,
    ServerWarningParams,
    SessionCompactedParams,
    SessionContextClearedParams,
    SessionSnapshotParams,
    SessionUpdatedParams,
    StatsUpdatedParams,
    TurnCompletedParams,
    TurnRetryingParams,
    TurnStartedParams,
)


@dataclass(frozen=True, slots=True)
class HistoryEntryAdded:
    entry: PublicHistoryEntry


@dataclass(frozen=True, slots=True)
class HistoryEntryUpdated:
    previous: PublicHistoryEntry
    entry: PublicHistoryEntry
    patch: list[JsonPatchOperation]


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: PublicSessionState


@dataclass(frozen=True, slots=True)
class SessionCompacted:
    params: SessionCompactedParams


@dataclass(frozen=True, slots=True)
class SessionContextCleared:
    params: SessionContextClearedParams


@dataclass(frozen=True, slots=True)
class SessionUpdated:
    previous: PublicSession
    session: PublicSession
    patch: list[JsonPatchOperation]


@dataclass(frozen=True, slots=True)
class TurnStarted:
    turn: PublicTurn


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    turn: PublicTurn


@dataclass(frozen=True, slots=True)
class StatsUpdated:
    params: StatsUpdatedParams


@dataclass(frozen=True, slots=True)
class CallbackRequested:
    callback: PublicCallbackEntry


@dataclass(frozen=True, slots=True)
class TurnRetrying:
    params: TurnRetryingParams


@dataclass(frozen=True, slots=True)
class ServerWarning:
    params: ServerWarningParams


@dataclass(frozen=True, slots=True)
class ServerError:
    params: ServerErrorParams


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRequiredEvent:
    params: MCPAuthRequiredParams


@dataclass(frozen=True, slots=True)
class ConnectorAuthorizationRequiredEvent:
    params: ConnectorAuthRequiredParams
    raw_connector_id: str | None = None
    action: str | None = None


type AppServerEvent = (
    HistoryEntryAdded
    | HistoryEntryUpdated
    | SessionSnapshot
    | SessionCompacted
    | SessionContextCleared
    | SessionUpdated
    | TurnStarted
    | TurnCompleted
    | StatsUpdated
    | CallbackRequested
    | TurnRetrying
    | ServerWarning
    | ServerError
    | MCPAuthorizationRequiredEvent
    | ConnectorAuthorizationRequiredEvent
)

_STREAMING_TEXT_PATHS = {"/content/0/text", "/text", "/state/outputText"}


def _merge_snapshot_suffix[ItemT](
    previous: list[ItemT] | None,
    current: list[ItemT] | None,
    *,
    item_id: Callable[[ItemT], str],
) -> tuple[list[ItemT] | None, bool]:
    if current is None:
        return previous, previous is not None
    if not previous or not current:
        return current, False

    first_current_id = item_id(current[0])
    overlap_start = next(
        (
            index
            for index, item in enumerate(previous)
            if item_id(item) == first_current_id
        ),
        None,
    )
    if overlap_start is None:
        return current, False

    overlap = previous[overlap_start:]
    if len(overlap) > len(current):
        return current, False
    if [item_id(item) for item in overlap] != [
        item_id(item) for item in current[: len(overlap)]
    ]:
        return current, False
    return [*previous[:overlap_start], *current], overlap_start > 0


def reconcile_snapshot(
    previous: PublicSessionState, current: PublicSessionState
) -> list[AppServerEvent]:
    events: list[AppServerEvent] = [SessionSnapshot(current)]
    if previous.session != current.session:
        events.append(
            SessionUpdated(
                previous=previous.session,
                session=current.session,
                patch=make_json_patch(
                    previous.session.model_dump(mode="json", by_alias=True),
                    current.session.model_dump(mode="json", by_alias=True),
                ),
            )
        )

    previous_entries = {entry.id: entry for entry in previous.history or []}
    for entry in current.history or []:
        prior = previous_entries.get(entry.id)
        if prior is None:
            events.append(HistoryEntryAdded(entry))
            continue
        if prior == entry:
            continue
        events.append(
            HistoryEntryUpdated(
                previous=prior,
                entry=entry,
                patch=make_json_patch(
                    prior.model_dump(mode="json", by_alias=True),
                    entry.model_dump(mode="json", by_alias=True),
                    append_paths=_STREAMING_TEXT_PATHS,
                ),
            )
        )

    previous_turns = {turn.id: turn for turn in previous.turns or []}
    for current_turn in current.turns or []:
        previous_turn = previous_turns.get(current_turn.id)
        if current_turn == previous_turn:
            continue
        if current_turn.status is PublicTurnStatus.IN_PROGRESS:
            events.append(TurnStarted(current_turn))
        else:
            events.append(TurnCompleted(current_turn))
    return events


type _KnownEventParams = (
    HistoryEntryAddedParams
    | HistoryEntryUpdatedParams
    | SessionSnapshotParams
    | SessionCompactedParams
    | SessionContextClearedParams
    | SessionUpdatedParams
    | StatsUpdatedParams
    | TurnCompletedParams
    | TurnStartedParams
)


class EventSequenceError(RuntimeError):
    pass


class UnknownNotificationError(RuntimeError):
    pass


def parse_server_event(
    notification: Notification,
) -> (
    ServerWarning
    | ServerError
    | TurnRetrying
    | MCPAuthorizationRequiredEvent
    | ConnectorAuthorizationRequiredEvent
    | None
):
    match notification.method:
        case "warning":
            return ServerWarning(
                validate_wire(ServerWarningParams, notification.params)
            )
        case "error":
            return ServerError(validate_wire(ServerErrorParams, notification.params))
        case "turn/retrying":
            return TurnRetrying(validate_wire(TurnRetryingParams, notification.params))
        case "mcp_catalog/authRequired":
            return MCPAuthorizationRequiredEvent(
                validate_wire(MCPAuthRequiredParams, notification.params)
            )
        case "connector_catalog/authRequired":
            return ConnectorAuthorizationRequiredEvent(
                validate_wire(ConnectorAuthRequiredParams, notification.params)
            )
        case _:
            return None


class ClientProjection:
    def __init__(self, state: PublicSessionState) -> None:
        self.state = state
        self._entries = {entry.id: entry for entry in state.history or []}
        self._last_event_id = state.event_id

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self.state.history or []

    @property
    def history_before_cursor(self) -> str | None:
        return self.state.history_before_cursor

    def replace_state(self, state: PublicSessionState) -> None:
        self._replace_state(state)

    def prepend_history_page(self, entries: list[PublicHistoryEntry]) -> None:
        page_entries: list[PublicHistoryEntry] = []
        page_ids: set[str] = set()
        for entry in entries:
            if entry.id in page_ids:
                raise ValueError(f"Duplicate paged history entry: {entry.id}")
            page_ids.add(entry.id)
            if existing := self._entries.get(entry.id):
                if existing != entry:
                    raise ValueError(f"Conflicting paged history entry: {entry.id}")
                continue
            self._entries[entry.id] = entry
            page_entries.append(entry)
        if self.state.history is None:
            self.state.history = []
        self.state.history[:0] = page_entries

    def ensure_callback(self, callback: PublicCallbackEntry) -> bool:
        if callback.id in self._entries:
            return False
        self._add_entry(callback)
        return True

    def begin_turn(self, turn: PublicTurn) -> None:
        if turn.session_id != self.state.session.id:
            raise EventSequenceError(
                f"Turn belongs to session {turn.session_id!r}, "
                f"expected {self.state.session.id!r}"
            )
        self._replace_turn(turn)

    def consume(self, notification: Notification) -> AppServerEvent | None:
        params = _parse_event_params(notification)
        if isinstance(params, SessionCompactedParams | SessionContextClearedParams):
            self._validate_handoff(params)
            self._replace_state(params.state)
            if isinstance(params, SessionCompactedParams):
                return SessionCompacted(params)
            return SessionContextCleared(params)
        if isinstance(params, SessionSnapshotParams):
            self._validate_snapshot(params)
        event_id = self._next_event_id(params.event_id, params.session_id)
        if event_id is None:
            return None
        event = self._apply_event(params)
        self._last_event_id = event_id
        return event

    def _apply_event(self, params: _KnownEventParams) -> AppServerEvent | None:
        event: AppServerEvent | None
        match params:
            case SessionSnapshotParams():
                self._apply_snapshot(params.state)
                event = SessionSnapshot(self.state)
            case SessionUpdatedParams():
                event = self._update_session(params)
            case HistoryEntryAddedParams():
                self._add_entry(params.entry)
                event = HistoryEntryAdded(params.entry)
            case HistoryEntryUpdatedParams():
                event = self._update_entry(params)
            case TurnStartedParams():
                self._replace_turn(params.turn)
                event = TurnStarted(params.turn)
            case TurnCompletedParams():
                self._replace_turn(params.turn)
                event = TurnCompleted(params.turn)
            case StatsUpdatedParams():
                self.state.session.token_usage = params.stats.token_usage
                event = StatsUpdated(params)
            case SessionCompactedParams() | SessionContextClearedParams():
                raise AssertionError("Session handoffs are reduced before events")
        return event

    def _next_event_id(self, event_id: object, session_id: object) -> int | None:
        if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id <= 0:
            raise EventSequenceError("App-server event IDs must be positive integers")
        if session_id != self.state.session.id:
            raise EventSequenceError(
                f"App-server event belongs to session {session_id!r}, "
                f"expected {self.state.session.id!r}"
            )
        if event_id <= self._last_event_id:
            return None
        expected = self._last_event_id + 1
        if event_id != expected:
            raise EventSequenceError(
                f"App-server event gap: expected {expected}, received {event_id}"
            )
        return event_id

    def _validate_snapshot(self, params: SessionSnapshotParams) -> None:
        if params.state.session.id != params.session_id:
            raise EventSequenceError(
                "App-server snapshot session does not match its notification"
            )
        if params.state.event_id != params.event_id:
            raise EventSequenceError(
                "App-server snapshot watermark does not match its notification"
            )

    def _validate_handoff(
        self, params: SessionCompactedParams | SessionContextClearedParams
    ) -> None:
        if params.old_session_id != self.state.session.id:
            raise EventSequenceError(
                "App-server handoff does not replace the active session"
            )
        if params.session_id == params.old_session_id:
            raise EventSequenceError("App-server handoff did not change session")
        if params.state.session.id != params.session_id:
            raise EventSequenceError(
                "App-server handoff state does not match its notification"
            )
        if params.state.event_id != params.event_id:
            raise EventSequenceError(
                "App-server handoff watermark does not match its notification"
            )
        if params.event_id <= 0:
            raise EventSequenceError("App-server handoff watermark must be positive")

    def _replace_state(self, state: PublicSessionState) -> None:
        self.state = state
        self._entries = {entry.id: entry for entry in state.history or []}
        self._last_event_id = state.event_id

    def _apply_snapshot(self, state: PublicSessionState) -> None:
        previous = self.state
        if state.session.id != previous.session.id:
            self._replace_state(state)
            return

        # History and turns are append-only within one session. Live snapshots
        # carry only their latest page, so retain a contiguous page already loaded
        # before that suffix while taking updated entries from the snapshot.
        history, preserved_history_prefix = _merge_snapshot_suffix(
            previous.history, state.history, item_id=lambda entry: entry.id
        )
        turns, _ = _merge_snapshot_suffix(
            previous.turns, state.turns, item_id=lambda turn: turn.id
        )
        self._replace_state(
            state.model_copy(
                update={
                    "history": history,
                    "history_before_cursor": (
                        previous.history_before_cursor
                        if preserved_history_prefix
                        else state.history_before_cursor
                    ),
                    "turns": turns,
                }
            )
        )

    def _add_entry(self, entry: PublicHistoryEntry) -> None:
        if entry.id in self._entries:
            raise ValueError(f"Duplicate public history entry: {entry.id}")
        self._entries[entry.id] = entry
        if self.state.history is None:
            self.state.history = []
        self.state.history.append(entry)
        if (
            isinstance(entry, PublicCallbackEntry)
            and entry.state.status == "open"
            and all(
                callback.callback_id != entry.callback_id
                for callback in self.state.active_callbacks
            )
        ):
            self.state.active_callbacks.append(entry)

    def _update_entry(
        self, params: HistoryEntryUpdatedParams
    ) -> HistoryEntryUpdated | None:
        previous = self._entries.get(params.entry_id)
        if previous is None:
            return None
        if previous.generation_status is PublicEntryGenerationStatus.COMPLETED:
            raise ValueError(
                f"Completed public history entry is frozen: {params.entry_id}"
            )
        raw = previous.model_dump(mode="json", by_alias=True)
        entry = validate_history_entry(apply_json_patch(raw, params.patch))
        self._replace_entry(entry)
        if isinstance(entry, PublicCallbackEntry):
            self.state.active_callbacks = [
                callback
                for callback in self.state.active_callbacks
                if callback.callback_id != entry.callback_id
            ]
            if entry.state.status == "open":
                self.state.active_callbacks.append(entry)
        return HistoryEntryUpdated(previous=previous, entry=entry, patch=params.patch)

    def _replace_entry(self, entry: PublicHistoryEntry) -> None:
        self._entries[entry.id] = entry
        history = self.state.history
        if history is None:
            return
        for index, existing in enumerate(history):
            if existing.id == entry.id:
                history[index] = entry
                return

    def _replace_turn(self, turn: PublicTurn) -> None:
        turns = self.state.turns
        if turns is None:
            self.state.turns = [turn]
            return
        for index, existing in enumerate(turns):
            if existing.id == turn.id:
                turns[index] = turn
                return
        turns.append(turn)

    def _update_session(self, params: SessionUpdatedParams) -> SessionUpdated:
        previous = self.state.session
        raw = previous.model_dump(mode="json", by_alias=True)
        patched = apply_json_patch(raw, params.patch)
        session = PublicSession.model_validate(patched)
        self.state.session = session
        return SessionUpdated(previous=previous, session=session, patch=params.patch)


def _parse_event_params(notification: Notification) -> _KnownEventParams:
    match notification.method:
        case "session/snapshot":
            params = validate_wire(SessionSnapshotParams, notification.params)
        case "session/compacted":
            params = validate_wire(SessionCompactedParams, notification.params)
        case "session/contextCleared":
            params = validate_wire(SessionContextClearedParams, notification.params)
        case "session/updated":
            params = validate_wire(SessionUpdatedParams, notification.params)
        case "history/entryAdded":
            params = validate_wire(HistoryEntryAddedParams, notification.params)
        case "history/entryUpdated":
            params = validate_wire(HistoryEntryUpdatedParams, notification.params)
        case "turn/started":
            params = validate_wire(TurnStartedParams, notification.params)
        case "turn/completed":
            params = validate_wire(TurnCompletedParams, notification.params)
        case "session/statsUpdated":
            params = validate_wire(StatsUpdatedParams, notification.params)
        case _:
            raise UnknownNotificationError(
                f"Unknown app-server notification: {notification.method}"
            )
    return params
