# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Session data that Session API events cannot carry, as ``vibe.*`` stream items.

The Session API's ``Event`` accepts only the payload types the API defines. This
Runtime has more to say: token usage, the title source, the Turn queue,
provider retries, background processes, notices with a level, subagent
registrations, and the few Turn and callback fields the API lacks.
``VibeExtensionProducer`` turns the Runtime's updates into ``vibe.*`` items.
The event log stores them next to the API events, with the same ID sequence,
so one ID orders both.

Two kinds of item exist:

- a state-carrying item holds the full current value, not a diff, and is
  produced only when that value changes;
- a detail item (``vibe.turn_details``, ``vibe.callback_details``) follows the
  API event it adds to and holds only the fields that event cannot carry.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, JsonValue

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CallbackOpenedEvent,
    Event,
    SessionEvent,
    SessionModel,
    TurnStartedEvent,
    TurnTerminatedEvent,
    TurnUpdatedEvent,
)
from mistralai_vibe_local_harness.session_protocol import (
    CompletedPublicTurn,
    JsonObject,
    PublicSessionState,
    TurnQueue,
)

USAGE_UPDATED = "vibe.usage_updated"
TITLE_SOURCE_UPDATED = "vibe.title_source_updated"
TURN_QUEUE_UPDATED = "vibe.turn_queue_updated"
RETRYING_UPDATED = "vibe.retrying_updated"
BACKGROUND_PROCESSES_UPDATED = "vibe.background_processes_updated"
TURN_DETAILS = "vibe.turn_details"
"""Turn fields the API's ``TurnState`` lacks: input entry, queue item, ``length`` stop."""
CALLBACK_DETAILS = "vibe.callback_details"
"""Callback fields the API's callbacks lack: required permissions, question request."""
NOTICE = "vibe.notice"
AUTHORIZATION_REQUIRED = "vibe.authorization_required"
TOOL_CLASSIFICATION = "vibe.tool_classification"
SUBAGENT_REGISTERED = "vibe.subagent_registered"

VIBE_SESSION_READ = "vibe/session/read"

_PASSED_THROUGH = {
    "mcp_authorization_required": AUTHORIZATION_REQUIRED,
    "connector_authorization_required": AUTHORIZATION_REQUIRED,
    "tool_classification": TOOL_CLASSIFICATION,
}
"""Runtime events carried whole, their ``type`` kept inside ``data``."""


@dataclass(frozen=True, slots=True)
class VibeExtensionEvent:
    """A ``vibe.*`` event: data the Session API cannot carry."""

    event_id: str
    emitted_at: int
    session_id: str
    type: str
    data: dict[str, JsonValue]


type StreamEvent = Event | VibeExtensionEvent


@dataclass(frozen=True, slots=True)
class StreamBatch:
    """The stream items of one Runtime update, in order.

    A detail item comes right after the API event it adds to; the
    state-carrying ``vibe.*`` items come last.
    """

    events: tuple[StreamEvent, ...]


@dataclass(frozen=True, slots=True)
class VibeExtension:
    """A ``vibe.*`` item before the event log gives it an ID."""

    type: str
    data: dict[str, JsonValue]


type StreamItem = SessionEvent | VibeExtension
"""One stream item before the event log gives it an ID."""


class VibeSessionReadParams(SessionModel):
    session_id: str


class VibeSessionReadResponse(SessionModel):
    """The current value of every state-carrying ``vibe.*`` item.

    ``last_event_id`` equals the ``session/read`` bookmark taken at the same
    moment. ``extensions`` maps each item type to its ``data``.
    """

    session_id: str
    last_event_id: str
    extensions: dict[str, JsonValue]


