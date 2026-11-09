"""One Session's state, kept from Session API reads and stream batches.

``ApiSessionState`` starts from a read (``session/read`` plus
``vibe/session/read``, taken at the same moment) and applies the stream's
batches one at a time. It holds what the adapter needs to show the Session:

- the API's ``Session``, latest ``TurnState`` and open callbacks, from which
  the Vibe status and latest Turn are derived;
- the latest value of each state-carrying ``vibe.*`` item, for what the API
  cannot carry (usage, title source, queue, retry, processes, Turn details);
- the history, through ``ApiHistoryProjection``, one API entry at a time.

``runtime_state`` gives the Runtime's public state without its history, and
``history`` gives Vibe's public entries, so the adapter builds the client's
state the same way on both paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    AnsweredCallbackOutcome,
    ApprovalCallback,
    CallbackOpenedEvent,
    CallbackOutcome,
    CallbackResolvedEvent,
    CancelledCallbackOutcome,
    CancelledTermination,
    CheckpointHistoryEntry,
    CompletedTermination,
    Event,
    FailedCallbackOutcome,
    FailedTermination,
    HistoryEntry,
    HistoryEntryAddedEvent,
    HistoryEntryCompletedEvent,
    HistoryEntryPatchedEvent,
    Session,
    SessionCallback,
    SessionUpdatedEvent,
    TextContentBlock,
    ToolCallHistoryEntry,
    TurnStartedEvent,
    TurnState,
    TurnTerminatedEvent,
    TurnUpdatedEvent,
    UnknownCallbackOutcome,
    UnknownTermination,
    UserInputCallback,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionReadResponse,
)
from mistralai_vibe_local_harness.session_protocol import (
    TURN_QUEUE_MAX_ITEMS,
    JsonObject,
    PublicSessionState as HarnessPublicSessionState,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    BACKGROUND_PROCESSES_UPDATED,
    CALLBACK_DETAILS,
    RETRYING_UPDATED,
    TITLE_SOURCE_UPDATED,
    TURN_DETAILS,
    TURN_QUEUE_UPDATED,
    USAGE_UPDATED,
    StreamBatch,
    VibeExtensionEvent,
    VibeSessionReadResponse,
)
from vibe.app_server._session_api_projection import (
    VIBE_PREFIX,
    ApiHistoryProjection,
    EntryChange,
)
from vibe.app_server.models import ApprovalDecisionType, PublicHistoryEntry

_STATE_ITEMS = frozenset({
    USAGE_UPDATED,
    TITLE_SOURCE_UPDATED,
    TURN_QUEUE_UPDATED,
    RETRYING_UPDATED,
    BACKGROUND_PROCESSES_UPDATED,
})
"""The ``vibe.*`` items that replace part of the Session's state."""

_BLOCKING_CALLBACKS = frozenset({"approval", "user_input"})
"""Callback kinds that block a running Turn until the client answers."""


@dataclass(frozen=True, slots=True)
class AppliedBatch:
    """What one stream batch changed, for the adapter to publish."""

    history_changes: list[EntryChange] = field(default_factory=list)
    signals: list[VibeExtensionEvent] = field(default_factory=list)
    """``vibe.*`` items that carry no state: notices, authorizations, Smart
    Approve classifications, subagents."""
    opened_callbacks: list[JsonObject] = field(default_factory=list)
    """Callbacks the batch opened, in the Runtime's callback entry format."""
    resolved_callbacks: list[JsonObject] = field(default_factory=list)
    """Callbacks the batch resolved, with their final state."""


@dataclass(slots=True)
class _OpenCallback:
    callback: SessionCallback
    opened_at: int
    details: dict[str, JsonValue] = field(default_factory=dict)


