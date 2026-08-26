from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server.models import PublicCheckpointEntry, PublicMessageEntry
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.app_server.session import AppServerSession


@pytest.mark.asyncio
async def test_completed_turn_is_visible_in_the_persisted_session_log(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )

    _ = [event async for event in backend_contract_persistent_session.act("save this")]
    log = await backend_contract_persistent_session.resources.sessions.read_log()
    session_id = backend_contract_persistent_session.session_id
    await backend_contract_persistent_session.resume(session_id)

    assert log.enabled is True
    assert log.persisted is True
    assert backend_contract_persistent_session.exit_summary().session_id == (session_id)


@pytest.mark.asyncio
async def test_completed_turn_survives_a_new_host(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
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
        assert resumed.session_id == session_id
        assert [
            entry.text
            for entry in resumed.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["save this", "stored"]
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_cleared_replacement_becomes_resumable_after_its_first_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before"),
            backend_contract_mistral_response("After"),
        ]
    )

    _ = [event async for event in backend_contract_persistent_session.act("first")]
    session_id = backend_contract_persistent_session.session_id
    assert backend_contract_persistent_session.exit_summary().session_id == session_id

    await backend_contract_persistent_session.clear_history()
    replacement_session_id = backend_contract_persistent_session.session_id
    assert backend_contract_persistent_session.exit_summary().session_id is None

    _ = [event async for event in backend_contract_persistent_session.act("second")]

    assert replacement_session_id != session_id
    assert (
        backend_contract_persistent_session.exit_summary().session_id
        == replacement_session_id
    )
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["first", "Before", "second", "After"]
    assert all(
        entry.session_id == replacement_session_id
        for entry in backend_contract_persistent_session.history
    )
    assert any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "clear"
        for entry in backend_contract_persistent_session.history
    )


@pytest.mark.asyncio
async def test_compaction_keeps_the_public_session_and_history(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_completion: Callable[[str], httpx.Response],
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before compaction"),
            backend_contract_mistral_completion(
                "<summary>First turn completed</summary>"
            ),
        ]
    )

    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "first question", client_message_id="user-1"
        )
    ]
    session_id = backend_contract_persistent_session.session_id
    summary = await backend_contract_persistent_session.compact()

    assert summary == "First turn completed"
    assert backend_contract_persistent_session.session_id == session_id
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["first question", "Before compaction"]
    assert (
        sum(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
            for entry in backend_contract_persistent_session.history
        )
        == 1
    )


@pytest.mark.asyncio
async def test_compaction_survives_a_new_host_with_the_same_public_identity(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_completion: Callable[[str], httpx.Response],
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before compaction"),
            backend_contract_mistral_completion(
                "<summary>First turn completed</summary>"
            ),
        ]
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("first question")]
        session_id = session.session_id
        await session.compact()
    finally:
        await session.close()

    resumed_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        assert resumed.session_id == session_id
        assert [
            entry.text
            for entry in resumed.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["first question", "Before compaction"]
        assert (
            sum(
                isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
                for entry in resumed.history
            )
            == 1
        )
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_in_place_rewind_preserves_identity_and_truncates_public_history(
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
            "first", client_message_id="user-1"
        )
    ]
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "second", client_message_id="user-2"
        )
    ]
    session_id = backend_contract_persistent_session.session_id

    rewound = await backend_contract_persistent_session.resources.sessions.rewind(
        "user-2", restore_files=False, inplace=True
    )

    assert rewound.state.session.id == session_id
    assert backend_contract_persistent_session.session_id == session_id
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["first", "First answer"]
    assert any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "rewind"
        for entry in backend_contract_persistent_session.history
    )
