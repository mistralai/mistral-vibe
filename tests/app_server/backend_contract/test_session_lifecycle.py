from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.protocol import (
    ClientCapabilities,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
)
from vibe.app_server.session import AppServerSession


@pytest.mark.asyncio
async def test_session_start_returns_the_attached_public_session(
    backend_contract_session: AppServerSession,
) -> None:
    assert (
        backend_contract_session.state.session.id == backend_contract_session.session_id
    )


@pytest.mark.asyncio
async def test_session_read_returns_the_public_snapshot_and_event_watermark(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    result = await backend_contract_connection.client.request(
        "session/read",
        SessionReadParams(session_id=backend_contract_session.session_id),
    )

    response = SessionReadResponse.model_validate(result)
    assert response.state.session.id == backend_contract_session.session_id
    assert "vibe" not in result
    assert "state" in result
    assert result["lastEventId"] == response.state.event_id
    assert "latestTurn" not in result["state"]
    assert isinstance(result["state"]["history"], list)
    assert isinstance(result["state"]["turns"], list)


@pytest.mark.asyncio
async def test_fork_attaches_the_public_child_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    source_session_id = backend_contract_persistent_session.session_id

    fork = await backend_contract_persistent_session.resources.sessions.fork("user-1")

    assert fork.source_session_id == source_session_id
    assert backend_contract_persistent_session.session_id != source_session_id
    assert (
        backend_contract_persistent_session.state.session.parent_session_id
        == source_session_id
    )
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


@pytest.mark.asyncio
async def test_detached_fork_returns_a_public_child_without_rebinding_the_source(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
        ]
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "Second", client_message_id="user-2"
        )
    ]
    source_session_id = backend_contract_persistent_session.session_id

    fork = await backend_contract_persistent_session.resources.sessions.fork(
        "user-1", attach=False
    )

    assert backend_contract_persistent_session.session_id == source_session_id
    assert fork.source_session_id == source_session_id
    assert fork.state.session.id != source_session_id
    assert fork.state.session.parent_session_id == source_session_id
    assert [
        entry.text
        for entry in fork.state.history or []
        if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


@pytest.mark.asyncio
async def test_detached_fork_is_readable_and_resumable_from_a_new_host(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    fork = await backend_contract_persistent_session.resources.sessions.fork(
        "user-1", attach=False
    )

    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    resumed: AppServerSession | None = None
    try:
        stored = await connection.host.read_session(fork.state.session.id)
        resumed = await connection.host.resume_session(fork.state.session.id)
    finally:
        if resumed is None:
            await connection.host.close()
        else:
            await resumed.close()

    assert stored.session.id == fork.state.session.id
    assert (
        stored.session.parent_session_id
        == backend_contract_persistent_session.session_id
    )
    assert resumed is not None
    assert resumed.session_id == fork.state.session.id
    assert [
        entry.text for entry in resumed.history if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


@pytest.mark.asyncio
async def test_in_place_resume_replaces_the_public_snapshot_and_keeps_the_session_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before resume"),
            backend_contract_mistral_response("After resume"),
        ]
    )
    _ = [event async for event in backend_contract_persistent_session.act("first")]
    session_id = backend_contract_persistent_session.session_id

    await backend_contract_persistent_session.resume(session_id)
    _ = [event async for event in backend_contract_persistent_session.act("second")]

    assert backend_contract_persistent_session.state.session.id == session_id
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["first", "Before resume", "second", "After resume"]


@pytest.mark.asyncio
async def test_continue_attaches_the_latest_persisted_session_and_keeps_it_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("saved"),
            backend_contract_mistral_response("continued"),
        ]
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        session_id = session.session_id
    finally:
        await session.close()

    continued_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    continued = await continued_connection.host.continue_session()
    try:
        _ = [event async for event in continued.act("continue this")]
        assert continued.session_id == session_id
        assert continued.state.session.id == session_id
        assert [
            entry.text
            for entry in continued.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["save this", "saved", "continue this", "continued"]
    finally:
        await continued.close()


@pytest.mark.asyncio
async def test_resume_attaches_the_requested_persisted_session_and_keeps_it_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("saved"),
            backend_contract_mistral_response("resumed"),
        ]
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        session_id = session.session_id
    finally:
        await session.close()

    resumed_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        _ = [event async for event in resumed.act("resume this")]
        assert resumed.session_id == session_id
        assert [
            entry.text
            for entry in resumed.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["save this", "saved", "resume this", "resumed"]
    finally:
        await resumed.close()