class ApiSessionState:
    """One Session's state, from a read and then stream batches."""

    def __init__(self, session_id: str, display_roots: Sequence[Path]) -> None:
        self.session_id = session_id
        self.history_projection = ApiHistoryProjection(session_id, display_roots)
        self._session: Session | None = None
        self._turn: TurnState | None = None
        self._read_active_turn_id: str | None = None
        self._callbacks: dict[str, _OpenCallback] = {}
        self._turn_details: dict[str, dict[str, JsonValue]] = {}
        self._tool_call_entries: dict[str, str] = {}
        self._values: dict[str, dict[str, JsonValue]] = {}

    def reset(
        self, read: SessionReadResponse, extensions: VibeSessionReadResponse
    ) -> None:
        """Replace everything from a read; history comes from ``read``'s page.

        The read's ``turns`` page, newest first, gives the latest Turn. Without
        one (a read that asked for no Turns), only the active Turn's ID is known.
        """
        state = read.state
        entries = list(state.history.items) if state.history is not None else []
        self._session = state.session
        self._read_active_turn_id = state.active_turn_id
        turn = state.turns.items[0] if state.turns and state.turns.items else None
        self._turn = None if _rewound_past(turn, entries) else turn
        values = cast(dict[str, dict[str, Any]], extensions.extensions)
        self._values = {key: values[key] for key in _STATE_ITEMS if key in values}
        self._turn_details = dict(values.get(TURN_DETAILS, {}))
        callback_details = values.get(CALLBACK_DETAILS, {})
        # A read has no callback times; the Turn's start is the closest one.
        turn = self._turn
        opened_at = (
            turn.started_at
            if turn is not None and turn.started_at is not None
            else state.session.updated_at
        )
        self._callbacks = {
            callback.id: _OpenCallback(
                callback, opened_at, callback_details.get(callback.id, {})
            )
            for callback in state.active_callbacks
        }
        self._tool_call_entries = {
            entry.tool_call_id: entry.id
            for entry in entries
            if isinstance(entry, ToolCallHistoryEntry)
        }
        self.history_projection.reset(entries)

    def apply(self, batch: StreamBatch) -> AppliedBatch:
        """Apply one stream batch: every item of one Runtime update."""
        applied = AppliedBatch()
        opened: list[str] = []
        for item in batch.events:
            if isinstance(item, VibeExtensionEvent):
                self._apply_extension(item, applied)
            else:
                self._apply_api_event(item, applied, opened)
        # Details follow their callback in the batch, so the entry is built last.
        for callback_id in opened:
            self._publish_opened(self._callbacks[callback_id], applied)
        return applied

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self.history_projection.history

    def runtime_state(self) -> HarnessPublicSessionState:
        """The Runtime's public state, without its history entries."""
        session = self._session
        if session is None:
            raise RuntimeError("The Session state was not read yet")
        usage = self._value(USAGE_UPDATED)
        raw: dict[str, Any] = {
            "session": {
                "id": session.id,
                "rootSessionId": session.root_session_id,
                "parentSessionId": session.parent_session_id,
                "title": session.title,
                "titleSource": self._value(TITLE_SOURCE_UPDATED).get(
                    "titleSource", "auto"
                ),
                "preview": session.preview,
                "status": self._status(session),
                "createdAt": session.created_at,
                "updatedAt": session.updated_at,
                "tokenUsage": usage.get("tokenUsage"),
                "contextUsage": usage.get("contextUsage"),
            },
            "history": {"range": "latest", "entries": []},
            "activeCallbacks": [
                callback
                for open_callback in self._callbacks.values()
                if (callback := self._raw_callback(open_callback)) is not None
            ],
            "latestTurn": self._latest_turn(session),
            "turnQueue": self._value(TURN_QUEUE_UPDATED).get("queue")
            or {"items": [], "paused": False, "maxItems": TURN_QUEUE_MAX_ITEMS},
            "backgroundProcesses": self._value(BACKGROUND_PROCESSES_UPDATED).get(
                "processes"
            )
            or [],
            "retrying": self._value(RETRYING_UPDATED).get("retrying"),
        }
        return HarnessPublicSessionState.model_validate(raw)

    def _status(self, session: Session) -> JsonObject:
        """Vibe's Session status, from the API's Session, Turn and callbacks."""
        if session.archived_at is not None:
            return {"type": "archived"}
        turn = self._turn
        if turn is None:
            turn_id = self._read_active_turn_id
        elif turn.status == "terminated":
            if isinstance(turn.termination, FailedTermination):
                return {"type": "failed", "message": turn.termination.error.message}
            return {"type": "idle"}
        else:
            turn_id = turn.id
        if turn_id is None:
            return {"type": "idle"}
        blocking = [
            open_callback.callback
            for open_callback in self._callbacks.values()
            if open_callback.callback.kind in _BLOCKING_CALLBACKS
            and open_callback.callback.turn_id in {turn_id, None}
        ]
        if not blocking:
            return {"type": "running", "activeTurnId": turn_id}
        return {
            "type": "blocked",
            "activeTurnId": turn_id,
            "callbackId": blocking[-1].id,
            "callbackKind": blocking[-1].kind,
        }

    def _latest_turn(self, session: Session) -> JsonObject | None:
        """Vibe's latest Turn, from the API's ``TurnState`` and its details."""
        turn = self._turn
        if turn is None:
            return None
        details = self._turn_details.get(turn.id, {})
        started_at = (
            turn.started_at if turn.started_at is not None else session.created_at
        )
        base: JsonObject = {
            "id": turn.id,
            "sessionId": self.session_id,
            "queueItemId": details.get("queueItemId"),
            "inputEntryId": details.get("inputEntryId"),
            "startedAt": started_at,
        }
        completed_at = (
            turn.completed_at if turn.completed_at is not None else started_at
        )
        match turn.termination:
            case None:
                return {**base, "status": "in_progress"}
            case CompletedTermination(stop_reason=stop_reason, output=output):
                completed: JsonObject = {
                    **base,
                    "status": "completed",
                    "completedAt": completed_at,
                    "stopReason": details.get("stopReason")
                    or ("limit" if stop_reason == "iteration_limit" else None),
                }
                if stop_reason == "skipped":
                    completed["skipReason"] = output if isinstance(output, str) else ""
                elif stop_reason == "rejected":
                    completed["rejectReason"] = (
                        output if isinstance(output, str) else ""
                    )
                return completed
            case FailedTermination(error=error):
                return {
                    **base,
                    "status": "failed",
                    "completedAt": completed_at,
                    "error": {
                        "message": error.message,
                        "code": error.code,
                        "details": error.details,
                    },
                }
            case CancelledTermination(reason=reason):
                return {
                    **base,
                    "status": "interrupted",
                    "completedAt": completed_at,
                    "reason": reason,
                }
            case UnknownTermination(original_outcome=outcome):
                # A newer Runtime's outcome: Vibe has no state for it, so the Turn shows as ended.
                return {
                    **base,
                    "status": "interrupted",
                    "completedAt": completed_at,
                    "reason": f"The turn ended with an outcome this version does not know: {outcome}",
                }

    def _publish_opened(
        self, open_callback: _OpenCallback, applied: AppliedBatch
    ) -> None:
        raw = self._raw_callback(open_callback)
        if raw is not None:
            applied.opened_callbacks.append(raw)

    def _raw_callback(self, open_callback: _OpenCallback) -> JsonObject | None:
        """The Runtime's callback entry for an open API callback.

        None for a callback Vibe cannot show: a kind other than approval and
        user input, or a user input without its structured question request.
        """
        callback = open_callback.callback
        details = open_callback.details
        base: JsonObject = {
            "type": "callback",
            "id": callback.id,
            "callbackId": callback.id,
            "sessionId": self.session_id,
            "turnId": callback.turn_id,
            "inputEntryId": self._turn_details.get(callback.turn_id or "", {}).get(
                "inputEntryId"
            ),
            "createdAt": open_callback.opened_at,
            "updatedAt": open_callback.opened_at,
            "generationStatus": "in_progress",
            "state": {"status": "open"},
        }
        match callback:
            case ApprovalCallback():
                name = callback.name or ""
                call_entry = self._tool_call_entries.get(callback.tool_call_id)
                related = call_entry.removesuffix(":call") if call_entry else None
                return {
                    **base,
                    "relatedEntryId": related,
                    "title": callback.title,
                    "detail": {
                        "kind": "approval",
                        "effect": {
                            "kind": "tool",
                            "toolName": name,
                            "input": callback.input,
                            "display": {
                                "summary": name,
                                "verb": "Run",
                                "message": name,
                                "settledVerb": "Ran",
                                "settledMessage": name,
                                "statusText": f"Waiting for approval to run {name}",
                            },
                        },
                        "requiredPermissions": details.get("requiredPermissions") or [],
                        "choices": [
                            decision.value for decision in ApprovalDecisionType
                        ],
                        "relatedEntryId": related,
                        "reason": callback.description or None,
                    },
                }
            case UserInputCallback():
                request = details.get("request")
                if request is None:
                    return None
                related = details.get("relatedEntryId")
                text = next(
                    (
                        block.text
                        for block in callback.message
                        if isinstance(block, TextContentBlock)
                    ),
                    "",
                )
                return {
                    **base,
                    "relatedEntryId": related,
                    "title": text,
                    "detail": {
                        "kind": "user_input",
                        "request": request,
                        "relatedEntryId": related,
                    },
                }
            case _:
                return None

    def _value(self, item_type: str) -> dict[str, JsonValue]:
        return self._values.get(item_type, {})

    def _apply_extension(self, item: VibeExtensionEvent, applied: AppliedBatch) -> None:
        if item.type in _STATE_ITEMS:
            self._values[item.type] = item.data
            return
        if item.type == TURN_DETAILS:
            self._turn_details[cast(str, item.data["turnId"])] = item.data
            return
        if item.type == CALLBACK_DETAILS:
            open_callback = self._callbacks.get(cast(str, item.data["callbackId"]))
            if open_callback is not None:
                open_callback.details = item.data
            return
        applied.signals.append(item)

    def _apply_api_event(
        self, item: Event, applied: AppliedBatch, opened: list[str]
    ) -> None:
        payload = item.payload
        match payload:
            case SessionUpdatedEvent(session=session):
                self._session = session
            case TurnStartedEvent(turn=turn) | TurnUpdatedEvent(turn=turn):
                if turn.started_at is None:
                    turn = turn.model_copy(update={"started_at": item.emitted_at})
                self._turn = turn
            case TurnTerminatedEvent(turn=turn):
                # A batch can start a new Turn before it ends an older one.
                if self._turn is None or self._turn.id == turn.id:
                    self._turn = turn
            case CallbackOpenedEvent(callback=callback):
                self._callbacks[callback.id] = _OpenCallback(callback, item.emitted_at)
                opened.append(callback.id)
            case CallbackResolvedEvent(callback_id=callback_id, outcome=outcome):
                open_callback = self._callbacks.pop(callback_id, None)
                if open_callback is None:
                    return
                if callback_id in opened:
                    opened.remove(callback_id)
                    self._publish_opened(open_callback, applied)
                raw = self._raw_callback(open_callback)
                if raw is not None:
                    applied.resolved_callbacks.append({
                        **raw,
                        "updatedAt": item.emitted_at,
                        "generationStatus": "completed",
                        "state": _resolved_state(outcome),
                    })
            case (
                HistoryEntryAddedEvent()
                | HistoryEntryPatchedEvent()
                | HistoryEntryCompletedEvent()
            ):
                if not isinstance(payload, HistoryEntryPatchedEvent) and isinstance(
                    payload.entry, ToolCallHistoryEntry
                ):
                    self._tool_call_entries[payload.entry.tool_call_id] = (
                        payload.entry.id
                    )
                applied.history_changes.extend(self.history_projection.apply(payload))
            case _:
                pass


