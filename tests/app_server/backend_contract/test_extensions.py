from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.session import AppServerSession


@pytest.mark.asyncio
async def test_runtime_and_history_extensions_are_available_from_a_public_session(
    backend_contract_session: AppServerSession,
) -> None:
    await backend_contract_session.resources.runtime.wait_until_ready()
    history = await backend_contract_session.resources.sessions.list_history()

    assert backend_contract_session.resources.runtime.ready is True
    assert isinstance(
        backend_contract_session.resources.runtime.session_init_duration_ms, int
    )
    assert backend_contract_session.resources.runtime.session_init_duration_ms >= 0
    assert history.items == []


@pytest.mark.asyncio
async def test_history_list_filters_entries_by_public_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("first answer"),
            backend_contract_mistral_response("second answer"),
        ]
    )
    _ = [event async for event in backend_contract_session.act("first question")]
    first_turn = backend_contract_session.state.latest_turn
    assert first_turn is not None
    _ = [event async for event in backend_contract_session.act("second question")]

    page = await backend_contract_session.resources.sessions.list_history(
        turn_id=first_turn.id
    )

    assert page.items
    assert all(entry.turn_id == first_turn.id for entry in page.items)
    assert not any(
        isinstance(entry, PublicMessageEntry) and entry.text == "second question"
        for entry in page.items
    )


@pytest.mark.asyncio
async def test_history_list_paginates_in_both_directions(
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
        _ = [event async for event in backend_contract_session.act(f"question {index}")]

    history_ids = [entry.id for entry in backend_contract_session.history]
    latest = await backend_contract_session.resources.sessions.list_history(limit=2)
    assert latest.next_cursor is not None
    older = await backend_contract_session.resources.sessions.list_history(
        before=latest.next_cursor, limit=2
    )
    forward = await backend_contract_session.resources.sessions.list_history(
        after=older.items[-1].id, limit=2
    )

    assert [entry.id for entry in latest.items] == history_ids[-2:]
    assert latest.previous_cursor is None
    assert [entry.id for entry in older.items] == history_ids[-4:-2]
    assert [entry.id for entry in forward.items] == history_ids[-2:]
