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
    ProtocolErrorCode,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionShellCommandParams,
    SessionShellCommandResponse,
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
async def test_session_archive_is_not_in_the_cli_contract() -> None:
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request("session/archive", {"sessionId": session.session_id})
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["plugin/info", "plugin/reload"])
async def test_plugin_procedures_are_not_implemented_on_the_legacy_backend(
    method: str,
) -> None:
    """Only a Unified session resolves plugins, so only it has a catalogue.

    ``plugin/info`` is served by the Unified backend; the legacy backend has
    nothing to project and says so rather than answering with an empty one.
    """
    client, session = await _session_with_history()
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(method, {})
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.NOT_IMPLEMENTED
