from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
import os
from typing import BinaryIO, cast

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    connect_backend_contract_client,
    connect_backend_contract_host,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionSnapshot,
    TurnStarted,
)
from vibe.app_server.models import (
    AnsweredCallbackState,
    CancelledCallbackState,
    CompletedEffectState,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicTurnStatus,
    TextContentBlock,
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResult,
    CallbackResultParams,
    ClientCapabilities,
    ClientInfo,
    Notification,
    ProtocolErrorCode,
    ServerRequest,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionResumeResponse,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
    TurnCompletedParams,
    TurnStartParams,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.app_server.transport import (
    JsonRpcTransport,
    StdioJsonRpcTransport,
    memory_transport_pair,
)

pytestmark = pytest.mark.both_paths


@pytest.fixture
def backend_contract_session_options() -> SessionOptions:
    return SessionOptions(enabled_tools=["ask_user_question"])


@pytest.fixture
def backend_contract_capabilities() -> ClientCapabilities:
    return ClientCapabilities(callback_kinds=["user_input"])


def _user_question_response(
    build_response: Callable[..., httpx.Response],
) -> httpx.Response:
    return build_response(
        "",
        tool_calls=[
            {
                "id": "question-1",
                "index": 0,
                "function": {
                    "name": "ask_user_question",
                    "arguments": (
                        '{"questions":[{"question":"Ship it?",'
                        '"options":[{"label":"Yes"},{"label":"No"}]}]}'
                    ),
                },
            }
        ],
    )


def _user_answer(answer: str) -> UserInputCallbackOutput:
    return UserInputCallbackOutput(
        result=UserQuestionResult(
            answers=[UserAnswer(question="Ship it?", answer=answer)]
        )
    )


@pytest.mark.asyncio
async def test_user_input_callback_round_trips_through_the_backend(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    callback_id: str | None = None
    events = []

    async for event in backend_contract_session.act("ask me"):
        if isinstance(event, CallbackRequested):
            callback_id = event.callback.callback_id
            await backend_contract_session.respond_to_callback(
                callback_id, _user_answer("Yes")
            )
        events.append(event)

    assert callback_id is not None
    assert any(isinstance(event, HistoryEntryUpdated) for event in events)
    assert [
        event.entry.id
        for event in events
        if isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicCallbackEntry)
    ] == [callback_id]
    effect = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_answered_user_input_callback_survives_a_new_host(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_config: None,
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(enabled_tools=["ask_user_question"]),
        capabilities=ClientCapabilities(callback_kinds=["user_input"]),
        process_services=process_services,
    )
    callback_id: str | None = None
    try:
        session = await connection.host.open_session()
        try:
            async for event in session.act("ask me"):
                if isinstance(event, CallbackRequested):
                    callback_id = event.callback.callback_id
                    await session.respond_to_callback(callback_id, _user_answer("Yes"))
            session_id = session.session_id
        finally:
            await session.close()
    finally:
        await connection.host.close()

    callbacks = await _resumed_callbacks(session_id, process_services)

    assert callback_id is not None
    assert [callback.callback_id for callback in callbacks] == [callback_id]
    assert callbacks[0].state == AnsweredCallbackState(output=_user_answer("Yes"))
    assert callbacks[0].generation_status == "completed"


@pytest.mark.asyncio
async def test_interrupted_user_input_callback_survives_a_new_host(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_config: None,
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=_user_question_response(backend_contract_mistral_response)
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(enabled_tools=["ask_user_question"]),
        capabilities=ClientCapabilities(callback_kinds=["user_input"]),
        process_services=process_services,
    )
    callback_id: str | None = None
    try:
        session = await connection.host.open_session()
        try:
            async for event in session.act("ask me"):
                if isinstance(event, CallbackRequested):
                    callback_id = event.callback.callback_id
                    await session.interrupt()
            session_id = session.session_id
        finally:
            await session.close()
    finally:
        await connection.host.close()

    callbacks = await _resumed_callbacks(session_id, process_services)

    assert callback_id is not None
    assert [callback.callback_id for callback in callbacks] == [callback_id]
    assert isinstance(callbacks[0].state, CancelledCallbackState)


@pytest.mark.asyncio
async def test_approval_policy_switches_during_a_callback_and_remains_selected(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )

    async for event in backend_contract_session.act("ask me"):
        if not isinstance(event, CallbackRequested):
            continue
        assert (
            await backend_contract_session.resources.agents.switch_policy(
                "auto-approve"
            )
        ).name == "auto-approve"
        assert (
            await backend_contract_session.resources.agents.switch_policy("ask")
        ).name == "ask"
        assert (
            await backend_contract_session.resources.agents.switch_policy(
                "auto-approve"
            )
        ).name == "auto-approve"
        await backend_contract_session.respond_to_callback(
            event.callback.callback_id, _user_answer("Yes")
        )

    assert (
        backend_contract_session.resources.agents.active_policy.name == "auto-approve"
    )


@pytest.mark.asyncio
async def test_callback_response_is_idempotent_and_rejects_a_conflict(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )

    async for event in backend_contract_session.act("ask me"):
        if not isinstance(event, CallbackRequested):
            continue
        await backend_contract_session.respond_to_callback(
            event.callback.callback_id, _user_answer("Yes")
        )
        await backend_contract_session.respond_to_callback(
            event.callback.callback_id, _user_answer("Yes")
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_session.respond_to_callback(
                event.callback.callback_id, _user_answer("No")
            )
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_callback_answer_for_an_unknown_session_is_not_found(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )

    async for event in backend_contract_session.act("ask me"):
        if not isinstance(event, CallbackRequested):
            continue
        backend_contract_session._callback_sessions[event.callback.callback_id] = (
            "unknown-session"
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_session.respond_to_callback(
                event.callback.callback_id, _user_answer("Yes")
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
        del backend_contract_session._callback_sessions[event.callback.callback_id]
        await backend_contract_session.respond_to_callback(
            event.callback.callback_id, _user_answer("Yes")
        )


@pytest.mark.asyncio
async def test_interrupt_closes_an_open_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=_user_question_response(backend_contract_mistral_response)
    )
    callback_id: str | None = None

    async for event in backend_contract_session.act("ask me"):
        if isinstance(event, CallbackRequested):
            callback_id = event.callback.callback_id
            await backend_contract_session.interrupt()

    assert callback_id is not None
    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_session.respond_to_callback(
            callback_id, _user_answer("Yes")
        )

    callback = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicCallbackEntry) and entry.callback_id == callback_id
    )
    assert exc_info.value.error.code is ProtocolErrorCode.CALLBACK_CLOSED
    assert isinstance(callback.state, CancelledCallbackState)
    assert callback.generation_status == "completed"
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_reconnect_redelivers_an_open_callback_before_accepting_its_result(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        first_callback = await _next_event(stream, CallbackRequested)
        disconnected_client = backend_contract_session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()

        snapshot = await _next_event(stream, SessionSnapshot)
        redelivered = await _next_event(stream, CallbackRequested)
        assert snapshot.state.session.id == backend_contract_session.session_id
        assert redelivered.callback.callback_id == first_callback.callback.callback_id
        assert backend_contract_session._connection.current is not disconnected_client

        await backend_contract_session.respond_to_callback(
            redelivered.callback.callback_id, _user_answer("Yes")
        )
        _ = [event async for event in stream]
    finally:
        await stream.aclose()

    effect = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_a_failed_read_of_open_callbacks_after_reconnect_keeps_the_client(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        first_callback = await _next_event(stream, CallbackRequested)
        reads = 0

        async def failing_read(
            self: UnifiedHarnessBackendAdapter,
        ) -> list[PublicCallbackEntry]:
            # An attach reads twice: to check the client's capabilities, then
            # to send the callbacks again. Both fail.
            nonlocal reads
            reads += 1
            raise RuntimeError("The runtime did not answer")

        monkeypatch.setattr(
            UnifiedHarnessBackendAdapter, "read_open_callbacks", failing_read
        )
        disconnected_client = backend_contract_session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        await _next_event(stream, SessionSnapshot)
        reconnected_client = backend_contract_session._connection.current

        await backend_contract_session.respond_to_callback(
            first_callback.callback.callback_id, _user_answer("Yes")
        )
        _ = [event async for event in stream]
    finally:
        await stream.aclose()

    assert reads == 2
    assert reconnected_client is not disconnected_client
    assert backend_contract_session._connection.current is reconnected_client
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_reconnect_refuses_a_client_that_cannot_answer_an_open_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        await _next_event(stream, CallbackRequested)
        connection = backend_contract_session._connection
        connection._capabilities = ClientCapabilities(callback_kinds=[])
        disconnected_client = connection.current
        assert disconnected_client is not None
        await disconnected_client.close()

        with pytest.raises(AppServerResponseError) as exc_info:
            await _next_event(stream, SessionSnapshot)
    finally:
        await stream.aclose()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert exc_info.value.error.data == {"missingCallbackKinds": ["user_input"]}


@pytest.mark.asyncio
async def test_reconnect_without_its_capability_check_rejects_a_callback_it_cannot_answer(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        await _next_event(stream, CallbackRequested)
        reads = 0
        read = UnifiedHarnessBackendAdapter.read_open_callbacks

        async def check_read_fails(
            self: UnifiedHarnessBackendAdapter,
        ) -> list[PublicCallbackEntry]:
            # The capability check reads first and fails; the re-send's read works.
            nonlocal reads
            reads += 1
            if reads == 1:
                raise RuntimeError("The runtime did not answer")
            return await read(self)

        monkeypatch.setattr(
            UnifiedHarnessBackendAdapter, "read_open_callbacks", check_read_fails
        )
        delivered: list[str] = []
        deliver = AppServer._deliver_callback

        async def recording(server: AppServer, callback: PublicCallbackEntry) -> None:
            delivered.append(callback.callback_id)
            await deliver(server, callback)

        monkeypatch.setattr(AppServer, "_deliver_callback", recording)
        connection = backend_contract_session._connection
        connection._capabilities = ClientCapabilities(callback_kinds=[])
        disconnected_client = connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        await _next_event(stream, SessionSnapshot)
        async with asyncio.timeout(5):
            while reads < 2:
                await asyncio.sleep(0.01)
        reconnected_client = connection.current

        # The rejection fails the Turn, without an answer from the client.
        with pytest.raises(AppServerTurnError) as failed:
            _ = [event async for event in stream]
    finally:
        await stream.aclose()

    assert delivered == []
    assert str(failed.value) == "The client does not support user_input callbacks"
    assert reconnected_client is not disconnected_client
    assert connection.current is reconnected_client
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_reconnect_does_not_send_again_a_callback_delivered_live_during_its_read(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        first_callback = await _next_event(stream, CallbackRequested)
        callback_id = first_callback.callback.callback_id
        reads = 0
        read = AppServer._read_open_callbacks

        async def live_delivery_during_read(
            server: AppServer, attach_method: str
        ) -> list[PublicCallbackEntry] | None:
            # The re-send's read is a request on the Session API path: the
            # event stream can deliver the callback live while it is in flight.
            nonlocal reads
            reads += 1
            callbacks = await read(server, attach_method)
            if reads == 2:
                for callback in callbacks or []:
                    await server._deliver_callback(callback)
            return callbacks

        monkeypatch.setattr(
            AppServer, "_read_open_callbacks", live_delivery_during_read
        )
        disconnected_client = backend_contract_session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        await _next_event(stream, SessionSnapshot)
        redelivered = await _next_event(stream, CallbackRequested)

        await backend_contract_session.respond_to_callback(
            redelivered.callback.callback_id, _user_answer("Yes")
        )
        later = [
            event
            async for event in stream
            if isinstance(event, CallbackRequested)
            and event.callback.callback_id == callback_id
        ]
    finally:
        await stream.aclose()

    assert reads == 2
    assert redelivered.callback.callback_id == callback_id
    assert later == []


@pytest.mark.asyncio
async def test_a_callback_sent_again_on_reconnect_is_not_sent_live_after_it(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        first_callback = await _next_event(stream, CallbackRequested)
        callback_id = first_callback.callback.callback_id
        redeliver = AppServer._redeliver_open_callbacks

        async def live_delivery_after_redelivery(
            server: AppServer, attach_method: str
        ) -> None:
            # A subagent the stream adopts after the re-send publishes its
            # open callbacks live: the same ones the re-send just sent.
            await redeliver(server, attach_method)
            for callback in await server._read_open_callbacks(attach_method) or []:
                await server._publish_callback(callback)

        monkeypatch.setattr(
            AppServer, "_redeliver_open_callbacks", live_delivery_after_redelivery
        )
        disconnected_client = backend_contract_session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        await _next_event(stream, SessionSnapshot)
        redelivered = await _next_event(stream, CallbackRequested)

        await backend_contract_session.respond_to_callback(
            redelivered.callback.callback_id, _user_answer("Yes")
        )
        later = [
            event
            async for event in stream
            if isinstance(event, CallbackRequested)
            and event.callback.callback_id == callback_id
        ]
    finally:
        await stream.aclose()

    assert redelivered.callback.callback_id == callback_id
    assert later == []


@pytest.mark.asyncio
async def test_reconnect_announces_the_running_turn_before_its_open_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    stream = backend_contract_session.act("ask me")

    try:
        started = await _next_event(stream, TurnStarted)
        await _next_event(stream, CallbackRequested)
        disconnected_client = backend_contract_session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()

        await _next_event(stream, SessionSnapshot)
        announced = await _next_event_of(stream, (TurnStarted, CallbackRequested))
        redelivered = await _next_event(stream, CallbackRequested)

        await backend_contract_session.respond_to_callback(
            redelivered.callback.callback_id, _user_answer("Yes")
        )
        _ = [event async for event in stream]
    finally:
        await stream.aclose()

    assert isinstance(announced, TurnStarted)
    assert announced.turn.id == started.turn.id
    assert announced.turn.status is PublicTurnStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_rejected_callback_delivery_fails_the_turn_and_closes_the_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=_user_question_response(backend_contract_mistral_response)
    )
    client = await connect_backend_contract_client(
        session_options=SessionOptions(enabled_tools=["ask_user_question"]),
        capabilities=ClientCapabilities(callback_kinds=["user_input"]),
        process_services=process_services,
    )
    incoming = client.incoming()
    try:
        started = SessionStartResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        await client.request(
            "turn/start",
            TurnStartParams(
                session_id=started.state.session.id,
                message=[TextContentBlock(text="ask me")],
            ),
        )

        callback_id: str | None = None
        completed: TurnCompletedParams | None = None
        while completed is None:
            message = await asyncio.wait_for(anext(incoming), timeout=5)
            if isinstance(message, ServerRequest) and message.method == "callback/call":
                callback = CallbackCallParams.model_validate(message.params).callback
                callback_id = callback.callback_id
                await client.respond(
                    message.id,
                    CallbackCallResponse(
                        callback_id=callback.callback_id, accepted=False
                    ),
                )
                continue
            if isinstance(message, Notification) and message.method == "turn/completed":
                completed = TurnCompletedParams.model_validate(message.params)

        state = SessionReadResponse.model_validate(
            await client.request(
                "session/read", SessionReadParams(session_id=started.state.session.id)
            )
        ).state
    finally:
        await incoming.aclose()
        await client.close()

    assert callback_id is not None
    assert completed.turn.status is PublicTurnStatus.FAILED
    assert completed.turn.error is not None
    assert state.active_callbacks == []


@pytest.mark.parametrize("transport", ["memory", "stdio"])
@pytest.mark.asyncio
async def test_a_question_open_at_session_stop_is_asked_again_after_resume(
    transport: str,
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session stopped with `session/stop` while its turn waits on
        a question to the user.
    *Do*: Resume it from a new app server, then answer the question it sends.
    *Assert*: After the resume response, the client gets `turn/started` for the
        same turn, then `callback/call` with the same callback ID; the answer
        completes the turn. The stdio case runs over OS pipes, as Desktop does.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=[
            _user_question_response(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    capabilities = ClientCapabilities(callback_kinds=["user_input"])
    first = await _connect_over(transport, capabilities, process_services)
    incoming = first.incoming()
    try:
        started = SessionStartResponse.model_validate(
            await first.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id
        await first.request(
            "turn/start",
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="ask me")]
            ),
        )
        asked = await _next_callback_call(incoming)
        await first.request("session/stop", SessionStopParams(session_id=session_id))
    finally:
        await incoming.aclose()
        await first.close()
    second = await _connect_over(transport, capabilities, process_services)
    incoming = second.incoming()

    # Do
    try:
        resumed = SessionResumeResponse.model_validate(
            await second.request(
                "session/resume", SessionResumeParams(session_id=session_id)
            )
        )
        before_the_question: list[str] = []
        turn_announced: str | None = None
        reasked: CallbackCallParams | None = None
        while reasked is None:
            message = await asyncio.wait_for(anext(incoming), timeout=5)
            if isinstance(message, ServerRequest) and message.method == "callback/call":
                reasked = CallbackCallParams.model_validate(message.params)
                await second.respond(
                    message.id,
                    CallbackCallResponse(
                        callback_id=reasked.callback.callback_id, accepted=True
                    ),
                )
            elif isinstance(message, Notification):
                before_the_question.append(message.method)
                if message.method == "turn/started":
                    turn_announced = cast(dict[str, dict[str, str]], message.params)[
                        "turn"
                    ]["id"]
        await second.request(
            "callback/result",
            CallbackResultParams(
                session_id=session_id,
                result=CallbackResult(
                    callback_id=reasked.callback.callback_id,
                    output=_user_answer("Yes").model_dump(mode="json", by_alias=True),
                ),
            ),
        )
        completed: TurnCompletedParams | None = None
        while completed is None:
            message = await asyncio.wait_for(anext(incoming), timeout=5)
            if isinstance(message, Notification) and message.method == "turn/completed":
                completed = TurnCompletedParams.model_validate(message.params)
    finally:
        await incoming.aclose()
        await second.close()

    # Assert
    assert resumed.state.session.status.type in {"running", "blocked"}
    assert "turn/started" in before_the_question
    assert turn_announced == asked.callback.turn_id
    assert reasked.callback.callback_id == asked.callback.callback_id
    assert reasked.callback.turn_id == asked.callback.turn_id
    assert reasked.callback.detail.kind == "user_input"
    assert completed.turn.id == asked.callback.turn_id
    assert completed.turn.status is PublicTurnStatus.COMPLETED


async def _connect_over(
    transport: str, capabilities: ClientCapabilities, process_services: ProcessServices
) -> AppServerClient:
    client_transport, server_transport = (
        memory_transport_pair() if transport == "memory" else _stdio_transport_pair()
    )
    harness = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    await client.start()
    await client.initialize(
        ClientInfo(name="backend-contract-test", version="0"), capabilities
    )
    await client.notify("initialized")
    return client


def _stdio_transport_pair() -> tuple[JsonRpcTransport, JsonRpcTransport]:
    """Two newline-delimited JSON-RPC ends over OS pipes."""
    to_server_read, to_server_write = os.pipe()
    to_client_read, to_client_write = os.pipe()
    client = _PipeTransport(
        os.fdopen(to_client_read, "rb"), os.fdopen(to_server_write, "wb")
    )
    server = _PipeTransport(
        os.fdopen(to_server_read, "rb"), os.fdopen(to_client_write, "wb")
    )
    return client, server


class _PipeTransport(StdioJsonRpcTransport):
    """A stdio end that closes its pipe on close, so the peer reads EOF."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        super().__init__(reader, writer)
        self._pipe = writer

    async def close(self) -> None:
        await super().close()
        self._pipe.close()


async def _next_callback_call(incoming: AsyncGenerator[object]) -> CallbackCallParams:
    while True:
        message = await asyncio.wait_for(anext(incoming), timeout=5)
        if isinstance(message, ServerRequest) and message.method == "callback/call":
            return CallbackCallParams.model_validate(message.params)


async def _resumed_callbacks(
    session_id: str, process_services: ProcessServices
) -> list[PublicCallbackEntry]:
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(callback_kinds=["user_input"]),
        process_services=process_services,
    )
    try:
        resumed = await connection.host.resume_session(session_id)
        try:
            return [
                entry
                for entry in resumed.history
                if isinstance(entry, PublicCallbackEntry)
            ]
        finally:
            await resumed.close()
    finally:
        await connection.host.close()


async def _next_event[EventT](
    stream: AsyncGenerator[AppServerEvent], event_type: type[EventT]
) -> EventT:
    async for event in stream:
        if isinstance(event, event_type):
            return event
    raise AssertionError(f"Event stream ended before {event_type.__name__}")


async def _next_event_of(
    stream: AsyncGenerator[AppServerEvent], event_types: tuple[type, ...]
) -> AppServerEvent:
    async for event in stream:
        if isinstance(event, event_types):
            return event
    raise AssertionError("Event stream ended before the expected event")
