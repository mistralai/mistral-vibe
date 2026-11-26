"""`session/context/read` splits the measured usage by the real model input."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.protocol import (
    ClientCapabilities,
    ContextCategoryId,
    SessionContextReadParams,
    SessionContextReadResponse,
    SessionOptions,
    SessionResumeParams,
)
from vibe.app_server.session import AppServerSession


async def _read(
    connection: BackendContractConnection, session: AppServerSession
) -> SessionContextReadResponse:
    return validate_wire(
        SessionContextReadResponse,
        await connection.client.request(
            "session/context/read",
            SessionContextReadParams(session_id=session.session_id),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.both_paths
async def test_context_read_splits_the_last_model_input(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session that has not called the model.

    Do read the breakdown, run one turn, read it again.

    Assert nothing is estimated before the call; after it, the API total is split
    over the system prompt, the tool catalogue and the conversation the call sent.
    """
    before = await _read(backend_contract_connection, backend_contract_session)
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("hello")
    )
    async for _ in backend_contract_session.act("hi", client_message_id="u1"):
        pass

    after = (
        await _read(backend_contract_connection, backend_contract_session)
    ).breakdown

    assert before.breakdown.measured is False
    assert before.breakdown.categories == []
    assert after.measured is True
    characters = {c.id: c.characters for c in after.categories}
    assert characters[ContextCategoryId.SYSTEM] > 0
    assert characters[ContextCategoryId.TOOLS] > 0
    assert characters[ContextCategoryId.CONVERSATION] >= len("hi")
    assert sum(c.estimated_tokens for c in after.categories) == after.used_tokens


@pytest.mark.asyncio
@pytest.mark.both_paths
async def test_a_resumed_session_keeps_the_split_of_its_last_call(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    """Prepare a saved session that made one model call, then close its host.

    Do resume it in a new host and read the breakdown before any new call.

    Assert the split is the one the closed host measured, not a bare total.
    """
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("hello")
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        async for _ in session.act("hi", client_message_id="u1"):
            pass
        before = (
            await _read(backend_contract_persistent_connection, session)
        ).breakdown
    finally:
        await session.close()
    await backend_contract_persistent_connection.host.close()
    resumed = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        await resumed.client.request(
            "session/resume",
            SessionResumeParams(session_id=session.session_id, history_limit=10),
        )
        after = (await _read(resumed, session)).breakdown
    finally:
        await resumed.host.close()

    assert after.categories == before.categories
    assert after.used_tokens == before.used_tokens
    assert {c.id for c in after.categories} >= {
        ContextCategoryId.SYSTEM,
        ContextCategoryId.TOOLS,
    }
