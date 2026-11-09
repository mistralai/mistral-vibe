# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""A Session API transport over one open Runtime session.

``LocalSessionApiTransport`` implements the Session API's ``SessionTransport``
contract on top of ``UnifiedHarnessSessionBackend``. It reads the Runtime's
public state through ``SessionApiView``, turns the Runtime's state updates into
Session API events, and sends commands to the Runtime session.

The transport serves the procedures that act on one open session. Creating,
forking, clearing and cancelling sessions belong to the Host and are refused
with ``UnsupportedSessionProcedureError``.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import secrets
import time
from typing import Any, Protocol, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import RustHarnessConfig
from mistralai_vibe_local_harness.session_api.app_server.session import (
    ChildSessionProcedureRefusedError,
    SessionCallbackSettledError,
    SessionIdempotencyConflictError,
    SessionTurnConflictError,
    UnsupportedSessionProcedureError,
)
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    AgentConfig,
    ClientTransportStreamItem,
    ContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceLinkContentBlock,
    SessionModel,
    TextContentBlock,
    TransportStreamEnded,
    TransportStreamStarted,
    TurnState,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    CallbackResultParams,
    CallbackResultResponse,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigWriteParams,
    ConfigWriteResponse,
    EventsReadParams,
    MessageSendParams,
    MessageSendResponse,
    PluginComponent,
    PluginInfo,
    PluginInfoParams,
    PluginInfoResponse,
    SessionCompactParams,
    SessionCompactResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SubagentSession,
    SubagentsListParams,
    SubagentsListResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)
from mistralai_vibe_local_harness.session_protocol import (
    JsonObject,
    PublicSessionState,
    SessionReadParams as RuntimeReadParams,
)
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessCallbackClosedError,
    HarnessCallbackConflictError,
    HarnessCallbackNotFoundError,
    HarnessContextCompactionError,
    HarnessStaleTurnError,
)
from mistralai_vibe_local_harness.vibe._fact_recorder import FactRecorder
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalRuntimeAdapterConfig,
    SessionConfig,
)
from mistralai_vibe_local_harness.vibe._session_api_config import (
    agent_config_from_runtime,
    session_config_from_agent_config,
)
from mistralai_vibe_local_harness.vibe._session_api_events import (
    SessionApiEventLog,
    SessionApiEventProducer,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    VIBE_SESSION_READ,
    StreamBatch,
    VibeExtensionProducer,
    VibeSessionReadParams,
    VibeSessionReadResponse,
)
from mistralai_vibe_local_harness.vibe._session_api_state import (
    SessionApiView,
    page,
    session_from_public,
)
from mistralai_vibe_local_harness.vibe._subagents._models import ChildSessionRecord

logger = logging.getLogger(__name__)

_BACKEND = "mistralai-vibe-local-harness"
_CONFIG_READ_PROCEDURE = "app_server/session/config/read"
_CONFIG_WRITE_PROCEDURE = "app_server/session/config/write"
_EVENTS_READ_PROCEDURE = "app_server/session/events/read"
_READ_PROCEDURE = "app_server/session/read"
_FULL_HISTORY = 1_000_000
"""Read the whole public history: Session API positions cover every entry."""
_PUBLICATION_TIMEOUT_SECONDS = 5.0
"""How long a reply waits for its own events to be published.

The wait ends as soon as the event pump catches up with the Runtime. Past this
bound the reply carries the bookmark published so far.
"""

_HOST_PROCEDURES = {
    "app_server/session/start": "the Host creates sessions; open one, then attach",
    "app_server/session/fork": "the Host forks sessions",
    "app_server/session/history/clear": "the Host replaces a cleared session",
    "app_server/session/cancel": "this Runtime has no permanent cancel state",
    "app_server/session/plugin/reload": "plugin reload belongs to the Host",
}


