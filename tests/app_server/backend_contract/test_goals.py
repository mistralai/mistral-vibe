from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._goal_notice_store import read_stored_goal_notices
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.models import (
    GoalNoticeDetail,
    GoalStatus,
    GoalStatusReason,
    PublicMessageEntry,
    PublicNoticeEntry,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession


async def _wait_for_goal(session: AppServerSession, *, present: bool) -> None:
    async with asyncio.timeout(3):
        while (session.state.session.goal is not None) != present:
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_goal_resource_supports_set_edit_and_clear(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: An attached session with no goal.
    *Do*: Set, edit, pause and clear the goal through the public resource.
    *Assert*: Each mutation returns the authoritative goal, and the session
    state follows it without a re-read.
    """
    # Prepare
    resource = backend_contract_session.resources.goal
    assert await resource.get() is None

    # Do
    created = await resource.set(objective="Make CI green", token_budget=50_000)
    await _wait_for_goal(backend_contract_session, present=True)
    live = backend_contract_session.state.session.goal
    edited = await resource.set(
        objective="Make CI green on Linux", expected_revision=created.revision
    )
    paused = await resource.set(status="paused")
    cleared = await resource.clear(paused.goal_id)
    await _wait_for_goal(backend_contract_session, present=False)

    # Assert
    assert created.status is GoalStatus.ACTIVE
    assert live == created
    assert edited.goal_id == created.goal_id
    assert edited.token_budget == 50_000
    assert edited.objective_revision == created.objective_revision + 1
    assert paused.status is GoalStatus.PAUSED
    assert cleared is True
    assert await resource.get() is None


@pytest.mark.asyncio
async def test_a_stale_goal_edit_is_rejected_with_conflict(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: A goal edited once since the client read it.
    *Do*: Send an edit expecting the old revision.
    *Assert*: The server rejects it with a conflict and keeps the newer goal.
    """
    # Prepare
    resource = backend_contract_session.resources.goal
    created = await resource.set(objective="First")
    newer = await resource.set(objective="Second")

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await resource.set(objective="Stale", expected_revision=created.revision)

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    assert await resource.get() == newer


@pytest.mark.asyncio
async def test_an_edit_or_clear_of_a_replaced_goal_is_rejected_with_conflict(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: Goal A, replaced by goal B from another client; both start at
    revision 1.
    *Do*: Edit and clear expecting A's id and revision, as A's open edit form
    and clear confirmation do.
    *Assert*: Both are conflicts, and B is untouched.
    """
    # Prepare
    resource = backend_contract_session.resources.goal
    first = await resource.set(objective="Goal A")
    second = await resource.set(
        objective="Goal B", replace=True, expected_goal_id=first.goal_id
    )
    assert second.revision == first.revision

    # Do
    with pytest.raises(AppServerResponseError) as edit_error:
        await resource.set(
            objective="Goal A, edited",
            expected_goal_id=first.goal_id,
            expected_revision=first.revision,
        )
    with pytest.raises(AppServerResponseError) as clear_error:
        await resource.clear(first.goal_id)

    # Assert
    assert edit_error.value.error.code is ProtocolErrorCode.CONFLICT
    assert clear_error.value.error.code is ProtocolErrorCode.CONFLICT
    assert await resource.get() == second


@pytest.mark.asyncio
async def test_an_edit_of_a_cleared_goal_is_rejected_with_conflict(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: A goal the client read, then cleared elsewhere.
    *Do*: Save the client's edit, which expects that goal's revision.
    *Assert*: A conflict, and no goal was created.
    """
    # Prepare
    resource = backend_contract_session.resources.goal
    created = await resource.set(objective="Goal")
    await resource.clear(created.goal_id)

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await resource.set(
            objective="Edited",
            expected_goal_id=created.goal_id,
            expected_revision=created.revision,
        )

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    assert await resource.get() is None


@pytest.mark.asyncio
async def test_an_oversized_objective_is_rejected(
    backend_contract_session: AppServerSession,
) -> None:
    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_session.resources.goal.set(objective="x" * 4001)

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_an_active_goal_comes_back_paused_after_resume(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A persisted session with an active goal, closed.
    *Do*: Resume it from a fresh Host, and list sessions.
    *Assert*: The goal is restored paused (suspended), and the catalogue row
    reports it.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    session = await backend_contract_persistent_connection.host.open_session()
    _ = [event async for event in session.act("persist the session")]
    created = await session.resources.goal.set(objective="Keep going")
    session_id = session.session_id
    await session.close()
    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )

    # Do
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        restored = await resumed.resources.goal.get()
        state_goal = resumed.state.session.goal
    finally:
        await resumed.close()
    passive_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        listed = await passive_connection.host.list_sessions()
    finally:
        await passive_connection.host.close()

    # Assert
    assert restored is not None
    assert restored.goal_id == created.goal_id
    assert restored.status is GoalStatus.PAUSED
    assert restored.status_reason is GoalStatusReason.SUSPENDED
    assert state_goal == restored
    row = next(item for item in listed if item.id == session_id)
    assert row.goal is not None
    assert row.goal.goal_id == created.goal_id


@pytest.mark.asyncio
async def test_clear_history_starts_without_a_goal(
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # Prepare
    await backend_contract_persistent_session.resources.goal.set(objective="Goal")

    # Do
    await backend_contract_persistent_session.clear_history()

    # Assert
    assert await backend_contract_persistent_session.resources.goal.get() is None
    assert backend_contract_persistent_session.state.session.goal is None


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_fork_starts_without_a_goal(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in backend_contract_persistent_session.act("persist")]
    await backend_contract_persistent_session.resources.goal.set(objective="Goal")

    # Do
    fork = await backend_contract_persistent_session.resources.sessions.fork()

    # Assert: the session now runs the fork, which has no goal.
    assert fork.source_session_id != fork.state.session.id
    assert fork.state.session.goal is None
    assert await backend_contract_persistent_session.resources.goal.get() is None


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_rewind_keeps_the_goal_and_its_counters(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A session with a goal and two turns, the goal set after both.
    *Do*: Rewind in place to the second turn.
    *Assert*: The session still has the same goal, and the transcript no longer
    shows the notice recorded after the rewound turn.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("answer")
    )
    session = backend_contract_persistent_session
    _ = [event async for event in session.act("First", client_message_id="u-1")]
    _ = [event async for event in session.act("Second", client_message_id="u-2")]
    created = await session.resources.goal.set(objective="Goal")
    second = next(
        entry.id
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.text == "Second"
    )

    # Do
    await session.resources.sessions.rewind(second, restore_files=False, inplace=True)
    after = await session.resources.goal.get()

    # Assert
    assert after is not None
    assert after.goal_id == created.goal_id
    assert after.tokens_used == created.tokens_used
    assert _goal_notices(session) == []


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_fork_rewind_carries_no_goal_notice_it_cannot_place(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A goal set between two turns, which records a notice.
    *Do*: Rewind into a fork at the second turn.
    *Assert*: The fork keeps the goal, but its history starts with the first
    message: the copied entries have no turn ids or times to place the
    source's notices against.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("answer")
    )
    session = backend_contract_persistent_session
    _ = [event async for event in session.act("First", client_message_id="u-1")]
    created = await session.resources.goal.set(objective="Goal", status="paused")
    _ = [event async for event in session.act("Second", client_message_id="u-2")]
    second = next(
        entry.id
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.text == "Second"
    )

    # Do
    await session.resources.sessions.rewind(second, restore_files=False, inplace=False)
    after = await session.resources.goal.get()

    # Assert
    assert after is not None
    assert after.goal_id == created.goal_id
    assert _goal_notices(session) == []
    first = session.history[0]
    assert isinstance(first, PublicMessageEntry)
    assert first.text == "First"


def _tool_call(
    call_id: str, name: str, arguments: dict[str, object]
) -> dict[str, object]:
    return {
        "id": call_id,
        "index": 0,
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _notices(session: AppServerSession, event: str) -> list[PublicNoticeEntry]:
    return [
        entry
        for entry in session.history
        if isinstance(entry, PublicNoticeEntry)
        and isinstance(entry.detail, GoalNoticeDetail)
        and entry.detail.event == event
    ]


async def _wait_for_status(
    session: AppServerSession, status: GoalStatus, *, timeout: float = 10
) -> None:
    async with asyncio.timeout(timeout):
        while (
            session.state.session.goal is None
            or session.state.session.goal.status is not status
        ):
            await asyncio.sleep(0.02)


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_continuations_run_until_the_model_completes_the_goal(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A session whose model works for two turns, then completes.
    *Do*: Set a goal and let the session run on its own.
    *Assert*: The goal completes after continuation turns and then leaves the
    session; the completion notice carries the final counts and its turn, no
    user bubble appears, and the model saw the hidden continuation message.
    """
    # Prepare
    session = backend_contract_persistent_session
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Working on step one."),
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call("goal-1", "update_goal", {"status": "complete"})
                ],
            ),
            backend_contract_mistral_response("Done: every requirement is met."),
        ]
    )
    user_messages_before = sum(
        isinstance(entry, PublicMessageEntry) and entry.role == "user"
        for entry in session.history
    )

    # Do
    await session.resources.goal.set(objective="Finish the two steps")
    await _wait_for_status(session, GoalStatus.COMPLETE)
    # The goal's removal and its completion notice arrive as two events.
    async with asyncio.timeout(10):
        while session.state.session.goal is not None or not _notices(
            session, "completed"
        ):
            await asyncio.sleep(0.02)

    # Assert
    [completed] = _notices(session, "completed")
    assert isinstance(completed.detail, GoalNoticeDetail)
    assert completed.detail.status_reason is GoalStatusReason.MODEL
    assert completed.detail.tokens_used > 0
    assert completed.detail.objective == "Finish the two steps"
    assert completed.message == "Goal complete: Finish the two steps"
    assert completed.turn_id is not None
    assert await session.resources.goal.get() is None
    user_messages_after = sum(
        isinstance(entry, PublicMessageEntry) and entry.role == "user"
        for entry in session.history
    )
    assert user_messages_after == user_messages_before
    sent = json.dumps(
        json.loads(backend_contract_mistral_api.calls[-1].request.content)
    )
    assert "Continue working toward the session goal" in sent


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_interrupting_a_goal_turn_pauses_the_goal(
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: An active goal whose continuation turn is in flight.
    *Do*: Interrupt the turn, as Esc does.
    *Assert*: The goal is paused with the interrupted reason, and no new
    continuation starts.
    """
    # Prepare
    session = backend_contract_persistent_session
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "never", started=started, release=release
        )
    )
    await session.resources.goal.set(objective="Long work")
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        # The client interrupts the turn it sees, as a user's Esc does.
        async with asyncio.timeout(5):
            while (
                session.state.latest_turn is None
                or session.state.latest_turn.status != "in_progress"
            ):
                await asyncio.sleep(0.02)

        # Do
        await session.interrupt()
        await _wait_for_status(session, GoalStatus.PAUSED)
    finally:
        release.set()
    calls = len(backend_contract_mistral_api.calls)
    await asyncio.sleep(1.5)

    # Assert
    goal = session.state.session.goal
    assert goal is not None
    assert goal.status_reason is GoalStatusReason.INTERRUPTED
    assert len(backend_contract_mistral_api.calls) == calls


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_goal_stops_at_its_token_budget(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A goal whose budget is smaller than one model call.
    *Do*: Let it run.
    *Assert*: After one turn it is budget limited, with its usage recorded.
    """
    # Prepare
    session = backend_contract_persistent_session
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=lambda _request: backend_contract_mistral_response("Some progress.")
    )

    # Do
    await session.resources.goal.set(objective="Big work", token_budget=10)
    await _wait_for_status(session, GoalStatus.BUDGET_LIMITED)

    # Assert
    goal = session.state.session.goal
    assert goal is not None
    assert goal.tokens_used >= 10


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_paused_goal_starts_no_continuation(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    # Prepare
    session = backend_contract_persistent_session
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=lambda _request: backend_contract_mistral_response("noted")
    )
    calls = len(backend_contract_mistral_api.calls)

    # Do
    await session.resources.goal.set(objective="Later", status="paused")
    await asyncio.sleep(1.5)

    # Assert
    assert len(backend_contract_mistral_api.calls) == calls


def _goal_notices(session: AppServerSession) -> list[tuple[str, str]]:
    return [
        (entry.detail.event, entry.message)
        for entry in session.history
        if isinstance(entry, PublicNoticeEntry)
        and isinstance(entry.detail, GoalNoticeDetail)
    ]


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_the_transcript_shows_goal_actions_and_continuations(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
    tmp_path: Path,
) -> None:
    """*Prepare*: A session whose model works one turn, then completes the goal.
    *Do*: Set a goal, let it run to completion, then resume the session.
    *Assert*: The transcript records the set, each continuation and the
    completion, in order; the resumed session shows the same notices, which
    the app server keeps beside the session.
    """
    # Prepare
    session = await backend_contract_persistent_connection.host.open_session()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Step one."),
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call("goal-1", "update_goal", {"status": "complete"})
                ],
            ),
            backend_contract_mistral_response("Done."),
        ]
    )

    # Do
    try:
        await session.resources.goal.set(objective="Two steps")
        await _wait_for_status(session, GoalStatus.COMPLETE)
        async with asyncio.timeout(5):
            while not any(event == "completed" for event, _ in _goal_notices(session)):
                await asyncio.sleep(0.02)
        live = _goal_notices(session)
        session_id = session.session_id
    finally:
        await session.close()
    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        stored = _goal_notices(resumed)
    finally:
        await resumed.close()

    # Assert
    assert [event for event, _ in live] == [
        "set",
        "continued",
        "continued",
        "completed",
    ]
    assert live[0] == ("set", "Goal set: Two steps")
    assert stored == live
    session_dir = tmp_path / "sessions" / "unified" / session_id
    assert len(read_stored_goal_notices(session_dir)) == len(live)


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_goal_the_model_creates_shows_and_is_charged_its_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A model that creates a goal, then completes it in the same turn.
    *Do*: Send the user turn that asks for it.
    *Assert*: The transcript shows the goal set, then its completion with the
    tokens of the turn that created it, and the goal leaves the session.
    """
    # Prepare
    session = backend_contract_persistent_session
    # A first turn takes the session's title request, so it never consumes one
    # of the scripted answers below.
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call("goal-1", "create_goal", {"objective": "Write a.txt"})
                ],
            ),
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call("goal-2", "update_goal", {"status": "complete"})
                ],
            ),
            backend_contract_mistral_response("Done."),
        ]
    )

    # Do
    _ = [event async for event in session.act("Set yourself a goal: write a.txt")]
    async with asyncio.timeout(10):
        while not any(event == "completed" for event, _ in _goal_notices(session)):
            await asyncio.sleep(0.02)

    # Assert
    assert [event for event, _ in _goal_notices(session)] == ["set", "completed"]
    [completed] = [
        entry.detail
        for entry in session.history
        if isinstance(entry, PublicNoticeEntry)
        and isinstance(entry.detail, GoalNoticeDetail)
        and entry.detail.event == "completed"
    ]
    assert isinstance(completed, GoalNoticeDetail)
    assert completed.tokens_used > 0
    await _wait_for_goal(session, present=False)


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_failed_goal_turn_blocks_the_goal_and_starts_no_continuation(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    """*Prepare*: A model that answers one continuation, then rejects the next
    request with a 400 that would fail again.
    *Do*: Set a goal and let it run.
    *Assert*: The goal is blocked by the failed turn, no continuation starts
    after it, no "Working on the goal" notice follows the stop, and the
    "Goal blocked" notice quotes the error and points away from resuming.
    """
    # Prepare
    session = backend_contract_persistent_session
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )
    _ = [event async for event in session.act("persist")]
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Step one."),
            httpx.Response(
                400, json={"message": "image dimensions exceed max allowed size"}
            ),
            backend_contract_mistral_response("Should never be asked."),
        ]
    )

    # Do
    await session.resources.goal.set(objective="Two steps")
    await _wait_for_status(session, GoalStatus.BLOCKED)
    async with asyncio.timeout(5):
        while not any(event == "blocked" for event, _ in _goal_notices(session)):
            await asyncio.sleep(0.02)
    calls = len(backend_contract_mistral_api.calls)
    await asyncio.sleep(1.5)

    # Assert
    goal = session.state.session.goal
    assert goal is not None
    assert goal.status_reason is GoalStatusReason.TURN_FAILED
    assert len(backend_contract_mistral_api.calls) == calls
    events = [event for event, _ in _goal_notices(session)]
    assert events == ["set", "continued", "continued", "blocked"]
    [blocked] = [
        entry
        for entry in session.history
        if isinstance(entry, PublicNoticeEntry)
        and isinstance(entry.detail, GoalNoticeDetail)
        and entry.detail.event == "blocked"
    ]
    assert isinstance(blocked.detail, GoalNoticeDetail)
    assert blocked.detail.error is not None
    assert "image dimensions exceed" in blocked.message
    assert "/rewind" in blocked.message
    assert "Two steps" not in blocked.message
