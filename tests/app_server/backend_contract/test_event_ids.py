from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.app_server.session import AppServerSession

pytestmark = pytest.mark.both_paths


def _record_event_ids(
    client: AppServerClient, monkeypatch: pytest.MonkeyPatch
) -> list[int]:
    """The ``eventId`` of every notification ``client`` receives from now on."""
    event_ids: list[int] = []
    dispatch = client._dispatch

    async def record(message: dict[str, Any]) -> None:
        params = message.get("params")
        if "id" not in message and isinstance(params, dict):
            event_id = params.get("eventId")
            if isinstance(event_id, int):
                event_ids.append(event_id)
        await dispatch(message)

    monkeypatch.setattr(client, "_dispatch", record)
    return event_ids


async def _resume_and_run_a_turn(
    session_id: str,
    prompt: str,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> tuple[int, list[int]]:
    """Resume ``session_id`` on a new Host and run one Turn there.

    Returns the resume's last event ID and the IDs of the Turn's events.
    """
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    resumed: AppServerSession = await connection.host.resume_session(session_id)
    try:
        resumed_at = resumed.state.event_id
        event_ids = _record_event_ids(connection.client, monkeypatch)
        _ = [event async for event in resumed.act(prompt)]
        return resumed_at, list(event_ids)
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_a_resumed_session_never_numbers_its_events_below_an_earlier_resume(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A persisted Session with one completed Turn.
    *Do*: Resume it on a new Host and run a Turn, twice.
    *Assert*: Each resume starts above the one before, and its Turn's events
    are numbered after the resume, so a client that keeps its event ID across
    reattaching never takes new events for ones it has already seen.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=lambda _request: backend_contract_mistral_response("answer")
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        started_at = session.state.event_id
        _ = [event async for event in session.act("first")]
        session_id = session.session_id
    finally:
        await session.close()

    # Do
    first_resume, first_turn = await _resume_and_run_a_turn(
        session_id, "second", monkeypatch, process_services
    )
    second_resume, second_turn = await _resume_and_run_a_turn(
        session_id, "third", monkeypatch, process_services
    )

    # Assert
    assert first_resume > started_at
    assert first_turn
    assert min(first_turn) > first_resume
    assert second_resume > first_resume
    assert second_turn
    assert min(second_turn) > second_resume
