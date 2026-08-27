from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass

from pydantic import ValidationError

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._streaming import finish_event_queue
from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.client_state import ClientBootstrap, ClientSessionState
from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.connection import AppServerConnection, AppServerResourceConnection
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    EventSequenceError,
    HistoryEntryAdded,
    SessionCompacted,
    SessionContextCleared,
    SessionUpdated,
    StatsUpdated,
    TurnCompleted,
    parse_server_event,
    reconcile_snapshot,
)
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CallbackOutput,
    ContentBlock,
    ImageAttachment,
    ImageContentBlock,
    MentionStats,
    PublicCallbackEntry,
    PublicError,
    PublicHistoryEntry,
    PublicSessionState,
    PublicTurnStatus,
    ResourceContentBlock,
    TextContentBlock,
    TokenUsage,
    UserDisplayContent,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResult,
    CallbackResultError,
    CallbackResultParams,
    CallbackResultResponse,
    ClientCapabilities,
    ClientInfo,
    ClientToolMethod,
    ClientToolReadTextFileParams,
    ClientToolTerminalCreateParams,
    ClientToolTerminalParams,
    ClientToolWriteTextFileParams,
    ContextInjectParams,
    ContextInjectResponse,
    Notification,
    ProtocolError,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    ServerRequest,
    SessionContinueParams,
    SessionContinueResponse,
    SessionKind,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionResumeResponse,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
    SessionStopResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)
from vibe.app_server.resources import AppServerResources
from vibe.user_content import UserResource


@dataclass(frozen=True, slots=True)
class _StreamClosed:
    error: Exception | None = None


type _QueuedEvent = AppServerEvent | _StreamClosed

_EVENT_QUEUE_MAX_SIZE = 256


class AppServerTurnError(RuntimeError):
    def __init__(self, error: PublicError | None) -> None:
        self.error = error or PublicError(message="App-server turn failed")
        super().__init__(self.error.message)


@dataclass(frozen=True, slots=True)
class SessionExitSummary:
    session_id: str | None
    usage: TokenUsage


