from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server._model import validate_wire
from vibe.app_server._patch import apply_json_patch, make_json_patch
from vibe.app_server.models import (
    JsonPatchOperation,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicHistoryPage,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    Notification,
    ServerErrorParams,
    ServerWarningParams,
    SessionCompactedParams,
    SessionContextClearedParams,
    SessionSnapshotParams,
    SessionUpdatedParams,
    StatsUpdatedParams,
    TurnCompletedParams,
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
class ServerWarning:
    params: ServerWarningParams


@dataclass(frozen=True, slots=True)
class ServerError:
    params: ServerErrorParams


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
    | ServerWarning
    | ServerError
)

_STREAMING_TEXT_PATHS = {"/content/0/text", "/text", "/state/outputText"}


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

    previous_entries = {entry.id: entry for entry in previous.history.entries}
    for entry in current.history.entries:
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

    previous_turn = previous.latest_turn
    current_turn = current.latest_turn
    if current_turn is None or current_turn == previous_turn:
        return events
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
) -> ServerWarning | ServerError | None:
    match notification.method:
        case "warning":
            return ServerWarning(
                validate_wire(ServerWarningParams, notification.params)
            )
        case "error":
            return ServerError(validate_wire(ServerErrorParams, notification.params))
        case _:
            return None


class ClientProjection:
    def __init__(self, state: PublicSessionState) -> None:
        self.state = state
        self._entries = {entry.id: entry for entry in state.history.entries}
        self._last_event_id = state.event_id

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self.state.history.entries

    def replace_state(self, state: PublicSessionState) -> None:
        self._replace_state(state)

    def prepend_history_page(self, page: PublicHistoryPage) -> None:
        page_entries: list[PublicHistoryEntry] = []
        page_ids: set[str] = set()
        for entry in page.entries:
            if entry.id in page_ids:
                raise ValueError(f"Duplicate paged history entry: {entry.id}")
            page_ids.add(entry.id)
            if existing := self._entries.get(entry.id):
                if existing != entry:
                    raise ValueError(f"Conflicting paged history entry: {entry.id}")
                continue
            self._entries[entry.id] = entry
            page_entries.append(entry)
        self.state.history.entries[:0] = page_entries
        self.state.history.cursor.before = page.cursor.before

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
        self.state.latest_turn = turn

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
                self._replace_state(params.state)
                event = SessionSnapshot(params.state)
            case SessionUpdatedParams():
                event = self._update_session(params)
            case HistoryEntryAddedParams():
                self._add_entry(params.entry)
                event = HistoryEntryAdded(params.entry)
            case HistoryEntryUpdatedParams():
                event = self._update_entry(params)
            case TurnStartedParams():
                self.state.latest_turn = params.turn
                event = TurnStarted(params.turn)
            case TurnCompletedParams():
                self.state.latest_turn = params.turn
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
        self._entries = {entry.id: entry for entry in state.history.entries}
        self._last_event_id = state.event_id

    def _add_entry(self, entry: PublicHistoryEntry) -> None:
        if entry.id in self._entries:
            raise ValueError(f"Duplicate public history entry: {entry.id}")
        self._entries[entry.id] = entry
        self.state.history.entries.append(entry)
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
        for index, existing in enumerate(self.state.history.entries):
            if existing.id == entry.id:
                self.state.history.entries[index] = entry
                return

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
