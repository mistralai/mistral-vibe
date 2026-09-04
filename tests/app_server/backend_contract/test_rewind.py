from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession


def _texts(session: AppServerSession) -> list[str]:
    return [
        entry.text for entry in session.history if isinstance(entry, PublicMessageEntry)
    ]


def _user_entry_id(session: AppServerSession, text: str) -> str:
    for entry in session.history:
        if (
            isinstance(entry, PublicMessageEntry)
            and entry.role == "user"
            and entry.text == text
        ):
            return entry.id
    raise AssertionError(f"No user history entry with text {text!r}: {_texts(session)}")


async def _act(session: AppServerSession, prompt: str, client_message_id: str) -> None:
    _ = [
        event
        async for event in session.act(prompt, client_message_id=client_message_id)
    ]


def _last_prompt(route: respx.Route) -> list[str]:
    """The non-system turns of the conversation the last model call was sent."""
    payload = json.loads(route.calls.last.request.content)
    texts: list[str] = []
    for message in payload["messages"]:
        if message["role"] == "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        for block in content or []:
            if block.get("type") == "text":
                texts.append(block["text"])
    return texts


@pytest.mark.asyncio
async def test_in_place_rewind_of_the_only_message_returns_it_and_keeps_the_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    session_id = backend_contract_persistent_session.session_id
    entry_id = _user_entry_id(backend_contract_persistent_session, "First")

    paths = await backend_contract_persistent_session.resources.sessions.rewind_preview(
        entry_id
    )
    result = await backend_contract_persistent_session.resources.sessions.rewind(
        entry_id, restore_files=False, inplace=True
    )

    assert paths == []
    assert result.message == "First"
    assert result.restore_errors == []
    assert backend_contract_persistent_session.session_id == session_id
    assert result.state.session.id == session_id
    assert _texts(backend_contract_persistent_session) == []


@pytest.mark.asyncio
async def test_in_place_rewind_leaves_the_session_usable_for_the_next_turn(
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
    await _act(backend_contract_persistent_session, "First", "user-1")
    session_id = backend_contract_persistent_session.session_id

    # A rewind is issued the moment the client sees the turn complete, so the
    # backend must not reject it for a turn that is only still winding down.
    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Second", "user-2")
    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=True,
    )

    assert result.message == "Second"
    assert backend_contract_persistent_session.session_id == session_id
    assert _texts(backend_contract_persistent_session) == []


