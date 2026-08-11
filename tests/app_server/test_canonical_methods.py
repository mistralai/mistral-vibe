"""Wire-level tests for the unified app-server contract."""

from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import attach_test_app_server_session, start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import PublicEffectEntry
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigWriteParams,
    PageRequest,
    ProtocolErrorCode,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionShellCommandParams,
    SessionShellCommandResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
)
from vibe.app_server.session import AppServerSession


async def _session_with_history() -> tuple[AppServerClient, AppServerSession]:
    backend = FakeBackend([mock_llm_chunk(content="hi there")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    _ = [event async for event in session.act("hello", client_message_id="u1")]
    return client, session


@pytest.mark.asyncio
async def test_session_read_returns_public_state_and_event_watermark() -> None:
    client, session = await _session_with_history()
    try:
        result = await client.request(
            "session/read", SessionReadParams(session_id=session.session_id)
        )
    finally:
        await session.close()

    response = SessionReadResponse.model_validate(result)
    assert response.state.session.id == session.session_id
    assert "vibe" not in result
    assert "state" in result
    assert result["lastEventId"] == response.state.event_id
    assert "latestTurn" not in result["state"]
    assert isinstance(result["state"]["history"], list)
    assert isinstance(result["state"]["turns"], list)


@pytest.mark.asyncio
async def test_wire_rejects_snake_case_params() -> None:
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request("session/read", {"session_id": session.session_id})
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_history_list_returns_one_rich_public_effect_entry() -> None:
    client, session = await _session_with_history()
    try:
        await client.request(
            "session/shellCommand",
            SessionShellCommandParams(
                session_id=session.session_id,
                command="printf 'hi'",
                operation_id="op-shell-1",
            ),
        )
        result = await client.request(
            "session/history/list",
            SessionHistoryListParams(session_id=session.session_id),
        )
    finally:
        await session.close()

    response = SessionHistoryListResponse.model_validate(result)
    effects = [
        entry for entry in response.items if isinstance(entry, PublicEffectEntry)
    ]
    assert len(effects) == 1
    assert effects[0].detail.display.summary == "shell: printf 'hi'"
    assert effects[0].state.status == "completed"


@pytest.mark.asyncio
async def test_turns_list_reconstructs_and_paginates_public_turns() -> None:
    backend = FakeBackend([[mock_llm_chunk(content="hi")] for _ in range(3)])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    try:
        for index in range(3):
            _ = [
                event
                async for event in session.act(
                    f"message {index}", client_message_id=f"message-{index}"
                )
            ]
        result = await client.request(
            "session/turns/list",
            SessionTurnsListParams(
                session_id=session.session_id, page=PageRequest(limit=2)
            ),
        )
    finally:
        await session.close()

    response = SessionTurnsListResponse.model_validate(result)
    assert len(response.items) == 2
    assert all(turn.status == "completed" for turn in response.items)
    assert response.next_cursor == response.items[0].id
    assert response.previous_cursor is None


@pytest.mark.asyncio
async def test_turns_list_returns_empty_page_for_stale_cursor() -> None:
    backend = FakeBackend([[mock_llm_chunk(content="hi")] for _ in range(3)])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    try:
        for index in range(3):
            _ = [
                event
                async for event in session.act(
                    f"message {index}", client_message_id=f"message-{index}"
                )
            ]
        responses = [
            await client.request(
                "session/turns/list",
                SessionTurnsListParams(
                    session_id=session.session_id,
                    page=PageRequest(cursor="stale", limit=2, direction=direction),
                ),
            )
            for direction in ("backward", "forward")
        ]
    finally:
        await session.close()

    for result in responses:
        response = SessionTurnsListResponse.model_validate(result)
        assert response.items == []
        assert response.next_cursor is None
        assert response.previous_cursor is None


@pytest.mark.asyncio
async def test_shell_command_rejects_cwd_outside_workspace() -> None:
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(
                "session/shellCommand",
                SessionShellCommandParams(
                    session_id=session.session_id,
                    command="printf 'hi'",
                    cwd="/",
                    operation_id="op-shell-1",
                ),
            )
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_shell_command_returns_direct_acknowledgement() -> None:
    client, session = await _session_with_history()
    try:
        result = await client.request(
            "session/shellCommand",
            SessionShellCommandParams(
                session_id=session.session_id,
                command="printf 'hi'",
                operation_id="op-shell-1",
            ),
        )
    finally:
        await session.close()

    response = SessionShellCommandResponse.model_validate(result)
    assert response.accepted is True


@pytest.mark.asyncio
async def test_config_read_and_write_use_vibe_config_surface() -> None:
    client, session = await _session_with_history()
    try:
        config = ConfigReadResponse.model_validate(
            await client.request(
                "config/read", ConfigReadParams(session_id=session.session_id)
            )
        )
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(
                "config/write",
                {
                    "sessionId": session.session_id,
                    "config": {"completion": {"model": "mistral-small-latest"}},
                },
            )
        with pytest.raises(AppServerResponseError) as patch_excinfo:
            await client.request(
                "config/patch", {"sessionId": session.session_id, "ops": []}
            )
        with pytest.raises(AppServerResponseError) as thinking_excinfo:
            await client.request(
                "config/thinking/write",
                {"sessionId": session.session_id, "level": "low"},
            )
        ConfigWriteParams.model_validate({"sessionId": session.session_id, "ops": []})
    finally:
        await session.close()

    assert config.config.active_model.alias
    assert excinfo.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert patch_excinfo.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND
    assert thinking_excinfo.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_history_list_requires_the_session_namespace() -> None:
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request("history/list", {"sessionId": session.session_id})
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["plugin/info", "plugin/reload", "session/archive"])
async def test_future_client_procedures_are_not_implemented(method: str) -> None:
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(method, {})
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.NOT_IMPLEMENTED