class RuntimeSession(Protocol):
    """The part of ``UnifiedHarnessSessionBackend`` this transport uses."""

    @property
    def session_id(self) -> str: ...

    @property
    def active_turn_id(self) -> str | None: ...

    async def read(self, params: RuntimeReadParams) -> Any: ...

    async def subscribe(self, params: RuntimeReadParams) -> Any: ...

    async def start_turn(self, params: object) -> object: ...

    async def wait_for_turn_admission(self, turn_id: str) -> None: ...

    async def steer_turn(self, params: object) -> object: ...

    async def interrupt_turn(self, params: object) -> object: ...

    async def respond_to_callback(self, params: object) -> object: ...

    async def compact(self, params: object) -> object: ...

    async def read_plugin_info(self) -> Any: ...

    @property
    def facts(self) -> FactRecorder: ...

    @property
    def core_config(self) -> RustHarnessConfig | None: ...

    @property
    def adapter_config(self) -> LocalRuntimeAdapterConfig | None: ...

    async def apply_config(self, config: SessionConfig) -> None: ...

    async def save_session_api_facts(self) -> None: ...


class ChildSessions(Protocol):
    """The Host operations that reach a root Session's subagent Sessions.

    ``UnifiedHarnessSessionBackendHost`` provides them. ``live_child`` returns
    the child's open Runtime Session, or None when the child is not loaded.
    """

    def child_session_records(
        self, root_session_id: str
    ) -> tuple[ChildSessionRecord, ...]: ...

    def references_child(self, root_session_id: str, child_session_id: str) -> bool: ...

    def live_child(
        self, root_session_id: str, child_session_id: str
    ) -> RuntimeSession | None: ...


_CHILD_PROCEDURES = frozenset({
    "app_server/session/read",
    VIBE_SESSION_READ,
    "app_server/session/history/list",
    "app_server/session/turns/list",
    "app_server/session/turn/interrupt",
    "app_server/session/callback/result",
    "app_server/session/config/read",
    "app_server/session/plugin/info",
})
"""What a client may ask of a subagent's Session: read it, interrupt its Turn
and answer its callbacks. Starting, steering or messaging it belongs to its
parent agent (``ChildSessionProcedureRefusedError``)."""


_TURN_START_RECEIPT_PREFIX = "turn/start:"
"""Turn start receipts share the receipt fact with ``message/send``."""


def _turn_start_key(idempotency_key: str | None) -> str | None:
    """The receipt key of a Turn start, or None when the start has no retry key."""
    if idempotency_key is None:
        return None
    return _TURN_START_RECEIPT_PREFIX + idempotency_key


def _minted_entry_id(session_id: str, idempotency_key: str | None) -> str:
    """An entry ID for a message the client did not name.

    With a retry key the ID follows from it, so a retry names the same entry
    and matches the receipts its first attempt saved.
    """
    if idempotency_key is None:
        return f"message-{secrets.token_hex(12)}"
    digest = hashlib.sha256(f"{session_id}\0{idempotency_key}".encode()).hexdigest()
    return f"message-{digest[:24]}"


def _lookup_receipt(
    view: SessionApiView, key: str | None, fingerprint: str
) -> JsonValue:
    """The accepted response saved under ``key``, or None when the key is new.

    Raises a conflict when the key was accepted for a different message.
    """
    if key is None:
        return None
    receipt = view.message_receipts.get(key)
    if receipt is None:
        return None
    if receipt.get("fingerprint") != fingerprint:
        raise SessionIdempotencyConflictError(
            idempotency_key=key.removeprefix(_TURN_START_RECEIPT_PREFIX)
        )
    return receipt.get("response")


def session_api_view_of(session: RuntimeSession) -> SessionApiView:
    """The Session API view ``session`` records its facts in.

    Raises ``RuntimeError`` when the Session API does not serve the session: its
    Runtime then records no facts the API could read.
    """
    facts = session.facts
    if not isinstance(facts, SessionApiView):
        raise RuntimeError(
            f"The Session API does not serve Session {session.session_id!r}"
        )
    return facts