class VibeExtensionProducer:
    """Turn the Runtime's updates into ``vibe.*`` items, each only on change.

    ``state_updated`` and ``callback_requested`` take the API events of the
    same update and return them with the ``vibe.*`` items placed in order:
    each detail item right after the API event it adds to, then the
    state-carrying items.
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, JsonValue]] = {}
        self._callbacks: dict[str, JsonObject] = {}
        self._turn_details: dict[str, JsonValue] | None = None

    def seed(self, state: PublicSessionState) -> None:
        """Remember ``state`` as already published, without producing items."""
        self._values = _state_values(state)
        self._callbacks = _callbacks_by_id(state.active_callbacks)
        self._turn_details = _turn_details(state)

    def values(self) -> dict[str, JsonValue]:
        """The ``vibe/session/read`` extensions.

        Each state-carrying item's current value by type, plus the current
        detail values keyed by Turn ID and by open callback ID.
        """
        values: dict[str, JsonValue] = dict(self._values)
        details = self._turn_details
        values[TURN_DETAILS] = (
            {cast(str, details["turnId"]): details} if details is not None else {}
        )
        values[CALLBACK_DETAILS] = {
            callback_id: details
            for callback_id, callback in self._callbacks.items()
            if (details := _callback_details(callback)) is not None
        }
        return values

    def state_updated(
        self, state: PublicSessionState, api_events: Sequence[SessionEvent]
    ) -> list[StreamItem]:
        self._callbacks = _callbacks_by_id(state.active_callbacks)
        turn_details = _turn_details(state)
        if turn_details is None or turn_details == self._turn_details:
            turn_details = None
        else:
            self._turn_details = turn_details
        items: list[StreamItem] = []
        for event in api_events:
            items.append(event)
            if (
                turn_details is not None
                and isinstance(
                    event, TurnStartedEvent | TurnUpdatedEvent | TurnTerminatedEvent
                )
                and event.turn.id == turn_details["turnId"]
            ):
                items.append(VibeExtension(TURN_DETAILS, turn_details))
                turn_details = None
            items.extend(self._callback_details_after(event))
        if turn_details is not None:
            items.append(VibeExtension(TURN_DETAILS, turn_details))
        items.extend(
            VibeExtension(event_type, data)
            for event_type, data in _state_values(state).items()
            if self._replace(event_type, data)
        )
        return items

    def callback_requested(
        self, callback: JsonObject, api_events: Sequence[SessionEvent]
    ) -> list[StreamItem]:
        self._callbacks[_callback_id(callback)] = callback
        items: list[StreamItem] = []
        for event in api_events:
            items.append(event)
            items.extend(self._callback_details_after(event))
        return items

    def callback_resolved(self, callback: JsonObject) -> None:
        self._callbacks.pop(_callback_id(callback), None)

    def runtime_event(self, raw: JsonObject) -> list[StreamItem]:
        """The items for a Runtime event that carries no public state.

        The Runtime's own ``eventId`` is dropped: the log gives each item its ID.
        """
        runtime_type = raw.get("type")
        if runtime_type is None:
            return self._turn_queue_updated(raw.get("payload"))
        if runtime_type == "child_session_registered":
            session_id = raw.get("sessionId")
            if not isinstance(session_id, str):
                return []
            return [VibeExtension(SUBAGENT_REGISTERED, {"sessionId": session_id})]
        if runtime_type == "notice":
            return [VibeExtension(NOTICE, _without(raw, "type", "eventId"))]
        extension_type = (
            _PASSED_THROUGH.get(runtime_type) if isinstance(runtime_type, str) else None
        )
        if extension_type is None:
            return []
        return [VibeExtension(extension_type, _without(raw, "eventId"))]

    def _callback_details_after(self, event: SessionEvent) -> list[VibeExtension]:
        if not isinstance(event, CallbackOpenedEvent):
            return []
        details = _callback_details(self._callbacks[event.callback.id])
        return [VibeExtension(CALLBACK_DETAILS, details)] if details else []

    def _turn_queue_updated(self, payload: JsonValue) -> list[StreamItem]:
        """A ``turn_queue_updated`` event, which nests its type in ``payload``."""
        if not isinstance(payload, dict) or payload.get("type") != "turn_queue_updated":
            return []
        queue = TurnQueue.model_validate(payload.get("queue"))
        data: dict[str, JsonValue] = {"queue": _json(queue)}
        if not self._replace(TURN_QUEUE_UPDATED, data):
            return []
        return [VibeExtension(TURN_QUEUE_UPDATED, data)]

    def _replace(self, event_type: str, data: dict[str, JsonValue]) -> bool:
        """Store ``data`` as the current value; return whether it changed."""
        if self._values.get(event_type) == data:
            return False
        self._values[event_type] = data
        return True


def _state_values(state: PublicSessionState) -> dict[str, dict[str, JsonValue]]:
    """Every state-carrying item's ``data``, in the order items are produced."""
    session = state.session
    return {
        USAGE_UPDATED: {
            "tokenUsage": _json(session.token_usage),
            "contextUsage": _json(session.context_usage),
        },
        TITLE_SOURCE_UPDATED: {"titleSource": session.title_source},
        TURN_QUEUE_UPDATED: {"queue": _json(state.turn_queue)},
        RETRYING_UPDATED: {"retrying": _json(state.retrying)},
        BACKGROUND_PROCESSES_UPDATED: {
            "processes": [_json(process) for process in state.background_processes]
        },
    }


