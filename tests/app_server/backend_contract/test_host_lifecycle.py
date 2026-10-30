from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.host import AppServerHost
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
)


@pytest.mark.asyncio
async def test_passive_host_lifecycle_uses_the_selected_backend(
    backend_contract_host: AppServerHost,
) -> None:
    assert await backend_contract_host.list_sessions() == []


@pytest.mark.asyncio
async def test_host_renames_the_attached_session(
    backend_contract_connection: BackendContractConnection,
) -> None:
    """*Prepare*: A session is attached to either supported backend.
    *Do*: Rename it through the public app-server route.
    *Assert*: The response and subsequent read expose the new title.
    """
    # Prepare
    session = await backend_contract_connection.host.open_session()

    try:
        # Do
        response = SessionTitleUpdateResponse.model_validate(
            await backend_contract_connection.client.request(
                "session/rename",
                SessionTitleUpdateParams(
                    session_id=session.session_id, title="  Renamed session  "
                ),
            )
        )
        snapshot = SessionReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "session/read", SessionReadParams(session_id=session.session_id)
            )
        ).state
    finally:
        await session.close()

    # Assert
    assert response.title == "Renamed session"
    assert snapshot.session.title == "Renamed session"


async def _rename(
    connection: BackendContractConnection, session_id: str, title: str
) -> SessionTitleUpdateResponse:
    return SessionTitleUpdateResponse.model_validate(
        await connection.client.request(
            "session/rename",
            SessionTitleUpdateParams(session_id=session_id, title=title),
        )
    )


async def _list_on_a_new_host(*, process_services: ProcessServices) -> list[str | None]:
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        return [session.title for session in await connection.host.list_sessions()]
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_renaming_an_unsaved_session_titles_it_without_persisting_it(
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session with persistent logging that never ran a Turn.
    *Do*: Rename it, then list sessions from a new Host.
    *Assert*: The live session carries the title, but nothing was saved.
    """
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        response = await _rename(
            backend_contract_persistent_connection, session.session_id, "Manual title"
        )
        snapshot = SessionReadResponse.model_validate(
            await backend_contract_persistent_connection.client.request(
                "session/read", SessionReadParams(session_id=session.session_id)
            )
        ).state
        log = await session.resources.sessions.read_log()
    finally:
        await session.close()

    assert response.title == "Manual title"
    assert snapshot.session.title == "Manual title"
    assert log.persisted is False
    assert await _list_on_a_new_host(process_services=process_services) == []


@pytest.mark.asyncio
async def test_a_renamed_saved_session_is_listed_under_its_new_title(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A persisted session that ran one Turn.
    *Do*: Rename it, close it, and list sessions from a new Host.
    *Assert*: The catalogue the resume picker reads shows the new title.
    """
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("saved")
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        await _rename(
            backend_contract_persistent_connection, session.session_id, "New title"
        )
    finally:
        await session.close()

    assert await _list_on_a_new_host(process_services=process_services) == ["New title"]


@pytest.mark.asyncio
async def test_passive_host_lists_and_reads_a_persisted_public_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
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
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
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
    process_services: ProcessServices,
) -> None:
    async def persist(prompt: str, *, process_services: ProcessServices) -> str:
        connection = await connect_backend_contract_host(
            session_options=SessionOptions(),
            capabilities=ClientCapabilities(),
            process_services=process_services,
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
        await persist("first", process_services=process_services),
        await persist("second", process_services=process_services),
        await persist("third", process_services=process_services),
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
