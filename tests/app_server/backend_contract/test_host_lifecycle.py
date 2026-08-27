from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server.host import AppServerHost
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
)


@pytest.mark.asyncio
async def test_passive_host_lifecycle_uses_the_selected_backend(
    backend_contract_host: AppServerHost,
) -> None:
    assert await backend_contract_host.list_sessions() == []


@pytest.mark.asyncio
async def test_passive_host_lists_and_reads_a_persisted_public_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("saved")
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        session_id = session.session_id
    finally:
        await session.close()

    passive_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    try:
        sessions = await passive_connection.host.list_sessions()
        snapshot = await passive_connection.host.read_session(session_id)
    finally:
        await passive_connection.host.close()

    assert any(session.id == session_id for session in sessions)
    assert snapshot.session.id == session_id
    assert snapshot.history is not None


@pytest.mark.asyncio
async def test_host_catalog_paginates_persisted_sessions_and_selects_the_latest(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    async def persist(prompt: str) -> str:
        connection = await connect_backend_contract_host(
            experimental_harness,
            session_options=SessionOptions(),
            capabilities=ClientCapabilities(),
        )
        session = await connection.host.open_session()
        try:
            _ = [event async for event in session.act(prompt)]
            return session.session_id
        finally:
            await session.close()

    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("first answer"),
            backend_contract_mistral_response("second answer"),
            backend_contract_mistral_response("third answer"),
        ]
    )
    session_ids = [
        await persist("first"),
        await persist("second"),
        await persist("third"),
    ]

    first_page = SessionListResponse.model_validate(
        await backend_contract_persistent_connection.client.request(
            "session/list", SessionListParams(limit=1)
        )
    )
    assert first_page.next_cursor is not None
    second_page = SessionListResponse.model_validate(
        await backend_contract_persistent_connection.client.request(
            "session/list", SessionListParams(cursor=first_page.next_cursor, limit=2)
        )
    )
    page_ids = [
        *(session.id for session in first_page.items),
        *(session.id for session in second_page.items),
    ]

    assert set(session_ids).issubset(page_ids)
    assert first_page.continue_session_id == session_ids[-1]

    continued = await backend_contract_persistent_connection.host.continue_session()
    try:
        assert continued.session_id == session_ids[-1]
    finally:
        await continued.close()


@pytest.mark.asyncio
async def test_missing_persisted_session_returns_not_found_without_starting_a_new_one(
    backend_contract_host: AppServerHost,
) -> None:
    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_host.resume_session("missing-session")

    assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
