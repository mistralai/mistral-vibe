"""Translation between the Vibe app-server port and the Unified Harness."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Never, cast

from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustHarnessConfig,
)

# `mistralai-unified-harness` is an optional extra, so an environment that never
# installs it — CI's type-check job included — cannot resolve these.
from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
    PublicSession as HarnessPublicSession,
    PublicSessionState as HarnessPublicSessionState,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
    SessionStartParams as HarnessSessionStartParams,
)
from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
    HarnessNotImplementedError,
    HarnessSessionError,
    HarnessSessionNotFoundError,
    HarnessSessionSubscription,
    LegacySourceLoader,
    LegacySourceResolver,
    LocalRuntimeAdapterConfig,
    UnifiedHarnessSessionBackend,
    UnifiedHarnessSessionBackendHost,
)
from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
    PluginLockV1,
)

from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._model import validate_wire
from vibe.app_server._session_backend_port import (
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendKind,
    SessionBackendResult,
    SessionEventSubscription,
    SessionForkResult,
    SessionLifecycleResult,
)
from vibe.app_server._state import history_page
from vibe.app_server.events import (
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionSnapshot,
    SessionUpdated,
    StatsUpdated,
    TurnCompleted,
    TurnStarted,
    reconcile_snapshot,
)
from vibe.app_server.models import (
    AccountStatus,
    AccountView,
    AgentStatsSnapshot,
    BlockedSessionStatus as VibeBlockedSessionStatus,
    FailedSessionStatus as VibeFailedSessionStatus,
    IdleSessionStatus as VibeIdleSessionStatus,
    PreparedPrompt,
    PublicError,
    PublicHistoryEntry,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    RunningSessionStatus as VibeRunningSessionStatus,
    SessionLogSummary,
    TokenUsage as VibeTokenUsage,
    TurnErrorCode,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AccountReadResponse,
    EmptyResponse,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    IdentityReadResponse,
    PageRequest,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    RuntimeUpdatedParams,
    SessionContinueParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyReadResponse,
    SessionReadyWaitResponse,
    SessionResumeParams,
    SessionStartParams,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    StatsUpdatedParams,
    TurnCompletedParams,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartedParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
)


@dataclass(frozen=True, slots=True)
class UnifiedSessionContext:
    runtime: RuntimeSnapshot
    storage_root: str
    legacy_source_loader: LegacySourceLoader
    legacy_source_resolver: LegacySourceResolver
    core_config: RustHarnessConfig
    plugin_lock: PluginLockV1
    adapter_config: LocalRuntimeAdapterConfig


type SessionContextBuilder = Callable[
    [SessionOptions], Awaitable[UnifiedSessionContext]
]


def adapt_harness_host(
    host: object, build_session_context: SessionContextBuilder
) -> UnifiedHarnessBackendHostAdapter:
    return UnifiedHarnessBackendHostAdapter(
        cast(UnifiedHarnessSessionBackendHost, host), build_session_context
    )


class UnifiedHarnessBackendHostAdapter:
    """Vibe's session Host backed by the Unified Harness Runtime."""

    def __init__(
        self,
        host: UnifiedHarnessSessionBackendHost,
        build_context: SessionContextBuilder,
    ) -> None:
        self._host = host
        self._build_context = build_context

    @property
    def harness_kind(self) -> SessionBackendKind:
        return self._host.harness_kind

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        options = params.agent_config
        context = await self._context(options)
        session = await _harness_call(
            self._host.start(
                HarnessSessionStartParams(history_limit=params.history_limit),
                cwd=_session_cwd(options),
            )
        )
        return SessionLifecycleResult(
            backend=UnifiedHarnessBackendAdapter(
                session, _session_cwd(options), context.runtime
            )
        )

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        context = await self._context(params.agent_config)
        session = await _harness_call(
            self._host.resume(params.session_id, history_limit=params.history_limit)
        )
        return SessionLifecycleResult(
            backend=UnifiedHarnessBackendAdapter(session, session.cwd, context.runtime)
        )

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        context = await self._context(params.agent_config)
        session = await _harness_call(
            self._host.continue_latest(history_limit=params.history_limit)
        )
        return SessionLifecycleResult(
            backend=UnifiedHarnessBackendAdapter(session, session.cwd, context.runtime)
        )

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        options = params.agent_config or SessionOptions()
        context = await self._context(options)
        result = await _harness_call(
            self._host.fork(
                params.source_session_id, history_limit=params.history_limit
            )
        )
        backend = UnifiedHarnessBackendAdapter(
            result.session, result.session.cwd, context.runtime
        )
        snapshot = await backend.read(
            SessionReadParams(
                session_id=backend.session_id,
                history=PageRequest(limit=params.history_limit),
            )
        )
        attached: UnifiedHarnessBackendAdapter | None = backend
        if not params.attach:
            await backend.shutdown()
            attached = None
        return SessionForkResult(
            response=SessionForkResponse(
                source_session_id=params.source_session_id,
                state=snapshot.state,
                last_event_id=snapshot.last_event_id,
            ),
            backend=attached,
        )

    async def list(self, params: SessionListParams) -> SessionListResponse:
        options = SessionOptions(cwd=params.cwd)
        await self._context(options)
        result = await _harness_call(
            self._host.list(
                limit=params.limit,
                cursor=params.cursor,
                cwd=_session_cwd(options) if params.cwd is not None else None,
                root_session_id=params.root_session_id,
                parent_session_id=params.parent_session_id,
            )
        )
        return SessionListResponse(
            items=[_public_session(item.session, item.cwd) for item in result.items],
            next_cursor=result.next_cursor,
            previous_cursor=result.previous_cursor,
            continue_session_id=result.continue_session_id,
        )

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        options = SessionOptions()
        await self._context(options)
        result = await _harness_call(self._host.read(_harness_read_params(params)))
        return _read_response(result.snapshot, result.cwd)

    async def shutdown(self) -> None:
        await self._host.shutdown()

    async def _context(self, options: SessionOptions) -> UnifiedSessionContext:
        context = await self._build_context(options)
        self._host.configure_storage(context.storage_root)
        self._host.configure_legacy_source_loader(context.legacy_source_loader)
        self._host.configure_legacy_source_resolver(context.legacy_source_resolver)
        self._host.configure_runtime(
            context.core_config, context.plugin_lock, context.adapter_config
        )
        return context