class AppServerSession:
    def __init__(
        self,
        client: AppServerClient,
        bootstrap: ClientBootstrap,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        client_tool_handler: ClientToolHandler | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> None:
        if capabilities.client_tools and client_tool_handler is None:
            raise ValueError("Client tool capabilities require a client tool handler")
        self._state = ClientSessionState(bootstrap)
        self._connection = AppServerConnection(
            client,
            self._state,
            client_info,
            capabilities,
            client_factory=client_factory,
        )
        resource_connection = AppServerResourceConnection(
            self._connection, self._ensure_attached
        )
        self.resources = AppServerResources(resource_connection, self._state)
        self._consumed_turn_id: str | None = None
        self._starting_turn = False
        self._callback_sessions: dict[str, str] = {}
        self._events: asyncio.Queue[_QueuedEvent] = asyncio.Queue(
            maxsize=_EVENT_QUEUE_MAX_SIZE
        )
        self._unsolicited_events: asyncio.Queue[_QueuedEvent] = asyncio.Queue(
            maxsize=_EVENT_QUEUE_MAX_SIZE
        )
        self._message_task: asyncio.Task[None] | None = None
        self._client_tool_handler = client_tool_handler
        self._client_request_tasks: set[asyncio.Task[None]] = set()

    @classmethod
    async def start(
        cls,
        client: AppServerClient,
        *,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        session_options: SessionOptions | None = None,
        resume_session_id: str | None = None,
        continue_session: bool = False,
        client_tool_handler: ClientToolHandler | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> AppServerSession:
        from vibe.app_server.host import AppServerHost

        host = await AppServerHost.connect(
            client,
            client_info=client_info,
            capabilities=capabilities,
            session_options=session_options,
            resume_session_id=resume_session_id,
            continue_session=continue_session,
            client_tool_handler=client_tool_handler,
            client_factory=client_factory,
        )
        try:
            return await host.open_session()
        except BaseException:
            await host.close()
            raise

    @classmethod
    async def open(
        cls,
        client: AppServerClient,
        *,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        session_options: SessionOptions | None = None,
        resume_session_id: str | None = None,
        continue_session: bool = False,
        session_kind: SessionKind = SessionKind.NORMAL,
        client_tool_handler: ClientToolHandler | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> AppServerSession:
        if resume_session_id is not None and continue_session:
            raise ValueError("Cannot resume a specific session and continue the latest")
        agent_config = session_options or SessionOptions()
        if continue_session:
            state = validate_wire(
                SessionContinueResponse,
                await client.request(
                    "session/continue", SessionContinueParams(agent_config=agent_config)
                ),
            ).state
        elif resume_session_id is None:
            state = validate_wire(
                SessionStartResponse,
                await client.request(
                    "session/start",
                    SessionStartParams(agent_config=agent_config, kind=session_kind),
                ),
            ).state
        else:
            state = validate_wire(
                SessionResumeResponse,
                await client.request(
                    "session/resume",
                    SessionResumeParams(
                        session_id=resume_session_id, agent_config=agent_config
                    ),
                ),
            ).state
        bootstrap = await _read_bootstrap(client, state)
        session = cls(
            client,
            bootstrap,
            client_info,
            capabilities,
            client_tool_handler,
            client_factory,
        )
        session._connection.adopt_initialized_session(state.session.id)
        await session._ensure_attached()
        return session

    @property
    def session_id(self) -> str:
        return self._state.session_id

    @property
    def cwd(self) -> str:
        cwd = self.state.session.cwd
        if cwd is None:
            raise RuntimeError("The active app-server session has no working directory")
        return cwd

    @property
    def state(self) -> PublicSessionState:
        return self._state.state

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self._state.projection.history

    @property
    def turn_active(self) -> bool:
        return self._starting_turn or any(
            turn.status is PublicTurnStatus.IN_PROGRESS
            for turn in self.state.turns or []
        )

    def exit_summary(self) -> SessionExitSummary:
        session_log = self.resources.runtime.session_log
        session_id = (
            session_log.session_id
            if session_log.enabled and session_log.persisted
            else None
        )
        return SessionExitSummary(
            session_id=session_id, usage=self._state.usage_since_baseline()
        )

    async def connect(self) -> None:
        await self._ensure_attached()

    async def act(
        self,
        message: str,
        client_message_id: str | None = None,
        *,
        auto_title: str | None = None,
        images: list[ImageAttachment] | None = None,
        resources: list[UserResource] | None = None,
        user_display_content: UserDisplayContent | None = None,
        mention_stats: MentionStats | None = None,
        injected: bool = False,
    ) -> AsyncGenerator[AppServerEvent, None]:
        client = await self._ensure_attached()
        if self.turn_active:
            raise RuntimeError("A turn is already running")
        self._starting_turn = True
        try:
            response = validate_wire(
                TurnStartResponse,
                await client.request(
                    "turn/start",
                    TurnStartParams(
                        session_id=self.session_id,
                        message=_content_blocks(message, images, resources),
                        injected=injected,
                        client_user_message_id=client_message_id,
                        auto_title=auto_title,
                        user_display_content=user_display_content,
                        mention_stats=mention_stats,
                    ),
                ),
            )
            turn = response.turn
            self._state.projection.begin_turn(turn)
            self._consumed_turn_id = turn.id
        finally:
            self._starting_turn = False
        try:
            while True:
                item = await self._events.get()
                if isinstance(item, _StreamClosed):
                    raise item.error or RuntimeError(
                        "App-server event stream closed unexpectedly"
                    )
                if isinstance(item, TurnCompleted) and item.turn.id == turn.id:
                    self._consumed_turn_id = None
                    await self.resources.runtime.refresh()
                    if item.turn.status is PublicTurnStatus.FAILED:
                        raise AppServerTurnError(item.turn.error)
                    return
                yield item
        except asyncio.CancelledError:
            with suppress(Exception):
                await self.interrupt()
            raise
        finally:
            if self._consumed_turn_id == turn.id:
                self._consumed_turn_id = None

    async def events(self) -> AsyncGenerator[AppServerEvent, None]:
        await self._ensure_attached()
        while True:
            item = await self._unsolicited_events.get()
            if isinstance(item, _StreamClosed):
                raise item.error or RuntimeError(
                    "App-server event stream closed unexpectedly"
                )
            yield item

    async def inject_user_context(
        self,
        content: str,
        *,
        as_message: bool = False,
        inject_invoked_skill: bool = False,
        images: list[ImageAttachment] | None = None,
        resources: list[UserResource] | None = None,
        client_message_id: str | None = None,
        mention_stats: MentionStats | None = None,
    ) -> list[HistoryEntryAdded]:
        client = await self._ensure_attached()
        blocks = _content_blocks(content, images, resources)
        active_turn_id = self._active_public_turn_id()
        if active_turn_id is not None:
            validate_wire(
                TurnSteerResponse,
                await client.request(
                    "turn/steer",
                    TurnSteerParams(
                        session_id=self.session_id,
                        expected_turn_id=active_turn_id,
                        message=blocks,
                        client_user_message_id=client_message_id,
                        inject_invoked_skill=inject_invoked_skill,
                        mention_stats=mention_stats,
                    ),
                    wait_for_incoming=True,
                ),
            )
            return []
        response = validate_wire(
            ContextInjectResponse,
            await client.request(
                "session/context/inject",
                ContextInjectParams(
                    session_id=self.session_id,
                    input=blocks,
                    as_message=as_message,
                    inject_invoked_skill=inject_invoked_skill,
                    client_user_message_id=client_message_id,
                    mention_stats=mention_stats,
                ),
            ),
        )
        return [HistoryEntryAdded(entry) for entry in response.entries]

    async def interrupt(self) -> None:
        active_turn_id = self._active_public_turn_id()
        if active_turn_id is None:
            return
        client = await self._ensure_attached()
        try:
            validate_wire(
                TurnInterruptResponse,
                await client.request(
                    "turn/interrupt",
                    TurnInterruptParams(
                        session_id=self.session_id, expected_turn_id=active_turn_id
                    ),
                    wait_for_incoming=True,
                ),
            )
        except AppServerResponseError as exc:
            if self._interrupt_already_settled(exc):
                return
            raise

    def _active_public_turn_id(self) -> str | None:
        return next(
            (
                turn.id
                for turn in self.state.turns or []
                if turn.status is PublicTurnStatus.IN_PROGRESS
            ),
            self._consumed_turn_id,
        )

    @staticmethod
    def _interrupt_already_settled(exc: AppServerResponseError) -> bool:
        error = exc.error
        if error.code is ProtocolErrorCode.STALE_TURN:
            return True
        if error.code is not ProtocolErrorCode.CONFLICT:
            return False
        return error.message in {"No active turn", "No matching active turn"}

    async def respond_to_callback(
        self, callback_id: str, output: CallbackOutput
    ) -> None:
        client = await self._ensure_attached()
        params = CallbackResultParams(
            session_id=self._callback_sessions.get(callback_id, self.session_id),
            result=CallbackResult(
                callback_id=callback_id,
                output=output.model_dump(mode="json", by_alias=True),
            ),
        )
        validate_wire(
            CallbackResultResponse, await client.request("callback/result", params)
        )

    async def reject_callback(
        self, callback_id: str, error: CallbackResultError
    ) -> None:
        client = await self._ensure_attached()
        params = CallbackResultParams(
            session_id=self._callback_sessions.get(callback_id, self.session_id),
            result=CallbackResult(callback_id=callback_id, error=error),
        )
        validate_wire(
            CallbackResultResponse, await client.request("callback/result", params)
        )

    async def deny_callback(
        self, callback: PublicCallbackEntry, feedback: str | None = None
    ) -> None:
        match callback.detail.kind:
            case "approval":
                output: CallbackOutput = ApprovalCallbackOutput(
                    decision=ApprovalDecision(type=ApprovalDecisionType.DENY),
                    feedback=feedback,
                )
            case "user_input":
                output = UserInputCallbackOutput(
                    result=UserQuestionResult(answers=[], cancelled=True)
                )
        await self.respond_to_callback(callback.callback_id, output)

    async def resume(self, session_id: str) -> None:
        await self.resources.sessions.resume(session_id)
        self.resources.vibe_code.reset()
        await self.resources.refresh()

    async def compact(self, extra_instructions: str = "") -> str:
        summary = await self.resources.sessions.compact(extra_instructions)
        await self.resources.refresh()
        return summary

    async def clear_history(self) -> None:
        await self.resources.sessions.clear_history()
        await self.resources.refresh()

    async def close(self) -> None:
        await self.resources.telemetry.flush()
        client = self._connection.current
        if client is not None:
            with suppress(Exception):
                validate_wire(
                    SessionStopResponse,
                    await client.request(
                        "session/stop", SessionStopParams(session_id=self.session_id)
                    ),
                )
        if self._message_task is not None:
            self._message_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._message_task
        client_request_tasks = list(self._client_request_tasks)
        for task in client_request_tasks:
            task.cancel()
        if client_request_tasks:
            await asyncio.gather(*client_request_tasks, return_exceptions=True)
            self._client_request_tasks.clear()
        await self._connection.close()
        self._close_event_streams()

    async def _ensure_attached(self) -> AppServerClient:
        client = await self._connection.connect()
        if snapshot := self._connection.take_snapshot():
            await self._publish_snapshot(snapshot.previous, snapshot.current)
        if self._message_task is None:
            self._message_task = asyncio.create_task(self._pump_messages())
        return client

    async def _pump_messages(self) -> None:
        while client := self._connection.current:
            error: Exception = AppServerConnectionClosed("App-server connection closed")
            try:
                async for message in client.incoming():
                    match message:
                        case Notification():
                            await self._handle_notification(client, message)
                        case ServerRequest():
                            if message.method == "callback/call":
                                await self._handle_request(message)
                                continue
                            task = asyncio.create_task(self._handle_request(message))
                            self._client_request_tasks.add(task)
                            task.add_done_callback(self._client_request_finished)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc
            if not await self._connection.reconnect(client):
                self._close_event_streams(error)
                return
            try:
                await self._ensure_attached()
            except Exception as exc:
                self._close_event_streams(exc)
                return

    async def _handle_notification(
        self, client: AppServerClient, notification: Notification
    ) -> None:
        if await self.resources.consume_notification(notification):
            return
        if server_event := parse_server_event(notification):
            await self._publish_event(server_event)
            return
        try:
            event = self._state.projection.consume(notification)
        except EventSequenceError:
            await self._resync(client)
            return
        if event is None:
            return
        if isinstance(event, StatsUpdated):
            self._state.stats = event.params.stats
            self._state.context_window = event.params.context_window
        if isinstance(event, SessionContextCleared):
            self._state.reset_usage_baseline()
        if isinstance(event, SessionCompacted | SessionContextCleared):
            self._state.session_log = event.params.session_log
            self._connection.mark_session_attached()
            self._callback_sessions = {
                callback_id: event.params.session_id
                if session_id == event.params.old_session_id
                else session_id
                for callback_id, session_id in self._callback_sessions.items()
            }
        if isinstance(event, SessionUpdated):
            if event.session.title != event.previous.title:
                self._state.session_log = self._state.session_log.model_copy(
                    update={"title": event.session.title}
                )
            if event.session.agent is not None:
                self._state.active_agent = event.session.agent
        if await self.resources.consume_event(event):
            return
        await self._publish_event(event)

    async def _resync(self, client: AppServerClient) -> None:
        state = validate_wire(
            SessionReadResponse,
            await client.request(
                "session/read", SessionReadParams(session_id=self.session_id)
            ),
        ).state
        previous = self._state.projection.state
        self._state.projection.replace_state(state)
        await self._publish_snapshot(previous, state)

    async def _publish_snapshot(
        self, previous: PublicSessionState, current: PublicSessionState
    ) -> None:
        for event in reconcile_snapshot(previous, current):
            await self._publish_event(event)

    async def _handle_request(self, request: ServerRequest) -> None:
        client = self._connection.current
        if client is None:
            return
        if request.method == "callback/call":
            await self._handle_callback_request(client, request)
            return
        handler = self._client_tool_handler
        if handler is None:
            await self._respond_method_not_found(client, request)
            return
        try:
            method = ClientToolMethod(request.method)
        except ValueError:
            await self._respond_method_not_found(client, request)
            return
        try:
            result = await self._dispatch_client_tool(handler, method, request.params)
        except ValidationError as exc:
            await client.respond(
                request.id,
                error=ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS, message=str(exc)
                ),
            )
            return
        except Exception as exc:
            await client.respond(
                request.id,
                error=ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR, message=str(exc)
                ),
            )
            return
        await client.respond(request.id, result)

    @staticmethod
    async def _dispatch_client_tool(
        handler: ClientToolHandler, method: ClientToolMethod, params: object
    ) -> ProtocolModel:
        match method:
            case ClientToolMethod.READ_TEXT_FILE:
                result: ProtocolModel = await handler.read_text_file(
                    validate_wire(ClientToolReadTextFileParams, params)
                )
            case ClientToolMethod.WRITE_TEXT_FILE:
                result = await handler.write_text_file(
                    validate_wire(ClientToolWriteTextFileParams, params)
                )
            case ClientToolMethod.TERMINAL_CREATE:
                result = await handler.create_terminal(
                    validate_wire(ClientToolTerminalCreateParams, params)
                )
            case ClientToolMethod.TERMINAL_WAIT:
                result = await handler.wait_for_terminal_exit(
                    validate_wire(ClientToolTerminalParams, params)
                )
            case ClientToolMethod.TERMINAL_OUTPUT:
                result = await handler.terminal_output(
                    validate_wire(ClientToolTerminalParams, params)
                )
            case ClientToolMethod.TERMINAL_KILL:
                result = await handler.kill_terminal(
                    validate_wire(ClientToolTerminalParams, params)
                )
            case ClientToolMethod.TERMINAL_RELEASE:
                result = await handler.release_terminal(
                    validate_wire(ClientToolTerminalParams, params)
                )
        return result

    @staticmethod
    async def _respond_method_not_found(
        client: AppServerClient, request: ServerRequest
    ) -> None:
        await client.respond(
            request.id,
            error=ProtocolError(
                code=ProtocolErrorCode.METHOD_NOT_FOUND,
                message=f"Unknown server method: {request.method}",
            ),
        )

    async def _handle_callback_request(
        self, client: AppServerClient, request: ServerRequest
    ) -> None:
        try:
            params = validate_wire(CallbackCallParams, request.params)
        except ValidationError as exc:
            await client.respond(
                request.id,
                error=ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS, message=str(exc)
                ),
            )
            return
        callback = params.callback
        self._callback_sessions[callback.callback_id] = callback.session_id
        if (
            callback.session_id == self.session_id
            and self._state.projection.ensure_callback(callback)
        ):
            await self._publish_event(HistoryEntryAdded(callback))
        await self._publish_event(CallbackRequested(callback))
        await client.respond(
            request.id, CallbackCallResponse(callback_id=params.callback.callback_id)
        )

    def _client_request_finished(self, task: asyncio.Task[None]) -> None:
        self._client_request_tasks.discard(task)
        if task.cancelled():
            return
        if isinstance(error := task.exception(), Exception):
            self._close_event_streams(error)

    def _close_event_streams(self, error: Exception | None = None) -> None:
        closed = _StreamClosed(error)
        for queue in (self._events, self._unsolicited_events):
            finish_event_queue(queue, closed)

    async def _publish_event(self, event: AppServerEvent) -> None:
        queue = (
            self._events
            if self._starting_turn or self._consumed_turn_id is not None
            else self._unsolicited_events
        )
        await queue.put(event)


async def _read_bootstrap(
    client: AppServerClient, state: PublicSessionState
) -> ClientBootstrap:
    return ClientBootstrap(
        state=state,
        runtime=validate_wire(
            RuntimeReadResponse,
            await client.request(
                "runtime/read", RuntimeReadParams(session_id=state.session.id)
            ),
        ),
    )


def _content_blocks(
    text: str,
    images: list[ImageAttachment] | None,
    resources: list[UserResource] | None = None,
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = [
        TextContentBlock(text=text),
        *[ImageContentBlock(attachment=image) for image in images or []],
        *[ResourceContentBlock(resource=resource) for resource in resources or []],
    ]
    return blocks