def _turn_details(state: PublicSessionState) -> dict[str, JsonValue] | None:
    """The latest Turn's fields the API cannot carry, or None if all are unset.

    ``stopReason`` is sent only for ``length``: the API's own
    ``CompletedTermination.stop_reason`` already says ``iteration_limit``. It also says
    ``output_limit`` now, but a Vibe app server older than that reads ``length`` only here.
    TODO: drop ``stopReason`` once every Vibe app server reads ``output_limit``.
    """
    turn = state.latest_turn
    if turn is None:
        return None
    stop_reason = (
        "length"
        if isinstance(turn, CompletedPublicTurn) and turn.stop_reason == "length"
        else None
    )
    if turn.input_entry_id is None and turn.queue_item_id is None and not stop_reason:
        return None
    return {
        "turnId": turn.id,
        "inputEntryId": turn.input_entry_id,
        "queueItemId": turn.queue_item_id,
        "stopReason": stop_reason,
    }


def _callback_details(callback: JsonObject) -> dict[str, JsonValue] | None:
    """A callback's fields the API's callback cannot carry, or None if none."""
    detail = cast(dict[str, JsonValue], callback["detail"])
    details: dict[str, JsonValue] = {"callbackId": _callback_id(callback)}
    if detail["kind"] == "user_input":
        details["request"] = detail.get("request")
        details["relatedEntryId"] = detail.get("relatedEntryId")
    elif detail.get("requiredPermissions"):
        details["requiredPermissions"] = detail["requiredPermissions"]
    return details if len(details) > 1 else None


def _json(model: BaseModel | None) -> JsonValue:
    if model is None:
        return None
    return cast(JsonValue, model.model_dump(mode="json", by_alias=True))


def _callback_id(callback: Mapping[str, JsonValue]) -> str:
    return cast(str, callback["callbackId"])


def _callbacks_by_id(callbacks: Sequence[JsonObject]) -> dict[str, JsonObject]:
    return {_callback_id(callback): callback for callback in callbacks}


def _without(raw: JsonObject, *keys: str) -> dict[str, JsonValue]:
    return {key: value for key, value in raw.items() if key not in keys}


__all__ = [
    "AUTHORIZATION_REQUIRED",
    "BACKGROUND_PROCESSES_UPDATED",
    "CALLBACK_DETAILS",
    "NOTICE",
    "RETRYING_UPDATED",
    "SUBAGENT_REGISTERED",
    "TITLE_SOURCE_UPDATED",
    "TOOL_CLASSIFICATION",
    "TURN_DETAILS",
    "TURN_QUEUE_UPDATED",
    "USAGE_UPDATED",
    "VIBE_SESSION_READ",
    "StreamBatch",
    "StreamEvent",
    "StreamItem",
    "VibeExtension",
    "VibeExtensionEvent",
    "VibeExtensionProducer",
    "VibeSessionReadParams",
    "VibeSessionReadResponse",
]
