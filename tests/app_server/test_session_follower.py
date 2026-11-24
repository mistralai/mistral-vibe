from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from mistralai_vibe_local_harness.session_protocol import (
    JsonObject,
    PublicSessionState,
    SessionReadParams,
    SessionReadResult,
    SessionSnapshot,
)
from vibe.app_server import _session_follower as follower_module
from vibe.app_server._session_follower import (
    EndedTurn,
    EntryLedger,
    RuntimeSessionFeed,
    SessionFollower,
    SessionFollowerConsumer,
)
from vibe.app_server.models import PublicHistoryEntry, PublicTurn, PublicTurnStatus

_SESSION = "session-1"


class _Recorder(SessionFollowerConsumer):
    def __init__(self) -> None:
        self.entries: list[PublicHistoryEntry] = []
        self.turn_ends: list[EndedTurn] = []
        self.resets = 0

    def entry_reported(self, entry: PublicHistoryEntry) -> None:
        self.entries.append(entry)

    def turn_ended(self, ended: EndedTurn) -> None:
        self.turn_ends.append(ended)

    def history_reset(self) -> None:
        self.resets += 1


class _FakeRuntimeSession:
    """The part of a Runtime session the direct feed reads, with no Runtime."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self.observer: Callable[[JsonObject], None] | None = None
        self.places_notices: bool | None = None

    @property
    def session_id(self) -> str:
        return _SESSION

    async def read(self, params: SessionReadParams) -> SessionReadResult:
        return SessionReadResult(
            snapshot=SessionSnapshot(
                state=PublicSessionState.model_validate(self._state),
                history_limit=params.history_limit,
                watermark=0,
            )
        )

    def observe_events(
        self,
        observer_id: str,
        observer: Callable[[JsonObject], None],
        *,
        places_notices: bool = True,
    ) -> object:
        self.observer = observer
        self.places_notices = places_notices
        return None

    def stop_observing_events(self, observer_id: str) -> None:
        self.observer = None

    def publish(self, state: dict[str, Any]) -> None:
        assert self.observer is not None
        self.observer({
            "type": "session_state_updated",
            "sessionId": _SESSION,
            "state": state,
        })


def _message(entry_id: str, text: str, *, completed: bool = True) -> JsonObject:
    return {
        "type": "message",
        "id": entry_id,
        "sessionId": _SESSION,
        "turnId": "turn-1",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed" if completed else "in_progress",
    }


def _turn(turn_id: str, status: str) -> JsonObject:
    turn: JsonObject = {
        "id": turn_id,
        "sessionId": _SESSION,
        "status": status,
        "startedAt": 1,
    }
    if status != "in_progress":
        turn["completedAt"] = 2
    return turn


def _state(entries: list[JsonObject], turn: JsonObject | None) -> dict[str, Any]:
    return {
        "session": {
            "id": _SESSION,
            "status": {"type": "idle"},
            "createdAt": 1,
            "updatedAt": 1,
        },
        "history": {"range": "latest", "entries": entries},
        "latestTurn": turn,
        "turnQueue": {"items": [], "paused": False, "maxItems": 10},
    }


def _public_turn(turn: Any) -> PublicTurn:
    return PublicTurn(
        id=turn.id,
        session_id=turn.session_id,
        status=PublicTurnStatus(str(turn.status)),
        started_at=turn.started_at,
    )


async def _followed(
    state: dict[str, Any],
) -> tuple[_FakeRuntimeSession, SessionFollower, _Recorder]:
    session = _FakeRuntimeSession(state)
    follower = SessionFollower(_SESSION)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(
        RuntimeSessionFeed(session, display_roots=lambda: (), public_turn=_public_turn)
    )
    return session, follower, recorder


@pytest.mark.asyncio
async def test_a_turn_a_newer_turn_replaces_in_the_same_update_is_reported_ended() -> (
    None
):
    """*Prepare*: A followed session whose Turn runs.
    *Do*: Publish one update where a queued Turn already replaced it.
    *Assert*: The replaced Turn is reported ended, without its final state; the
    new Turn is not.
    """
    # Prepare
    session, _follower, recorder = await _followed(
        _state([], _turn("turn-1", "in_progress"))
    )

    # Do
    session.publish(_state([], _turn("turn-2", "in_progress")))

    # Assert
    assert recorder.turn_ends == [EndedTurn(turn_id="turn-1", turn=None)]


@pytest.mark.asyncio
async def test_a_turn_end_is_reported_once_however_many_updates_show_it() -> None:
    """*Prepare*: A followed session whose Turn runs.
    *Do*: Publish its completed state three times.
    *Assert*: Its end is reported once, with its final state.
    """
    # Prepare
    session, _follower, recorder = await _followed(
        _state([], _turn("turn-1", "in_progress"))
    )

    # Do
    for _ in range(3):
        session.publish(_state([], _turn("turn-1", "completed")))

    # Assert
    assert [ended.turn_id for ended in recorder.turn_ends] == ["turn-1"]
    assert recorder.turn_ends[0].turn is not None
    assert recorder.turn_ends[0].turn.status is PublicTurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_turn_that_ended_before_the_follower_started_is_not_reported() -> None:
    """*Prepare*: A session whose latest Turn completed before it was followed.
    *Do*: Publish that state again.
    *Assert*: Nothing is reported.
    """
    # Prepare
    state = _state([_message("a-1", "done")], _turn("turn-1", "completed"))
    session, _follower, recorder = await _followed(state)

    # Do
    session.publish(_state([_message("a-1", "done")], _turn("turn-1", "completed")))

    # Assert
    assert recorder.turn_ends == []
    assert recorder.entries == []


@pytest.mark.asyncio
async def test_an_entry_is_reported_when_it_appears_and_when_it_completes() -> None:
    """*Prepare*: A followed session with no history.
    *Do*: Publish a streamed answer, a longer stream of it, then its completion.
    *Assert*: It is reported twice: in progress, then completed.
    """
    # Prepare
    session, _follower, recorder = await _followed(_state([], None))

    # Do
    session.publish(_state([_message("a-1", "Hi th", completed=False)], None))
    session.publish(_state([_message("a-1", "Hello", completed=False)], None))
    session.publish(_state([_message("a-1", "Hello")], None))

    # Assert
    assert [(entry.id, entry.generation_status) for entry in recorder.entries] == [
        ("a-1", "in_progress"),
        ("a-1", "completed"),
    ]


@pytest.mark.asyncio
async def test_removed_entries_reset_the_history_but_a_dropped_stream_does_not() -> (
    None
):
    """*Prepare*: A followed session with a completed answer.
    *Do*: Publish a streamed answer, drop it as a retry does, then remove the
    completed answer as a rewind does.
    *Assert*: Only the removal resets the history.
    """
    # Prepare
    session, _follower, recorder = await _followed(
        _state([_message("a-1", "kept")], None)
    )

    # Do
    session.publish(
        _state([_message("a-1", "kept"), _message("a-2", "dr", completed=False)], None)
    )
    session.publish(_state([_message("a-1", "kept")], None))
    resets_after_drop = recorder.resets
    session.publish(_state([], None))

    # Assert
    assert resets_after_drop == 0
    assert recorder.resets == 1


@pytest.mark.asyncio
async def test_the_follower_leaves_held_notices_and_stops_at_close() -> None:
    """*Prepare*: A followed session.
    *Do*: Close the follower, then publish an update.
    *Assert*: The feed asked to leave held notices alone, and reports nothing
    after the close.
    """
    # Prepare
    session, follower, recorder = await _followed(
        _state([], _turn("turn-1", "in_progress"))
    )
    observer = session.observer
    assert observer is not None

    # Do
    await follower.close()
    observer({
        "type": "session_state_updated",
        "sessionId": _SESSION,
        "state": _state([], _turn("turn-1", "completed")),
    })

    # Assert
    assert session.places_notices is False
    assert session.observer is None
    assert recorder.turn_ends == []


@pytest.mark.asyncio
async def test_a_rewind_back_to_an_ended_turn_reports_no_turn_end() -> None:
    """*Prepare*: A followed session with two ended Turns' answers.
    *Do*: Publish the update of a rewind that removes the second answer and
    leaves the first Turn, long ended, as the latest.
    *Assert*: The history is reset, and no Turn end is reported.
    """
    # Prepare
    session, _follower, recorder = await _followed(
        _state(
            [_message("a-1", "one"), _message("a-2", "two")],
            _turn("turn-2", "completed"),
        )
    )

    # Do
    session.publish(_state([_message("a-1", "one")], _turn("turn-1", "completed")))

    # Assert
    assert recorder.resets == 1
    assert recorder.turn_ends == []


class _BlockingReadSession(_FakeRuntimeSession):
    def __init__(self, state: dict[str, Any]) -> None:
        super().__init__(state)
        self.reading = asyncio.Event()
        self.release = asyncio.Event()
        self.failures = 0

    async def read(self, params: SessionReadParams) -> SessionReadResult:
        self.reading.set()
        await self.release.wait()
        if self.failures:
            self.failures -= 1
            raise RuntimeError("read failed")
        return await super().read(params)


@pytest.mark.asyncio
async def test_a_follower_closed_while_it_starts_attaches_nothing() -> None:
    """*Prepare*: A follower whose start waits on the Session's read.
    *Do*: Close it, then let the read answer.
    *Assert*: No observer is left on the Session.
    """
    # Prepare
    session = _BlockingReadSession(_state([], None))
    follower = SessionFollower(_SESSION)
    starting = asyncio.create_task(
        follower.start(
            RuntimeSessionFeed(
                session, display_roots=lambda: (), public_turn=_public_turn
            )
        )
    )
    await session.reading.wait()

    # Do
    await follower.close()
    session.release.set()
    await starting

    # Assert
    assert session.observer is None


@pytest.mark.asyncio
async def test_a_turn_that_ends_while_the_start_is_retried_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A Session whose Turn runs, and whose first read fails.
    *Do*: Start the follower; the Turn ends before the retry reads the Session.
    *Assert*: The start returns at once, and the retry reports the Turn's end.
    """
    # Prepare
    monkeypatch.setattr(follower_module, "_START_RETRY_SECONDS", 0.01)
    session = _BlockingReadSession(_state([], _turn("turn-1", "in_progress")))
    session.failures = 1
    session.release.set()
    follower = SessionFollower(_SESSION)
    recorder = _Recorder()
    follower.add_consumer(recorder)

    # Do
    await follower.start(
        RuntimeSessionFeed(session, display_roots=lambda: (), public_turn=_public_turn)
    )
    attached_at_once = session.observer is not None
    session._state = _state([], _turn("turn-1", "completed"))
    async with asyncio.timeout(5):
        while session.observer is None:
            await asyncio.sleep(0.01)
    session.publish(_state([], _turn("turn-1", "completed")))
    await follower.close()

    # Assert
    assert not attached_at_once
    assert [ended.turn_id for ended in recorder.turn_ends] == ["turn-1"]
    assert recorder.turn_ends[0].turn is not None