class UnifiedHarnessBackendAdapter:
    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        cwd: str | None,
        runtime: RuntimeSnapshot,
    ) -> None:
        self._session = session
        self._cwd = cwd
        self._runtime = runtime
        self._event_id = 0

    @property
    def session_id(self) -> str:
        return self._session.session_id

    def runtime_updated_params(self) -> RuntimeUpdatedParams:
        return RuntimeUpdatedParams(session_id=self.session_id, runtime=self._runtime)

    async def dispatch_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "runtime/read":
                validate_wire(RuntimeReadParams, raw_params)
                response = RuntimeReadResponse(
                    runtime=self._runtime,
                    session_log=SessionLogSummary(
                        enabled=True, session_id=self.session_id, persisted=True
                    ),
                    ready=True,
                )
            case "session/ready/wait":
                response = SessionReadyWaitResponse(ready=True, init_duration_ms=0)
            case "session/ready/read":
                response = SessionReadyReadResponse(ready=True)
            case "account/read":
                response = AccountReadResponse(
                    account=AccountView(status=AccountStatus.READY)
                )
            case "identity/read":
                response = IdentityReadResponse(identity=None)
            case "telemetry/record":
                response = EmptyResponse()
            case "feedback/shouldShow":
                params = validate_wire(FeedbackShouldShowParams, raw_params)
                if params.session_id != self.session_id:
                    raise SessionBackendError(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    )
                response = FeedbackShouldShowResponse(show=False)
            case "workspace/prompt/prepare":
                params = validate_wire(WorkspacePromptPrepareParams, raw_params)
                if params.session_id != self.session_id:
                    raise SessionBackendError(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    )
                # ponytail: text-only preparation for the foundation PR. Move
                # mention/image/title parity into a backend-neutral service in VIBE-4074.
                response = WorkspacePromptPrepareResponse(
                    prompt=PreparedPrompt(
                        display_text=params.message, prompt_text=params.message
                    )
                )
            case "session/history/list":
                params = validate_wire(SessionHistoryListParams, raw_params)
                if params.session_id != self.session_id:
                    raise SessionBackendError(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    )
                state = await self._read_page_state(params.session_id)
                page = history_page(
                    state.history or [],
                    turn_id=params.turn_id,
                    before=params.cursor
                    if params.sort_direction == "backward"
                    else None,
                    after=params.cursor if params.sort_direction == "forward" else None,
                    limit=params.limit,
                )
                response = SessionHistoryListResponse(
                    items=page.entries,
                    next_cursor=(
                        page.cursor.before
                        if params.sort_direction == "backward"
                        else page.cursor.after
                    ),
                    previous_cursor=(
                        page.cursor.after
                        if params.sort_direction == "backward"
                        else page.cursor.before
                    ),
                )
            case "session/turns/list":
                params = validate_wire(SessionTurnsListParams, raw_params)
                if params.session_id != self.session_id:
                    raise SessionBackendError(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    )
                state = await self._read_page_state(params.session_id)
                response = _turns_list_response(
                    _turns_from_history(state.history or [], state.session.id), params
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        result = await self._session.read(_harness_read_params(params))
        return self._read_response(result.snapshot)

    async def subscribe(self, params: SessionReadParams) -> SessionEventSubscription:
        subscription = await self._session.subscribe(_harness_read_params(params))
        snapshot = self._read_response(subscription.snapshot)
        return SessionEventSubscription(
            snapshot=snapshot,
            events=self._translated_events(subscription, snapshot.state),
        )

    def guard_request(self) -> None:
        self._session.guard_request()

    async def switch_agent(self, params: object) -> Never:
        _reject("agent/switch")

    async def update_settings(self, params: object) -> Never:
        _reject("session/settings/update")

    async def write_config(self, params: object) -> Never:
        _reject("config/write")

    async def reload_config(self, params: object) -> Never:
        _reject("config/reload")

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        result = cast(Any, await _harness_call(self._session.start_turn(params)))
        response = result.response
        turn = response.turn
        return SessionBackendResult(
            response=TurnStartResponse(
                turn=PublicTurn(
                    id=turn.id,
                    session_id=turn.session_id,
                    status=PublicTurnStatus.IN_PROGRESS,
                    started_at=turn.started_at,
                ),
                last_event_id=self._event_id,
            ),
            after_response=result.after_response,
        )

    async def steer_turn(self, params: TurnSteerParams) -> Never:
        await _harness_call(self._session.steer_turn(params))
        raise AssertionError("unreachable")

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> SessionBackendResult[TurnInterruptResponse]:
        result = cast(Any, await _harness_call(self._session.interrupt_turn(params)))
        response = result.response
        return SessionBackendResult(
            response=TurnInterruptResponse(
                accepted=response["accepted"], last_event_id=response["last_event_id"]
            ),
            after_response=result.after_response,
        )

    async def inject_context(self, params: object) -> Never:
        _reject("context/inject")

    async def respond_to_callback(self, params: object) -> Never:
        await _harness_call(self._session.respond_to_callback(params))
        raise AssertionError("unreachable")

    async def compact(self, params: object) -> Never:
        _reject("session/compact")

    async def shutdown(self) -> None:
        await self._session.shutdown()

    async def _read_page_state(self, session_id: str) -> PublicSessionState:
        result = await self._session.read(
            _harness_read_params(
                SessionReadParams(
                    session_id=session_id,
                    history=PageRequest(limit=500),
                    turns=PageRequest(limit=500),
                )
            )
        )
        return _read_response(result.snapshot, self._cwd).state

    def _read_response(self, snapshot: HarnessSessionSnapshot) -> SessionReadResponse:
        response = _read_response(snapshot, self._cwd, event_id=self._event_id)
        self._event_id = response.last_event_id
        return response

    async def _translated_events(
        self, subscription: HarnessSessionSubscription, previous: PublicSessionState
    ) -> AsyncIterator[SessionBackendEvent]:
        async for event in subscription.events:
            if event.get("type") != "session_state_updated":
                _reject(f"the Harness session event {event.get('type', event)!r}")
            raw_state = event.get("state")
            if not isinstance(raw_state, dict):
                _reject("a Harness session update without state")
            watermark = event.get("eventId")
            if not isinstance(watermark, int):
                _reject("a Harness session update without an event id")
            state = HarnessPublicSessionState.model_validate(raw_state)
            current = _read_response(
                HarnessSessionSnapshot(
                    state=state,
                    history_limit=subscription.snapshot.history_limit,
                    watermark=watermark,
                ),
                self._cwd,
                event_id=self._event_id,
            ).state
            stats_updated = previous.session.token_usage != current.session.token_usage
            for app_event in reconcile_snapshot(previous, current):
                if isinstance(app_event, SessionSnapshot):
                    continue
                if isinstance(app_event, TurnCompleted) and stats_updated:
                    usage = current.session.token_usage
                    stats = AgentStatsSnapshot(
                        session_prompt_tokens=usage.input_tokens if usage else 0,
                        session_completion_tokens=usage.output_tokens if usage else 0,
                        context_tokens=usage.input_tokens if usage else 0,
                    )
                    self._event_id += 1
                    yield _event_envelope(
                        StatsUpdated(
                            StatsUpdatedParams(
                                event_id=self._event_id,
                                session_id=current.session.id,
                                emitted_at=int(time.time() * 1000),
                                stats=stats,
                                context_window=0,
                            )
                        ),
                        self._event_id,
                    )
                    stats_updated = False
                self._event_id += 1
                yield _event_envelope(app_event, self._event_id)
            if stats_updated:
                usage = current.session.token_usage
                stats = AgentStatsSnapshot(
                    session_prompt_tokens=usage.input_tokens if usage else 0,
                    session_completion_tokens=usage.output_tokens if usage else 0,
                    context_tokens=usage.input_tokens if usage else 0,
                )
                self._event_id += 1
                yield _event_envelope(
                    StatsUpdated(
                        StatsUpdatedParams(
                            event_id=self._event_id,
                            session_id=current.session.id,
                            emitted_at=int(time.time() * 1000),
                            stats=stats,
                            context_window=0,
                        )
                    ),
                    self._event_id,
                )
            previous = current.model_copy(
                update={"event_id": self._event_id}, deep=True
            )


def _session_cwd(options: SessionOptions) -> str:
    return str(Path(options.cwd or Path.cwd()).expanduser().resolve())


def _harness_read_params(params: SessionReadParams) -> HarnessSessionReadParams:
    return HarnessSessionReadParams(
        session_id=params.session_id, history_limit=params.history_limit
    )


def _read_response(
    snapshot: HarnessSessionSnapshot, cwd: str | None, *, event_id: int | None = None
) -> SessionReadResponse:
    history = []
    for raw_entry in snapshot.state.history.entries:
        normalized = dict(raw_entry)
        normalized.pop("outcome", None)
        history.append(validate_history_entry(normalized))
    last_event_id = (
        snapshot.watermark if event_id is None else max(event_id, snapshot.watermark)
    )
    return SessionReadResponse(
        state=PublicSessionState(
            event_id=last_event_id,
            session=_public_session(snapshot.state.session, cwd),
            history=history,
            turns=(
                [_public_turn(snapshot.state.latest_turn)]
                if snapshot.state.latest_turn is not None
                else []
            ),
        ),
        last_event_id=last_event_id,
    )


def _public_session(session: HarnessPublicSession, cwd: str | None) -> PublicSession:
    status = cast(Any, session.status)
    if getattr(status, "type", None) == "running":
        public_status = VibeRunningSessionStatus(active_turn_id=status.active_turn_id)
    elif getattr(status, "type", None) == "blocked":
        public_status = VibeBlockedSessionStatus(
            active_turn_id=status.active_turn_id,
            callback_id=status.callback_id,
            reason=status.callback_kind,
        )
    elif getattr(status, "type", None) == "failed":
        public_status = VibeFailedSessionStatus(message=status.message)
    else:
        public_status = VibeIdleSessionStatus()
    token_usage = (
        VibeTokenUsage.model_validate(
            session.token_usage.model_dump(mode="json", by_alias=True)
        )
        if session.token_usage is not None
        else None
    )
    return PublicSession(
        id=session.id,
        root_session_id=session.root_session_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        preview=session.preview,
        status=public_status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        cwd=cwd,
        token_usage=token_usage,
    )


def _public_turn(turn: object) -> PublicTurn:
    raw = cast(Any, turn)
    error = getattr(raw, "error", None)
    return PublicTurn(
        id=raw.id,
        session_id=raw.session_id,
        status=PublicTurnStatus(str(raw.status)),
        started_at=raw.started_at,
        completed_at=getattr(raw, "completed_at", None),
        error=_public_turn_error(error),
        stop_reason=getattr(raw, "stop_reason", None),
    )


def _public_turn_error(error: object | None) -> PublicError | None:
    if error is None:
        return None
    raw_error = cast(Any, error)
    public_error = PublicError.model_validate(
        raw_error.model_dump(mode="json", by_alias=True)
    )
    if public_error.code == "model_stream_failed":
        public_error = public_error.model_copy(
            update={"code": TurnErrorCode.BACKEND_ERROR}
        )
    return public_error


def _turns_list_response(
    turns: list[PublicTurn], params: SessionTurnsListParams
) -> SessionTurnsListResponse:
    if params.sort_direction == "backward":
        if params.cursor is None:
            page = turns[-params.limit :]
            first_index = max(0, len(turns) - len(page))
        else:
            end = next(
                (index for index, turn in enumerate(turns) if turn.id == params.cursor),
                0,
            )
            first_index = max(0, end - params.limit)
            page = turns[first_index:end]
        last_index = first_index + len(page) - 1
    else:
        first_index = (
            0
            if params.cursor is None
            else next(
                (
                    index + 1
                    for index, turn in enumerate(turns)
                    if turn.id == params.cursor
                ),
                len(turns),
            )
        )
        page = turns[first_index : first_index + params.limit]
        last_index = first_index + len(page) - 1
    next_cursor = page[0].id if page and first_index > 0 else None
    previous_cursor = page[-1].id if page and last_index < len(turns) - 1 else None
    if params.sort_direction == "forward":
        next_cursor, previous_cursor = previous_cursor, next_cursor
    return SessionTurnsListResponse(
        items=page, next_cursor=next_cursor, previous_cursor=previous_cursor
    )


def _turns_from_history(
    history: list[PublicHistoryEntry], session_id: str
) -> list[PublicTurn]:
    turns: dict[str, PublicTurn] = {}
    for entry in history:
        if entry.turn_id is None:
            continue
        previous = turns.get(entry.turn_id)
        turns[entry.turn_id] = PublicTurn(
            id=entry.turn_id,
            session_id=session_id,
            status=PublicTurnStatus.COMPLETED,
            started_at=entry.created_at if previous is None else previous.started_at,
            completed_at=entry.updated_at,
        )
    return list(turns.values())


def _event_envelope(event: object, event_id: int) -> SessionBackendEvent:
    emitted_at = int(time.time() * 1000)
    if isinstance(event, HistoryEntryAdded):
        params = HistoryEntryAddedParams(
            event_id=event_id,
            session_id=event.entry.session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry=event.entry,
        )
        return SessionBackendEvent(
            event=event,
            method="history/entryAdded",
            params=params,
            session_id=event.entry.session_id,
            event_id=event_id,
        )
    if isinstance(event, HistoryEntryUpdated):
        params = HistoryEntryUpdatedParams(
            event_id=event_id,
            session_id=event.entry.session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry_id=event.entry.id,
            patch=event.patch,
        )
        return SessionBackendEvent(
            event=event,
            method="history/entryUpdated",
            params=params,
            session_id=event.entry.session_id,
            event_id=event_id,
        )
    if isinstance(event, SessionUpdated):
        params = SessionUpdatedParams(
            event_id=event_id,
            session_id=event.session.id,
            emitted_at=emitted_at,
            patch=event.patch,
        )
        return SessionBackendEvent(
            event=event,
            method="session/updated",
            params=params,
            session_id=event.session.id,
            event_id=event_id,
        )
    if isinstance(event, TurnStarted):
        params = TurnStartedParams(
            event_id=event_id,
            session_id=event.turn.session_id,
            emitted_at=emitted_at,
            turn=event.turn,
        )
        return SessionBackendEvent(
            event=event,
            method="turn/started",
            params=params,
            session_id=event.turn.session_id,
            event_id=event_id,
        )
    if isinstance(event, TurnCompleted):
        params = TurnCompletedParams(
            event_id=event_id,
            session_id=event.turn.session_id,
            emitted_at=emitted_at,
            turn=event.turn,
        )
        return SessionBackendEvent(
            event=event,
            method="turn/completed",
            params=params,
            session_id=event.turn.session_id,
            event_id=event_id,
        )
    if isinstance(event, StatsUpdated):
        params = event.params.model_copy(update={"event_id": event_id})
        return SessionBackendEvent(
            event=event,
            method="session/statsUpdated",
            params=params,
            session_id=params.session_id,
            event_id=event_id,
        )
    raise TypeError(f"Unsupported app-server event: {event!r}")


async def _harness_call[ResultT](operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except HarnessSessionNotFoundError as exc:
        raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
    except HarnessNotImplementedError as exc:
        raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except HarnessSessionError as exc:
        if exc.code == "stale_turn":
            data: dict[str, Any] = {}
            active_turn_id = exc.details.get("active_turn_id") if exc.details else None
            if active_turn_id is None:
                raise SessionBackendError(
                    ProtocolErrorCode.CONFLICT, "No active turn"
                ) from exc
            data["activeTurnId"] = active_turn_id
            raise SessionBackendError(
                ProtocolErrorCode.STALE_TURN, str(exc), data or None
            ) from exc
        code = (
            ProtocolErrorCode.CONFLICT
            if exc.code
            in {
                "session_busy",
                "client_command_conflict",
                "turn_conflict",
                "unfinished_work_migration",
            }
            else ProtocolErrorCode.INTERNAL_ERROR
        )
        data: dict[str, Any] = {"harnessCode": exc.code}
        if exc.details is not None:
            data["details"] = exc.details
        raise SessionBackendError(code, str(exc), data) from exc
    except ValueError as exc:
        raise SessionBackendError(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc


def _reject(operation: str) -> Never:
    raise SessionBackendError(
        ProtocolErrorCode.INTERNAL_ERROR,
        f"The Unified Harness backend does not implement {operation} yet.",
    )


__all__ = [
    "UnifiedHarnessBackendAdapter",
    "UnifiedHarnessBackendHostAdapter",
    "UnifiedSessionContext",
    "adapt_harness_host",
]
