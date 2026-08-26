from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server.events import (
    CallbackRequested,
    HistoryEntryAdded,
    SessionSnapshot,
    StatsUpdated,
)
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CompletedEffectState,
    PublicEffectEntry,
    PublicMessageEntry,
    ResourceContentBlock,
    TextContentBlock,
    TurnErrorCode,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    PageRequest,
    ProtocolErrorCode,
    SessionOptions,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    TurnSteerParams,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.user_content import UserResourceLink


@pytest.mark.asyncio
async def test_turn_streams_public_events_over_json_rpc(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("hello")
    )
    context_window = backend_contract_session.resources.runtime.context_window

    events = [
        event
        async for event in backend_contract_session.act("hi", client_message_id="u1")
    ]

    assert backend_contract_mistral_api.called
    stats_updated = next(event for event in events if isinstance(event, StatsUpdated))
    assert stats_updated.params.context_window == context_window
    user = next(
        event.entry
        for event in events
        if isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicMessageEntry)
        and event.entry.role == "user"
    )
    assert user.id == "u1"
    assert user.text == "hi"
    assistant = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    )
    assert assistant.text == "hello"
    assert all(
        entry.generation_status == "completed"
        for entry in backend_contract_session.history
    )


@pytest.mark.asyncio
async def test_read_file_tool_call_runs_through_the_backend(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    experimental_harness: bool,
    tmp_path,
) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("from file\n", encoding="utf-8")
    tool_arguments = (
        {"path": str(target)} if experimental_harness else {"file_path": str(target)}
    )
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "read-1",
                        "index": 0,
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps(tool_arguments),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("read done"),
        ]
    )
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path), enabled_tools=["read_file"], auto_approve=True
        ),
        capabilities=ClientCapabilities(),
    )
    try:
        session = await connection.host.open_session()

        _ = [event async for event in session.act("read notes")]

        assistant = next(
            entry
            for entry in session.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
        )
        effect = next(
            entry for entry in session.history if isinstance(entry, PublicEffectEntry)
        )
        assert assistant.text == "read done"
        assert isinstance(effect.state, CompletedEffectState)
        assert backend_contract_mistral_api.call_count == 2
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_shell_tool_call_runs_through_the_backend(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    experimental_harness: bool,
    tmp_path,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "bash-1",
                        "index": 0,
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps({
                                "command": "printf shell-ok",
                                "timeout_seconds": 5,
                            }),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("shell done"),
        ]
    )
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path), enabled_tools=["bash"], auto_approve=True
        ),
        capabilities=ClientCapabilities(),
    )
    try:
        session = await connection.host.open_session()

        _ = [event async for event in session.act("run shell")]

        assistant = next(
            entry
            for entry in session.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
        )
        effect = next(
            entry for entry in session.history if isinstance(entry, PublicEffectEntry)
        )
        assert assistant.text == "shell done"
        assert isinstance(effect.state, CompletedEffectState)
        assert backend_contract_mistral_api.call_count == 2
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_shell_tool_call_waits_for_approval(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    experimental_harness: bool,
    tmp_path,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "bash-1",
                        "index": 0,
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps({
                                "command": "printf approved",
                                "timeout_seconds": 5,
                            }),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("approved done"),
        ]
    )
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(cwd=str(tmp_path), enabled_tools=["bash"]),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
    )
    try:
        session = await connection.host.open_session()

        callbacks = []
        async for event in session.act("run shell with approval"):
            if isinstance(event, CallbackRequested):
                callbacks.append(event.callback)
                await session.respond_to_callback(
                    event.callback.callback_id,
                    ApprovalCallbackOutput(
                        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                    ),
                )

        assistant = next(
            entry
            for entry in session.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
        )
        effect = next(
            entry for entry in session.history if isinstance(entry, PublicEffectEntry)
        )
        assert [callback.detail.kind for callback in callbacks] == ["approval"]
        assert assistant.text == "approved done"
        assert isinstance(effect.state, CompletedEffectState)
        assert backend_contract_mistral_api.call_count == 2
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_interrupt_closes_shell_approval_callback(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    experimental_harness: bool,
    tmp_path,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response(
            "",
            tool_calls=[
                {
                    "id": "bash-1",
                    "index": 0,
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({
                            "command": "printf late",
                            "timeout_seconds": 5,
                        }),
                    },
                }
            ],
        )
    )
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(cwd=str(tmp_path), enabled_tools=["bash"]),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
    )
    callback_id: str | None = None
    try:
        session = await connection.host.open_session()

        async for event in session.act("run shell with approval"):
            if isinstance(event, CallbackRequested):
                callback_id = event.callback.callback_id
                await session.interrupt()

        assert callback_id is not None
        with pytest.raises(AppServerResponseError) as exc_info:
            await session.respond_to_callback(
                callback_id,
                ApprovalCallbackOutput(
                    decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                ),
            )
        assert exc_info.value.error.code is ProtocolErrorCode.CALLBACK_CLOSED
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_context_injection_adds_a_public_user_message(
    backend_contract_session: AppServerSession,
) -> None:
    events = await backend_contract_session.inject_user_context(
        "Use this detail", as_message=True, client_message_id="context-1"
    )

    assert len(events) == 1
    entry = events[0].entry
    assert isinstance(entry, PublicMessageEntry)
    assert entry.id == "context-1"
    assert entry.text == "Use this detail"