def _rewound_past(turn: TurnState | None, entries: Sequence[HistoryEntry]) -> bool:
    """Whether an in-place rewind removed every Turn from ``turn`` on.

    A rewind keeps the Turns before its anchor, so a read's newest Turn can be
    one that ended before the rewound one. The API cannot say that the latest
    Turn was rewound; the rewind checkpoint after all of ``turn``'s entries
    does. A Turn with no entry is listed only while it is the Runtime's own
    latest Turn, which a rewind clears, so it is never rewound past.
    """
    if turn is None or turn.status != "terminated" or not turn.history_entry_ids:
        return False
    own = set(turn.history_entry_ids)
    last = max((entry.index for entry in entries if entry.id in own), default=-1)
    return any(
        isinstance(entry, CheckpointHistoryEntry)
        and entry.annotations.get(VIBE_PREFIX + "checkpointKind") == "rewind"
        and entry.index > last
        for entry in entries
    )


def _resolved_state(outcome: CallbackOutcome | None) -> JsonObject:
    """Vibe's callback state for an API outcome; Vibe has no failed state."""
    match outcome:
        case AnsweredCallbackOutcome(output=output):
            return {"status": "answered", "output": output}
        case CancelledCallbackOutcome(reason=reason):
            return {"status": "cancelled", "reason": reason or ""}
        case FailedCallbackOutcome(error=error):
            return {"status": "cancelled", "reason": error.message}
        case UnknownCallbackOutcome() | None:
            return {"status": "cancelled", "reason": ""}


__all__ = ["ApiSessionState", "AppliedBatch"]