class LocalSessionApiTransport:
    """Serve the Session API for one open Runtime session and its subagents.

    With ``children``, requests that name a subagent of this Session are served
    by a transport over that subagent's own Runtime Session, opened on first use.
    """

    def __init__(
        self,
        session: RuntimeSession,
        view: SessionApiView,
        *,
        clock: Callable[[], int] | None = None,
        children: ChildSessions | None = None,
        parent: "LocalSessionApiTransport | None" = None,
    ) -> None:
        self._session = session
        self._children = children
        self._parent = parent
        self._child_transports: dict[str, LocalSessionApiTransport] = {}
        # The Session owns the view from its creation and its Runtime saves it.
        self._view = view
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._producer = SessionApiEventProducer(session.session_id, self._view)
        self._extensions = VibeExtensionProducer()
        self._log = SessionApiEventLog(
            session.session_id,
            root_session_id=None if parent is None else parent.root_session_id,
            clock=self._clock,
        )
        self._state: PublicSessionState | None = None
        self._pump: asyncio.Task[None] | None = None
        self._observed_watermark = -1
        self._observed = asyncio.Condition()
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def root_session_id(self) -> str:
        """The root Session of this transport's tree: its own, unless a subagent."""
        return (
            self._session.session_id
            if self._parent is None
            else self._parent.root_session_id
        )

    @property
    def view(self) -> SessionApiView:
        return self._view

    async def open(self) -> None:
        """Subscribe to the Runtime session and start producing events."""
        if self._pump is not None:
            return
        subscription = await self._session.subscribe(
            RuntimeReadParams(session_id=self.session_id, history_limit=_FULL_HISTORY)
        )
        state = subscription.snapshot.state
        self._state = state
        self._producer.seed(state)
        self._extensions.seed(state)
        self._observed_watermark = subscription.snapshot.watermark
        self._pump = asyncio.create_task(
            self._consume(subscription.events), name="session-api-events"
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        children, self._child_transports = self._child_transports, {}
        for child in children.values():
            await child.close()
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
        await self._log.close()

    async def request[ResultT: SessionModel](
        self, method: str, params: SessionModel, result_type: type[ResultT]
    ) -> ResultT:
        if method == "app_server/subagents/list":
            response: SessionModel = await self._list_subagents(
                cast(SubagentsListParams, params)
            )
            return result_type.model_validate(response.model_dump(by_alias=True))
        handler = self._handlers().get(method)
        if handler is None:
            raise UnsupportedSessionProcedureError(
                method=method,
                backend=_BACKEND,
                reason=_HOST_PROCEDURES.get(method, "unknown Session procedure"),
            )
        session_id = getattr(params, "session_id", self.session_id)
        if session_id != self.session_id:
            child = await self._child_transport(session_id, method)
            return await child.request(method, params, result_type)
        if self._parent is not None and method not in _CHILD_PROCEDURES:
            raise ChildSessionProcedureRefusedError(method=method, backend=_BACKEND)
        response = await handler(params)
        return result_type.model_validate(response.model_dump(by_alias=True))

    async def read_events(
        self, params: EventsReadParams
    ) -> AsyncIterator[ClientTransportStreamItem]:
        if params.session_id != self.session_id:
            child = await self._child_transport(
                params.session_id, _EVENTS_READ_PROCEDURE
            )
            async for item in child.read_events(params):
                yield item
            return
        yield TransportStreamStarted()
        async for batch in self._log.follow(params.after_event_id, params.batch_size):
            yield cast(ClientTransportStreamItem, batch)
        yield TransportStreamEnded(
            reason="server_closed", latest_event_id=self._log.last_event_id
        )

    async def read_stream(self, params: EventsReadParams) -> AsyncIterator[StreamBatch]:
        """Like ``read_events``, but each batch holds API events and ``vibe.*`` events, in order.

        Each batch is exactly the items of one Runtime update, also when catching
        up after the bookmark, so ``params.batch_size`` does not apply.
        """
        if params.session_id != self.session_id:
            child = await self._child_transport(
                params.session_id, _EVENTS_READ_PROCEDURE
            )
            async for batch in child.read_stream(params):
                yield batch
            return
        async for items in self._log.follow_appends(params.after_event_id):
            yield StreamBatch(events=items)

    async def read_with_extensions(
        self, params: SessionReadParams
    ) -> tuple[SessionReadResponse, VibeSessionReadResponse]:
        """``session/read`` and ``vibe/session/read`` taken at the same moment."""
        if params.session_id != self.session_id:
            child = await self._child_transport(params.session_id, _READ_PROCEDURE)
            return await child.read_with_extensions(params)
        state, last_event_id = await self._settled_state()
        return (
            self._session_read(params, state, last_event_id),
            self._vibe_session_read(last_event_id),
        )

    def _handlers(self) -> dict[str, Callable[[Any], Awaitable[SessionModel]]]:
        return {
            "app_server/session/read": self._read,
            VIBE_SESSION_READ: self._read_vibe_session,
            "app_server/session/history/list": self._list_history,
            "app_server/session/turns/list": self._list_turns,
            "app_server/session/message/send": self._send_message,
            "app_server/session/turn/start": self._start_turn,
            "app_server/session/turn/steer": self._steer_turn,
            "app_server/session/turn/interrupt": self._interrupt_turn,
            "app_server/session/callback/result": self._callback_result,
            "app_server/session/compact": self._compact,
            "app_server/session/plugin/info": self._plugin_info,
            _CONFIG_READ_PROCEDURE: self._read_config,
            _CONFIG_WRITE_PROCEDURE: self._write_config,
        }

    async def _read_config(self, _params: ConfigReadParams) -> ConfigReadResponse:
        return ConfigReadResponse(
            config=self._current_agent_config(_CONFIG_READ_PROCEDURE)
        )

    async def _write_config(self, params: ConfigWriteParams) -> ConfigWriteResponse:
        core, local = self._runtime_settings(_CONFIG_WRITE_PROCEDURE)
        current = self._current_agent_config(_CONFIG_WRITE_PROCEDURE)
        session_config = session_config_from_agent_config(
            core, local, current, params.config
        )
        await self._session.apply_config(session_config)
        self.view.record_agent_config(
            cast(JsonObject, params.config.model_dump(mode="json", by_alias=True))
        )
        await self._session.save_session_api_facts()
        await self._wait_for_runtime_events()
        return ConfigWriteResponse(
            config=self._current_agent_config(_CONFIG_WRITE_PROCEDURE),
            last_event_id=self._log.last_event_id,
        )

    def _current_agent_config(self, method: str) -> AgentConfig:
        core, local = self._runtime_settings(method)
        return agent_config_from_runtime(core, local, saved=self.view.agent_config)

    def _runtime_settings(
        self, method: str
    ) -> tuple[RustHarnessConfig, LocalRuntimeAdapterConfig]:
        core = self._session.core_config
        local = self._session.adapter_config
        if core is None or local is None:
            raise UnsupportedSessionProcedureError(
                method=method,
                backend=_BACKEND,
                reason="this Session was opened without a Runtime configuration",
            )
        return core, local

    async def _read(self, params: SessionReadParams) -> SessionReadResponse:
        state, last_event_id = await self._settled_state()
        return self._session_read(params, state, last_event_id)

    async def _read_vibe_session(
        self, _params: VibeSessionReadParams
    ) -> VibeSessionReadResponse:
        _, last_event_id = await self._settled_state()
        return self._vibe_session_read(last_event_id)

    def _session_read(
        self, params: SessionReadParams, state: PublicSessionState, last_event_id: str
    ) -> SessionReadResponse:
        return SessionReadResponse(
            state=self.view.session_state(
                state, history=params.history, turns=params.turns
            ),
            last_event_id=last_event_id,
        )

    def _vibe_session_read(self, last_event_id: str) -> VibeSessionReadResponse:
        return VibeSessionReadResponse(
            session_id=self.session_id,
            last_event_id=last_event_id,
            extensions=self._extensions.values(),
        )

    async def _list_history(
        self, params: SessionHistoryListParams
    ) -> SessionHistoryListResponse:
        state, _ = await self._settled_state()
        entries = self.view.history_entries(state)
        if params.turn_id is not None:
            entries = [entry for entry in entries if entry.turn_id == params.turn_id]
        return SessionHistoryListResponse(page=page(entries, params.page))

    async def _list_turns(
        self, params: SessionTurnsListParams
    ) -> SessionTurnsListResponse:
        state, _ = await self._settled_state()
        return SessionTurnsListResponse(
            page=page(self.view.turn_states(state), params.page)
        )

    async def _send_message(self, params: MessageSendParams) -> MessageSendResponse:
        fingerprint = _fingerprint(params.message, params.message_entry_id)
        cached = _lookup_receipt(self.view, params.idempotency_key, fingerprint)
        if cached is not None:
            return MessageSendResponse.model_validate(cached)
        # The Runtime names a message entry after the client's ID when it has
        # one, so asking for an ID up front makes the entry findable afterwards.
        entry_id = params.message_entry_id or _minted_entry_id(
            self.session_id, params.idempotency_key
        )
        # A retry whose first attempt started a Turn but saved no send receipt
        # gets that start's reply; steering would reuse the start's key.
        start_saved = (
            _turn_start_key(params.idempotency_key) in self.view.message_receipts
        )
        turn_id = (
            None if start_saved else await self._steer_running_turn(params, entry_id)
        )
        if turn_id is None:
            started = await self._start_turn(
                TurnStartParams(
                    idempotency_key=params.idempotency_key,
                    session_id=params.session_id,
                    message_entry_id=entry_id,
                    message=params.message,
                )
            )
            turn_id = started.turn.id
        response = MessageSendResponse(message_entry_id=entry_id, turn_id=turn_id)
        await self._record_receipt(params.idempotency_key, fingerprint, response)
        return response

    async def _record_receipt(
        self, key: str | None, fingerprint: str, response: SessionModel
    ) -> None:
        """Save an accepted response before replying, so a retry after a crash finds it."""
        if key is None:
            return
        self.view.record_message_receipt(
            key,
            {
                "fingerprint": fingerprint,
                "response": cast(
                    JsonValue, response.model_dump(mode="json", by_alias=True)
                ),
            },
        )
        await self._session.save_session_api_facts()

    async def _steer_running_turn(
        self, params: MessageSendParams, entry_id: str
    ) -> str | None:
        """Steer the running Turn; return its ID, or None when no Turn runs.

        The check and the steer are two Runtime calls. If the Turn ends between
        them the steer is refused as stale, and the message starts a new Turn.
        """
        running = self._session.active_turn_id
        if running is None:
            return None
        try:
            await self._steer_turn(
                TurnSteerParams(
                    idempotency_key=params.idempotency_key,
                    session_id=params.session_id,
                    expected_turn_id=running,
                    message_entry_id=entry_id,
                    message=params.message,
                )
            )
        except SessionTurnConflictError:
            return None
        return running

    async def _start_turn(self, params: TurnStartParams) -> TurnStartResponse:
        # A retried start returns the first reply. The Runtime's own command
        # receipt would replay it under a new Turn ID that never runs.
        key = _turn_start_key(params.idempotency_key)
        fingerprint = _fingerprint(params.message, params.message_entry_id)
        cached = _lookup_receipt(self.view, key, fingerprint)
        if cached is not None:
            return TurnStartResponse.model_validate(cached)
        result = cast(
            Any,
            await self._session.start_turn(
                _RuntimeMessage.of(
                    params.session_id,
                    params.message,
                    idempotency_key=params.idempotency_key,
                    message_entry_id=params.message_entry_id,
                )
            ),
        )
        _run_after_response(result)
        turn = result.response.turn
        # A client reads the Turn as running once the start returns.
        await self._session.wait_for_turn_admission(turn.id)
        state, last_event_id = await self._settled_state()
        response = TurnStartResponse(
            turn=self._started_turn_state(state, turn.id, turn.started_at),
            last_event_id=last_event_id,
        )
        await self._record_receipt(key, fingerprint, response)
        return response

    def _started_turn_state(
        self, state: PublicSessionState, turn_id: str, started_at: int | None
    ) -> TurnState:
        """The state of a Turn this transport just started.

        Admission also ends when the Turn fails before Core accepts it; the
        published state then holds it as terminated. A Turn not listed yet
        reads as running.
        """
        for observed in self.view.turn_states(state):
            if observed.id == turn_id:
                return observed
        return TurnState(id=turn_id, status="running", started_at=started_at)

    async def _steer_turn(self, params: TurnSteerParams) -> TurnSteerResponse:
        try:
            result = await self._session.steer_turn(
                _RuntimeMessage.of(
                    params.session_id,
                    params.message,
                    idempotency_key=params.idempotency_key,
                    message_entry_id=params.message_entry_id,
                    expected_turn_id=params.expected_turn_id,
                )
            )
        except HarnessStaleTurnError as exc:
            raise SessionTurnConflictError(str(exc)) from exc
        _run_after_response(result)
        await self._wait_for_runtime_events()
        return TurnSteerResponse(last_event_id=self._log.last_event_id)

    async def _interrupt_turn(
        self, params: TurnInterruptParams
    ) -> TurnInterruptResponse:
        try:
            result = await self._session.interrupt_turn(
                _RuntimeInterrupt(
                    session_id=params.session_id,
                    expected_turn_id=params.expected_turn_id,
                    reason=params.reason,
                )
            )
        except HarnessStaleTurnError as exc:
            raise SessionTurnConflictError(str(exc)) from exc
        _run_after_response(result)
        await self._wait_for_runtime_events()
        return TurnInterruptResponse(last_event_id=self._log.last_event_id)

    async def _callback_result(
        self, params: CallbackResultParams
    ) -> CallbackResultResponse:
        try:
            result = await self._session.respond_to_callback({
                "session_id": params.session_id,
                "result": params.result,
            })
        except (
            HarnessCallbackClosedError,
            HarnessCallbackConflictError,
            HarnessCallbackNotFoundError,
        ) as exc:
            raise SessionCallbackSettledError(str(exc)) from exc
        _run_after_response(result)
        await self._wait_for_runtime_events()
        return CallbackResultResponse(last_event_id=self._log.last_event_id)

    async def _compact(self, params: SessionCompactParams) -> SessionCompactResponse:
        try:
            result = cast(
                Any,
                await self._session.compact(
                    _RuntimeCompact(
                        session_id=params.session_id,
                        extra_instructions=params.instructions or "",
                    )
                ),
            )
        except HarnessContextCompactionError as exc:
            # A failed compaction still holds the Session until its reply is
            # out; this API has no later moment, so release it now.
            _run_after_response(exc)
            raise
        _run_after_response(result)
        await self._wait_for_runtime_events()
        state, last_event_id = await self._settled_state()
        return SessionCompactResponse(
            state=self.view.session_state(state),
            last_event_id=last_event_id,
            summary=result.response["summary"],
        )

    async def _plugin_info(self, _params: PluginInfoParams) -> PluginInfoResponse:
        info = await self._session.read_plugin_info()
        return PluginInfoResponse(
            info=PluginInfo(
                workdir=info.workdir,
                components=[
                    PluginComponent.model_validate(
                        component.model_dump(mode="json", by_alias=True)
                    )
                    for component in info.components
                ],
                raw=info.raw,
            )
        )

    async def _consume(self, events: AsyncIterator[JsonObject]) -> None:
        try:
            async for raw in events:
                await self._publish(raw)
        except Exception:
            logger.exception("Session API event production stopped")
        finally:
            await self._log.close()
            # The stream ended or ``close`` stopped it, so no event will wake
            # the requests still waiting for one.
            async with self._observed:
                self._observed.notify_all()

    async def _publish(self, raw: JsonObject) -> None:
        match raw.get("type"):
            case "session_state_updated":
                state = published_runtime_state(cast(dict[str, Any], raw["state"]))
                self._state = state
                payloads = self._producer.state_updated(state)
                items = self._extensions.state_updated(state, payloads)
            case "callback_requested":
                callback = cast(JsonObject, raw.get("callback"))
                payloads = self._producer.callback_requested(callback)
                items = self._extensions.callback_requested(callback, payloads)
            case "callback_resolved":
                callback = cast(JsonObject, raw.get("callback"))
                items = self._producer.callback_resolved(callback)
                self._extensions.callback_resolved(callback)
            case _:
                items = self._extensions.runtime_event(raw)
        await self._log.append(items)
        watermark = _runtime_event_id(raw.get("eventId"))
        async with self._observed:
            if watermark is not None:
                self._observed_watermark = max(self._observed_watermark, watermark)
            self._observed.notify_all()

    async def _settled_state(self) -> tuple[PublicSessionState, str]:
        """The latest state and the bookmark of the last event it includes."""
        await self._wait_for_runtime_events()
        if self._state is None:
            raise RuntimeError("The Session API transport was not opened")
        return self._state, self._log.last_event_id

    async def _wait_for_runtime_events(self) -> None:
        """Wait until every Runtime event published so far became an API event.

        A command response carries a bookmark. Waiting here makes that bookmark
        cover the events the command itself caused. After
        ``_PUBLICATION_TIMEOUT_SECONDS`` it logs a warning and returns, so the
        reply carries the bookmark published so far.
        """
        snapshot = await self._session.read(
            RuntimeReadParams(session_id=self.session_id, history_limit=1)
        )
        target = snapshot.snapshot.watermark
        async with self._observed:
            try:
                await asyncio.wait_for(
                    self._observed.wait_for(
                        lambda: (
                            self._observed_watermark >= target
                            or self._pump is None
                            or self._pump.done()
                        )
                    ),
                    timeout=_PUBLICATION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                # The command already took effect; its remaining events still
                # reach the stream after the bookmark this reply carries.
                logger.warning(
                    "Session API events lag the Runtime: published up to %s, "
                    "Runtime at %s after %ss",
                    self._observed_watermark,
                    target,
                    _PUBLICATION_TIMEOUT_SECONDS,
                )

    async def _child_transport(
        self, session_id: str, method: str
    ) -> "LocalSessionApiTransport":
        """The transport serving a subagent of this Session, opened on first use.

        ``method`` is the procedure the caller is serving, which a refusal names.
        """
        children = self._children
        if children is None or not children.references_child(
            self.session_id, session_id
        ):
            raise UnsupportedSessionProcedureError(
                method=method,
                backend=_BACKEND,
                reason=(
                    f"this transport serves Session {self.session_id!r} "
                    "and its subagents only"
                ),
            )
        existing = self._child_transports.get(session_id)
        if existing is not None:
            return existing
        child = children.live_child(self.session_id, session_id)
        if child is None:
            raise UnsupportedSessionProcedureError(
                method=method,
                backend=_BACKEND,
                reason=f"subagent Session {session_id!r} is not loaded",
            )
        transport = LocalSessionApiTransport(
            child, session_api_view_of(child), clock=self._clock, parent=self
        )
        await transport.open()
        self._child_transports[session_id] = transport
        return transport

    async def _list_subagents(
        self, params: SubagentsListParams
    ) -> SubagentsListResponse:
        """This Session's loaded subagents, newest first, in one page."""
        if params.parent_session_id != self.session_id or self._children is None:
            return SubagentsListResponse(items=[])
        items: list[SubagentSession] = []
        for record in reversed(self._children.child_session_records(self.session_id)):
            child = self._children.live_child(self.session_id, record.child_session_id)
            if child is None:
                continue
            read = await child.read(
                RuntimeReadParams(session_id=record.child_session_id, history_limit=0)
            )
            items.append(
                SubagentSession(
                    session=session_from_public(read.snapshot.state),
                    name=record.agent_name,
                    config_name=record.agent_type,
                    # Subagents are one level deep in this Runtime.
                    agent_path=f"/root/{record.agent_name}",
                )
            )
        return SubagentsListResponse(items=items)


@dataclass(frozen=True, slots=True)
class _RuntimeTextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class _RuntimeInlineSource:
    data: str
    kind: str = "inline"


@dataclass(frozen=True, slots=True)
class _RuntimeFileSource:
    path: str
    kind: str = "file"


@dataclass(frozen=True, slots=True)
class _RuntimeAttachment:
    source: _RuntimeInlineSource | _RuntimeFileSource
    mime_type: str | None


@dataclass(frozen=True, slots=True)
class _RuntimeImageBlock:
    attachment: _RuntimeAttachment
    type: str = "image"


@dataclass(frozen=True, slots=True)
class _RuntimeResource:
    kind: str
    uri: str
    name: str | None = None
    title: str | None = None
    description: str | None = None
    media_type: str | None = None
    size: int | None = None
    text: str | None = None
    blob: str | None = None


@dataclass(frozen=True, slots=True)
class _RuntimeResourceBlock:
    resource: _RuntimeResource
    type: str = "resource"


@dataclass(frozen=True, slots=True)
class _RuntimeMessage:
    """Turn input in the shape the Runtime session's command methods read."""

    session_id: str
    message: list[object]
    idempotency_key: str | None
    client_user_message_id: str | None
    expected_turn_id: str | None = None
    user_display_content: None = None
    injected: bool = False

    @classmethod
    def of(
        cls,
        session_id: str,
        message: list[ContentBlock],
        *,
        idempotency_key: str | None,
        message_entry_id: str | None,
        expected_turn_id: str | None = None,
    ) -> "_RuntimeMessage":
        return cls(
            session_id=session_id,
            message=[_runtime_block(block) for block in message],
            idempotency_key=idempotency_key,
            client_user_message_id=message_entry_id,
            expected_turn_id=expected_turn_id,
        )

    def model_dump(self, **_kwargs: object) -> dict[str, JsonValue]:
        """The params the Runtime saves with its command receipt."""
        return {
            "sessionId": self.session_id,
            "idempotencyKey": self.idempotency_key,
            "clientUserMessageId": self.client_user_message_id,
            "expectedTurnId": self.expected_turn_id,
            "message": [
                cast(JsonValue, asdict(cast(Any, block))) for block in self.message
            ],
        }


@dataclass(frozen=True, slots=True)
class _RuntimeInterrupt:
    session_id: str
    expected_turn_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class _RuntimeCompact:
    session_id: str
    extra_instructions: str


def _runtime_block(block: ContentBlock) -> object:
    match block:
        case TextContentBlock():
            return _RuntimeTextBlock(text=block.text)
        case ImageContentBlock():
            return _runtime_image(block)
        case ResourceLinkContentBlock():
            return _RuntimeResourceBlock(
                _RuntimeResource(
                    kind="link",
                    uri=block.uri,
                    name=block.name,
                    title=block.title,
                    description=block.description,
                    media_type=block.media_type,
                    size=block.size,
                )
            )
        case EmbeddedResourceContentBlock():
            return _RuntimeResourceBlock(
                _RuntimeResource(
                    kind="text" if block.text is not None else "blob",
                    uri=block.uri,
                    media_type=block.media_type,
                    text=block.text,
                    blob=block.blob,
                )
            )
    raise ValueError(f"Unsupported content block for this Runtime: {block.type!r}")


def _runtime_image(block: ImageContentBlock) -> _RuntimeImageBlock:
    if block.uri.startswith("data:"):
        header, _, data = block.uri.partition(",")
        media_type = block.media_type or header[5:].removesuffix(";base64") or None
        return _RuntimeImageBlock(
            _RuntimeAttachment(_RuntimeInlineSource(data=data), media_type)
        )
    path = block.uri.removeprefix("file://")
    return _RuntimeImageBlock(
        _RuntimeAttachment(_RuntimeFileSource(path=path), block.media_type)
    )


def _run_after_response(result: object) -> None:
    after_response = getattr(result, "after_response", None)
    if callable(after_response):
        after_response()


def _runtime_event_id(value: object) -> int | None:
    """The Runtime's event number; queue events carry it as a string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def published_runtime_state(value: dict[str, Any]) -> PublicSessionState:
    """A published state, with its history entries kept as they are.

    Validating every entry would cost the Session's length on every update.
    """
    history = value["history"]
    state = PublicSessionState.model_validate(
        {**value, "history": {**history, "entries": []}}, extra="ignore"
    )
    return state.model_copy(
        update={
            "history": state.history.model_copy(update={"entries": history["entries"]})
        }
    )


def _fingerprint(message: list[ContentBlock], entry_id: str | None) -> str:
    return json.dumps(
        {
            "message": [
                block.model_dump(mode="json", by_alias=True) for block in message
            ],
            "entryId": entry_id,
        },
        sort_keys=True,
    )


__all__ = [
    "LocalSessionApiTransport",
    "RuntimeSession",
    "published_runtime_state",
    "session_api_view_of",
]