@pytest.mark.asyncio
async def test_turn_preserves_rich_resource_content_in_the_public_history(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("reviewed")
    )
    resource = UserResourceLink(
        uri="file:///workspace/spec.md",
        media_type="text/markdown",
        title="Specification",
    )

    _ = [
        event
        async for event in backend_contract_session.act(
            "Review this", resources=[resource], client_message_id="resource-user"
        )
    ]

    user = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicMessageEntry) and entry.id == "resource-user"
    )
    assert user.text == "Review this"
    assert any(
        isinstance(block, ResourceContentBlock)
        and isinstance(block.resource, UserResourceLink)
        and block.resource.uri == resource.uri
        and block.resource.title == resource.title
        for block in user.content
    )


@pytest.mark.asyncio
async def test_failed_turn_is_typed_and_leaves_the_session_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            httpx.Response(400, json={"message": "provider rejected the request"}),
            backend_contract_mistral_response("recovered"),
        ]
    )

    with pytest.raises(AppServerTurnError) as exc_info:
        _ = [event async for event in backend_contract_session.act("fail first")]

    recovered = [event async for event in backend_contract_session.act("try again")]

    assert exc_info.value.error.code == TurnErrorCode.BACKEND_ERROR
    assert backend_contract_session.state.session.status.type == "idle"
    assert any(isinstance(event, StatsUpdated) for event in recovered)
    assert any(
        isinstance(entry, PublicMessageEntry)
        and entry.role == "assistant"
        and entry.text == "recovered"
        for entry in backend_contract_session.history
    )


@pytest.mark.asyncio
async def test_turns_list_reconstructs_and_paginates_public_turns(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(f"answer {index}") for index in range(3)
        ]
    )
    for index in range(3):
        _ = [
            event
            async for event in backend_contract_session.act(
                f"message {index}", client_message_id=f"message-{index}"
            )
        ]

    result = await backend_contract_connection.client.request(
        "session/turns/list",
        SessionTurnsListParams(
            session_id=backend_contract_session.session_id, page=PageRequest(limit=2)
        ),
    )

    response = SessionTurnsListResponse.model_validate(result)
    assert len(response.items) == 2
    assert all(turn.status == "completed" for turn in response.items)
    assert response.next_cursor == response.items[0].id
    assert response.previous_cursor is None


@pytest.mark.asyncio
async def test_turns_list_returns_an_empty_page_for_a_stale_cursor(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(f"answer {index}") for index in range(3)
        ]
    )
    for index in range(3):
        _ = [
            event
            async for event in backend_contract_session.act(
                f"message {index}", client_message_id=f"message-{index}"
            )
        ]

    responses = [
        await backend_contract_connection.client.request(
            "session/turns/list",
            SessionTurnsListParams(
                session_id=backend_contract_session.session_id,
                page=PageRequest(cursor="stale", limit=2, direction=direction),
            ),
        )
        for direction in ("backward", "forward")
    ]

    for result in responses:
        response = SessionTurnsListResponse.model_validate(result)
        assert response.items == []
        assert response.next_cursor is None
        assert response.previous_cursor is None


@pytest.mark.asyncio
async def test_interrupt_terminates_an_active_public_turn(
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "unreachable", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in backend_contract_session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await backend_contract_session.interrupt()
        await turn
    finally:
        release.set()
        if not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn

    user = next(
        entry
        for entry in backend_contract_session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    )
    assert user.text == "wait"
    assert backend_contract_session.state.session.status.type == "idle"
    assert backend_contract_session.state.latest_turn is not None
    assert backend_contract_session.state.latest_turn.status == "interrupted"


@pytest.mark.asyncio
async def test_steering_adds_a_public_user_message_to_an_active_turn(
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "continued", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in backend_contract_session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        active_turn = backend_contract_session.state.latest_turn
        assert active_turn is not None
        await backend_contract_session.inject_user_context(
            "follow up", as_message=True, client_message_id="steer-user"
        )
        for _ in range(20):
            if any(
                entry.id == "steer-user" for entry in backend_contract_session.history
            ):
                break
            await asyncio.sleep(0)

        steered = next(
            entry
            for entry in backend_contract_session.history
            if entry.id == "steer-user"
        )
        assert isinstance(steered, PublicMessageEntry)
        assert steered.text == "follow up"
        assert steered.source == "turn_steer"
        assert steered.turn_id == active_turn.id
    finally:
        release.set()
        await turn


@pytest.mark.asyncio
async def test_stale_turn_control_returns_a_typed_error(
    backend_contract_connection: BackendContractConnection,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "continued", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in backend_contract_session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "turn/steer",
                TurnSteerParams(
                    session_id=backend_contract_session.session_id,
                    expected_turn_id="stale-turn",
                    message=[TextContentBlock(text="follow up")],
                ),
            )
    finally:
        release.set()
        await turn

    assert exc_info.value.error.code is ProtocolErrorCode.STALE_TURN


@pytest.mark.asyncio
async def test_reconnect_replays_turn_output_emitted_while_detached(
    backend_contract_connection: BackendContractConnection,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "Recovered output", started=started, release=release
        )
    )

    async def consume_turn() -> list[object]:
        return [
            event
            async for event in backend_contract_session.act("finish while detached")
        ]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await backend_contract_connection.client.close()
        release.set()
        events = await asyncio.wait_for(turn, timeout=1)
    finally:
        release.set()
        if not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn

    assert any(isinstance(event, SessionSnapshot) for event in events)
    recovered = [
        event.entry
        for event in events
        if isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicMessageEntry)
        and event.entry.role == "assistant"
    ]
    assert [entry.text for entry in recovered] == ["Recovered output"]