@pytest.mark.asyncio
async def test_a_rewind_while_the_start_is_retried_resets_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A Session whose first read fails.
    *Do*: Start the follower, rewind the Session before the retry reads it.
    *Assert*: Once the retry succeeds, consumers get one history reset.
    """
    # Prepare
    monkeypatch.setattr(follower_module, "_START_RETRY_SECONDS", 0.01)
    session = _BlockingReadSession(_state([_message("m1", "Hi there")], None))
    session.failures = 1
    session.release.set()
    follower = SessionFollower(_SESSION)
    recorder = _Recorder()
    follower.add_consumer(recorder)

    # Do
    await follower.start(
        RuntimeSessionFeed(session, display_roots=lambda: (), public_turn=_public_turn)
    )
    resets_before_retry = recorder.resets
    await follower.history_rewound()
    session._state = _state([], None)
    async with asyncio.timeout(5):
        while session.observer is None or recorder.resets == 0:
            await asyncio.sleep(0.01)
    await follower.close()

    # Assert
    assert resets_before_retry == 0
    assert recorder.resets == 1


def test_a_replayed_in_progress_update_does_not_make_a_completion_new() -> None:
    """*Prepare*: A ledger that took over a completed entry from another one.
    *Do*: Replay the entry's in-progress update, then its completion.
    *Assert*: Neither counts as a change.
    """
    # Prepare
    reported = EntryLedger()
    reported.changed("m1", completed=True)
    ledger = EntryLedger()
    ledger.absorb(reported)

    # Do
    replayed_add = ledger.changed("m1", completed=False)
    replayed_completion = ledger.changed("m1", completed=True)

    # Assert
    assert not replayed_add
    assert not replayed_completion