@pytest.mark.asyncio
async def test_the_turn_that_replaces_a_rewound_turn_is_answered_by_the_model(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # A rewind hands the backend a conversation rebuilt from history, and the
    # turns of a rebuilt conversation are numbered from the beginning again. The
    # turn that takes over a rewound turn's number must still be put to the
    # model rather than answered from what that number once resolved to.
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_mistral_response("Third answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")

    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Third", "user-3")

    assert _last_prompt(backend_contract_mistral_api) == [
        "First",
        "First answer",
        "Third",
    ]
    assert _texts(backend_contract_persistent_session) == [
        "First",
        "First answer",
        "Third",
        "Third answer",
    ]


@pytest.mark.asyncio
async def test_two_sequential_in_place_rewinds_leave_the_abandoned_turns_behind(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # Rewinding twice without a turn in between walks the same numbering back
    # over itself, so the turn resumed from the second rewind lands on a number
    # that two abandoned turns have already used.
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_mistral_response("Weather answer"),
            backend_contract_mistral_response("Fourth answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")
    await _act(backend_contract_persistent_session, "Weather", "user-3")

    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Weather"),
        restore_files=False,
        inplace=True,
    )
    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Fourth", "user-4")

    assert _last_prompt(backend_contract_mistral_api) == [
        "First",
        "First answer",
        "Fourth",
    ]
    assert _texts(backend_contract_persistent_session) == [
        "First",
        "First answer",
        "Fourth",
        "Fourth answer",
    ]


@pytest.mark.asyncio
async def test_a_turn_kept_by_a_rewind_can_be_rewound_onto_after_the_next_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # An in-place rewind rebuilds the conversation from its own transcript, and
    # the turns it keeps go on being published under the ids already recorded
    # for them. Those ids have to survive the rebuild, or the next rewind onto
    # one of the kept turns has nothing left to anchor to.
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_mistral_response("Third answer"),
            backend_contract_mistral_response("Fourth answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")

    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Third", "user-3")
    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Fourth", "user-4")

    assert result.message == "First"
    assert _last_prompt(backend_contract_mistral_api) == ["Fourth"]
    assert _texts(backend_contract_persistent_session) == ["Fourth", "Fourth answer"]


@pytest.mark.asyncio
async def test_a_turn_can_be_rewound_after_an_earlier_turn_was_interrupted(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # Interrupting a turn cancels the request that started it, and a rewind
    # needs the session settled enough to be read back from storage. Ending a
    # turn early has to leave it as settled as letting it finish would.
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_gated_mistral_response(
                "unreachable", started=started, release=release
            ),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    session_id = backend_contract_persistent_session.session_id

    async def consume_turn() -> None:
        await _act(backend_contract_persistent_session, "Second", "user-2")

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await backend_contract_persistent_session.interrupt()
        await turn
    finally:
        release.set()
        if not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn

    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=True,
    )

    assert result.message == "First"
    assert backend_contract_persistent_session.session_id == session_id
    assert _texts(backend_contract_persistent_session) == []


@pytest.mark.asyncio
async def test_rewind_rejects_an_entry_that_is_not_a_rewindable_message(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    await _act(backend_contract_persistent_session, "First", "user-1")

    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_persistent_session.resources.sessions.rewind(
            "missing-entry", restore_files=False, inplace=True
        )

    assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
    assert _texts(backend_contract_persistent_session) == ["First", "First answer"]


@pytest.mark.asyncio
async def test_fork_rewind_attaches_a_truncated_child_and_keeps_the_source_whole(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    experimental_harness: bool,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")
    source_session_id = backend_contract_persistent_session.session_id
    entry_id = _user_entry_id(backend_contract_persistent_session, "Second")

    result = await backend_contract_persistent_session.resources.sessions.rewind(
        entry_id, restore_files=False, inplace=False
    )

    assert result.message == "Second"
    assert backend_contract_persistent_session.session_id != source_session_id
    assert result.state.session.id == backend_contract_persistent_session.session_id
    assert result.state.session.parent_session_id == source_session_id
    assert _texts(backend_contract_persistent_session) == ["First", "First answer"]

    # The source is no longer attached, so it is read the way a session picker
    # would read it: from storage, once this connection has let go of it.
    await backend_contract_persistent_session.close()
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    try:
        source = await connection.host.read_session(source_session_id)
    finally:
        await connection.host.close()

    assert [
        entry.text
        for entry in source.history or []
        if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer", "Second", "Second answer"]


@pytest.mark.asyncio
async def test_a_forked_child_can_be_rewound_in_place_onto_an_inherited_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # The turns a fork inherits are republished under the child's own entry ids,
    # and those ids are all a second rewind has to work with. The anchor has to
    # resolve against history the child was handed rather than history it
    # recorded itself.
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_mistral_response("Third answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")
    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=False,
    )
    child_session_id = backend_contract_persistent_session.session_id

    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=True,
    )
    await _act(backend_contract_persistent_session, "Third", "user-3")

    assert result.message == "First"
    assert backend_contract_persistent_session.session_id == child_session_id
    assert _texts(backend_contract_persistent_session) == ["Third", "Third answer"]


@pytest.mark.asyncio
async def test_a_forked_child_can_be_forked_again_onto_an_inherited_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_mistral_response("Third answer"),
        ]
    )
    await _act(backend_contract_persistent_session, "First", "user-1")
    await _act(backend_contract_persistent_session, "Second", "user-2")
    await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "Second"),
        restore_files=False,
        inplace=False,
    )
    child_session_id = backend_contract_persistent_session.session_id

    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=False,
    )
    await _act(backend_contract_persistent_session, "Third", "user-3")

    assert result.message == "First"
    assert backend_contract_persistent_session.session_id != child_session_id
    assert result.state.session.parent_session_id == child_session_id
    assert _texts(backend_contract_persistent_session) == ["Third", "Third answer"]


@pytest.mark.asyncio
async def test_fork_rewind_of_the_only_message_yields_an_empty_child(
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
    await _act(backend_contract_persistent_session, "First", "user-1")
    source_session_id = backend_contract_persistent_session.session_id

    result = await backend_contract_persistent_session.resources.sessions.rewind(
        _user_entry_id(backend_contract_persistent_session, "First"),
        restore_files=False,
        inplace=False,
    )
    await _act(backend_contract_persistent_session, "Second", "user-2")

    assert result.message == "First"
    assert backend_contract_persistent_session.session_id != source_session_id
    assert _texts(backend_contract_persistent_session) == ["Second", "Second answer"]
