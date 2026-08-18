from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import connect_backend_contract_client
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryUpdated,
    SessionSnapshot,
)
from vibe.app_server.models import (
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
    ClientCapabilities,
    Notification,
    ProtocolErrorCode,
    ServerRequest,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionStartParams,
    SessionStartResponse,
    TurnCompletedParams,
    TurnStartParams,
)
from vibe.app_server.session import AppServerSession


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
    effect = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)
    assert backend_contract_session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_agent_switches_during_a_callback_and_remains_selected(
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
            await backend_contract_session.resources.agents.switch("plan")
        ).name == "plan"
        assert (
            await backend_contract_session.resources.agents.switch("ask")
        ).name == "ask"
        assert (
            await backend_contract_session.resources.agents.switch("plan")
        ).name == "plan"
        await backend_contract_session.respond_to_callback(
            event.callback.callback_id, _user_answer("Yes")
        )

    assert backend_contract_session.resources.agents.active.name == "plan"


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
async def test_rejected_callback_delivery_fails_the_turn_and_closes_the_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=_user_question_response(backend_contract_mistral_response)
    )
    client = await connect_backend_contract_client(
        experimental_harness,
        session_options=SessionOptions(enabled_tools=["ask_user_question"]),
        capabilities=ClientCapabilities(callback_kinds=["user_input"]),
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
            message = await asyncio.wait_for(anext(incoming), timeout=1)
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


async def _next_event[EventT](
    stream: AsyncGenerator[AppServerEvent, None], event_type: type[EventT]
) -> EventT:
    async for event in stream:
        if isinstance(event, event_type):
            return event
    raise AssertionError(f"Event stream ended before {event_type.__name__}")
