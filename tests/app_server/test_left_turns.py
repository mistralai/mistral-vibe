from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vibe.app_server import _left_turns
from vibe.app_server._left_turns import (
    AccountLender,
    LeftTurn,
    LeftTurns,
    LeftTurnsRead,
    LeftTurnsStopped,
    SignsInItself,
    StoppedLeftTurn,
    close_left_turns_first,
)
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._platform_credential_lenders import PlatformCredentialLenders
from vibe.app_server._platform_credentials import ConnectionPlatformCredentialProvider
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)

OWNER = ("desktop", "org", "ws", "alice")
BOB = ("desktop", "org", "ws", "bob")


class _Sessions:
    """What the process holds: a parked token per session, as a resume left it."""

    def __init__(self) -> None:
        self.tokens: dict[str, object] = {}
        self.held: set[str] = set()
        self.resuming_ids: list[str] = []

    def parked(self, session_id: str) -> object | None:
        return self.tokens.get(session_id)

    def held_open(self, session_id: str) -> bool:
        return session_id in self.held

    def resuming(self, session_id: str) -> bool:
        return session_id in self.resuming_ids

    async def begin_resume(self, session_id: str) -> asyncio.Future[None]:
        self.resuming_ids.append(session_id)
        return asyncio.get_running_loop().create_future()

    def end_resume(self, session_id: str, claim: asyncio.Future[None]) -> None:
        self.resuming_ids.remove(session_id)


class _Resumes:
    """Resumes that run until released. A plain resume parks its session; an
    interrupt ends it, unless told to leave it running.
    """

    def __init__(self, sessions: _Sessions) -> None:
        self.sessions = sessions
        self.calls: list[tuple[str, bool]] = []
        self.release: dict[str, asyncio.Event] = {}
        self.running = 0
        self.peak = 0
        self.fail: set[str] = set()
        self.hang: set[str] = set()
        self.keep_running: set[str] = set()

    async def __call__(self, session_id: str, interrupt: bool) -> None:
        self.calls.append((session_id, interrupt))
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if session_id in self.hang:
                await asyncio.Event().wait()
            if session_id in self.fail:
                raise RuntimeError("replay failed")
            if not interrupt:
                await self.release.setdefault(session_id, asyncio.Event()).wait()
                self.sessions.tokens[session_id] = object()
            elif session_id in self.keep_running:
                self.sessions.tokens[session_id] = object()
            else:
                self.sessions.tokens.pop(session_id, None)
        finally:
            self.running -= 1


_ACCOUNT = ("org", "ws", "alice")


def _lender() -> ConnectionPlatformCredentialProvider:
    return ConnectionPlatformCredentialProvider(
        AsyncMock(), tenants=AsyncMock(), kind="oidc_id_token"
    )


_durable = SignsInItself()


async def _listed(*ids: str) -> list[str]:
    return list(ids)


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _start(*ids: str) -> tuple[LeftTurns, _Sessions, _Resumes]:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    turns.resume_once(OWNER, lambda: _listed(*ids), resumes, sessions, lender=_durable)
    return turns, sessions, resumes


async def _until_listed(turns: LeftTurns) -> None:
    async with asyncio.timeout(2):
        while (await turns.read(OWNER)).status == "listing":
            await asyncio.sleep(0.01)


def _release(resumes: _Resumes, *ids: str) -> None:
    for session_id in ids:
        resumes.release.setdefault(session_id, asyncio.Event()).set()


@pytest.mark.asyncio
async def test_at_most_four_left_turns_resume_at_once() -> None:
    turns, _sessions, resumes = _start(*(f"s{i}" for i in range(6)))
    await _settle()

    read = await turns.read(OWNER)

    assert resumes.peak == 4
    assert (
        sorted(turn.state for turn in read.turns) == ["queued"] * 2 + ["resuming"] * 4
    )
    _release(resumes, *(f"s{i}" for i in range(6)))
    await turns.close()


@pytest.mark.asyncio
async def test_a_left_turn_is_resumed_once_per_owner_per_process() -> None:
    turns, sessions, resumes = _start("a")
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    _release(resumes, "a")
    await _settle()

    assert resumes.calls == [("a", False)]
    await turns.close()


@pytest.mark.asyncio
async def test_a_listing_that_fails_reads_so_and_is_tried_again() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)

    async def failing() -> list[str]:
        raise RuntimeError("host closed")

    turns.resume_once(OWNER, failing, resumes, sessions, lender=_durable)
    await _settle()
    failed = (await turns.read(OWNER)).status
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    _release(resumes, "a")
    await _settle()

    assert failed == "listing_failed"
    assert resumes.calls == [("a", False)]
    await turns.close()


@pytest.mark.asyncio
async def test_an_owner_nothing_resumes_for_reads_and_stops_as_not_resuming() -> None:
    turns = LeftTurns()

    stopped = await turns.interrupt(OWNER)

    assert (await turns.read(OWNER)).status == "not_resuming"
    assert stopped.status == "not_resuming"


@pytest.mark.asyncio
async def test_a_stop_whose_listing_hangs_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.1)
    turns, sessions = LeftTurns(), _Sessions()

    async def hanging() -> list[str]:
        await asyncio.Event().wait()
        return []

    turns.resume_once(OWNER, hanging, _Resumes(sessions), sessions, lender=_durable)

    stopped = await asyncio.wait_for(turns.interrupt(OWNER), 2)

    assert (stopped.status, stopped.turns) == ("listing_timed_out", [])
    await turns.close()


@pytest.mark.asyncio
async def test_read_says_whether_the_listing_is_still_running() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    listed = asyncio.Event()

    async def slow() -> list[str]:
        await listed.wait()
        return []

    turns.resume_once(OWNER, slow, _Resumes(sessions), sessions, lender=_durable)
    await _settle()
    listing = (await turns.read(OWNER)).status
    listed.set()
    await _settle()

    assert (listing, (await turns.read(OWNER)).status) == ("listing", "listed")
    await turns.close()


@pytest.mark.asyncio
async def test_a_resumed_turn_reads_as_running_while_it_runs_with_no_client() -> None:
    turns, sessions, resumes = _start("a", "b")
    _release(resumes, "a", "b")
    await _settle()
    del sessions.tokens["b"]

    read = await turns.read(OWNER)

    assert [(turn.session_id, turn.state) for turn in read.turns] == [("a", "running")]
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_a_client_took_since_is_neither_read_nor_stopped() -> None:
    turns, sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()
    sessions.tokens.pop("a")
    sessions.held.add("a")

    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert read.turns == []
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "taken_by_client")
    ]
    assert resumes.calls == [("a", False)]
    await turns.close()


@pytest.mark.asyncio
async def test_another_owner_reads_and_stops_nothing() -> None:
    turns, _sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()

    assert (await turns.read(BOB)).turns == []
    assert (await turns.interrupt(BOB)).turns == []
    await turns.close()


@pytest.mark.asyncio
async def test_stopping_ends_queued_turns_without_running_them_and_interrupts_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, _sessions, resumes = _start("a", "b")
    await _settle()

    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await _settle()
    _release(resumes, "a")
    stopped = (await asyncio.wait_for(stopping, 1)).turns

    assert ("b", True) in resumes.calls
    assert ("b", False) not in resumes.calls
    assert sorted((item.session_id, item.outcome) for item in stopped) == [
        ("a", "interrupted"),
        ("b", "ended_before_running"),
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_queued_turn_still_running_after_its_interrupt_is_reported_and_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, _sessions, resumes = _start("a", "b")
    resumes.keep_running.add("b")
    await _settle()

    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await _settle()
    _release(resumes, "a")
    stopped = (await asyncio.wait_for(stopping, 1)).turns

    assert resumes.calls.count(("b", True)) == 2
    assert sorted((item.session_id, item.outcome) for item in stopped) == [
        ("a", "interrupted"),
        ("b", "still_running"),
    ]
    assert [
        (turn.session_id, turn.state) for turn in (await turns.read(OWNER)).turns
    ] == [("b", "running")]
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_that_runs_on_after_its_interrupt_is_reported_and_kept() -> None:
    turns, _sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()
    resumes.keep_running.add("a")

    stopped = (await turns.interrupt(OWNER)).turns

    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "still_running")
    ]
    assert [turn.state for turn in (await turns.read(OWNER)).turns] == ["running"]
    await turns.close()


@pytest.mark.asyncio
async def test_a_resume_that_fails_reads_as_failed() -> None:
    turns, _sessions, resumes = _start("a")
    resumes.fail.add("a")
    await _settle()

    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert [(turn.session_id, turn.state) for turn in read.turns] == [("a", "failed")]
    assert [(item.session_id, item.outcome) for item in stopped] == [("a", "failed")]
    await turns.close()


@pytest.mark.asyncio
async def test_a_hung_resume_gives_up_so_a_stop_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.1)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    resumes.hang.add("a")
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)

    stopped = (await asyncio.wait_for(turns.interrupt(OWNER), 2)).turns

    assert [(item.session_id, item.outcome) for item in stopped] == [("a", "failed")]
    assert sessions.resuming_ids == []
    await turns.close()


@pytest.mark.asyncio
async def test_a_resume_s_wait_for_its_session_counts_against_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.4)
    sessions = _Sessions()
    claim = sessions.begin_resume

    async def slow_claim(session_id: str) -> asyncio.Future[None]:
        await asyncio.sleep(0.3)
        return await claim(session_id)

    sessions.begin_resume = slow_claim  # type: ignore[method-assign]
    turns, resumes = LeftTurns(), _Resumes(sessions)
    resumes.hang.add("a")
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    loop = asyncio.get_running_loop()
    started = loop.time()

    stopped = (await asyncio.wait_for(turns.interrupt(OWNER), 2)).turns

    assert loop.time() - started < 0.6
    assert [(item.session_id, item.outcome) for item in stopped] == [("a", "failed")]
    await turns.close()


@pytest.mark.asyncio
async def test_two_stops_at_once_interrupt_each_turn_once() -> None:
    turns, _sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()

    first, second = await asyncio.gather(turns.interrupt(OWNER), turns.interrupt(OWNER))

    assert [call for call in resumes.calls if call[1]] == [("a", True)]
    assert [item.outcome for item in first.turns + second.turns] == ["interrupted"]
    await turns.close()


class _Parked:
    def __init__(self) -> None:
        self.stops = 0

    def has_turn_work(self) -> bool:
        return True

    async def stop(self) -> None:
        self.stops += 1


@pytest.mark.asyncio
async def test_a_resume_cut_by_the_process_exit_parks_before_parked_sessions_stop() -> (
    None
):
    turns, open_sessions = LeftTurns(), OpenSessions()
    parked = _Parked()
    started = asyncio.Event()

    async def resume(session_id: str, interrupt: bool) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            open_sessions.park(session_id, parked)

    turns.resume_once(OWNER, lambda: _listed("a"), resume, _Sessions(), lender=_durable)
    await started.wait()

    await close_left_turns_first(turns, open_sessions)

    assert parked.stops == 1


@pytest.mark.asyncio
async def test_a_chat_opened_while_its_left_turn_is_queued_is_not_read_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions, resumes = _start("a", "b")
    await _settle()
    sessions.held.add("b")

    _release(resumes, "a")
    await _settle()
    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert ("b", False) not in resumes.calls
    assert [turn.session_id for turn in read.turns] == ["a"]
    assert sorted((item.session_id, item.outcome) for item in stopped) == [
        ("a", "interrupted"),
        ("b", "taken_by_client"),
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_racing_a_chat_attach_answers_taken_by_client() -> None:
    turns, sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()
    claiming = asyncio.Event()
    attach = asyncio.Event()

    async def claim_after_attach(session_id: str) -> asyncio.Future[None]:
        claiming.set()
        await attach.wait()
        return await _Sessions.begin_resume(sessions, session_id)

    sessions.begin_resume = claim_after_attach  # type: ignore[method-assign]
    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await claiming.wait()
    sessions.tokens.pop("a")
    sessions.held.add("a")
    attach.set()
    stopped = (await stopping).turns

    assert [call for call in resumes.calls if call[1]] == []
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "taken_by_client")
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_asked_before_a_failed_listing_holds_once_it_is_listed() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)

    async def failing() -> list[str]:
        raise RuntimeError("host closed")

    turns.resume_once(OWNER, failing, resumes, sessions, lender=_durable)
    await _settle()
    stopped = await turns.interrupt(OWNER)
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    await _settle()

    assert (stopped.status, stopped.turns) == ("listing_failed", [])
    assert resumes.calls == [("a", True)]
    await turns.close()


@pytest.mark.asyncio
async def test_a_left_turn_runs_as_soon_as_a_durable_lender_signs_in() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    lenders = PlatformCredentialLenders()
    turns.resume_once(
        OWNER,
        lambda: _listed("a"),
        resumes,
        sessions,
        lender=AccountLender(lenders, _ACCOUNT),
    )
    lenders.register(_ACCOUNT, _lender())
    await _settle()
    waiting = await turns.read(OWNER)

    lenders.register(_ACCOUNT, _lender(), durable=True)
    _release(resumes, "a")
    await _settle()

    assert [(turn.session_id, turn.state) for turn in waiting.turns] == [
        ("a", "waiting_for_lender")
    ]
    assert resumes.calls == [("a", False)]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_ends_a_turn_waiting_for_a_lender_without_one() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    lenders = PlatformCredentialLenders()
    turns.resume_once(
        OWNER,
        lambda: _listed("a"),
        resumes,
        sessions,
        lender=AccountLender(lenders, _ACCOUNT),
    )
    await _settle()

    stopped = (await asyncio.wait_for(turns.interrupt(OWNER), 1)).turns

    assert resumes.calls == [("a", True)]
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "ended_before_running")
    ]
    assert not lenders._waiters
    await turns.close()


@pytest.mark.asyncio
async def test_a_queued_turn_a_chat_opened_and_closed_still_reads_as_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions, resumes = _start("a", "b")
    await _settle()
    sessions.tokens["b"] = object()

    _release(resumes, "a")
    await _settle()
    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert ("b", False) not in resumes.calls
    assert sorted((turn.session_id, turn.state) for turn in read.turns) == [
        ("a", "running"),
        ("b", "running"),
    ]
    assert sorted((item.session_id, item.outcome) for item in stopped) == [
        ("a", "interrupted"),
        ("b", "interrupted"),
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_chat_opened_on_a_queued_turn_and_closed_while_it_runs_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions, resumes = _start("a", "b")
    await _settle()
    sessions.held.add("b")
    _release(resumes, "a")
    await _settle()

    sessions.held.discard("b")
    sessions.tokens["b"] = object()
    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert ("b", "running") in [(turn.session_id, turn.state) for turn in read.turns]
    assert ("b", "interrupted") in [(item.session_id, item.outcome) for item in stopped]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_pending_on_a_turn_a_chat_left_running_reports_it_as_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions, resumes = _start("a", "b")
    await _settle()
    sessions.tokens["b"] = object()

    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await _settle()
    _release(resumes, "a")
    stopped = (await asyncio.wait_for(stopping, 1)).turns

    assert ("b", "interrupted") in [(item.session_id, item.outcome) for item in stopped]
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_that_ends_while_its_stop_waits_is_not_opened_again() -> None:
    turns, sessions, resumes = _start("a")
    _release(resumes, "a")
    await _settle()
    claiming = asyncio.Event()
    ended = asyncio.Event()

    async def claim_after_the_turn_ends(session_id: str) -> asyncio.Future[None]:
        claiming.set()
        await ended.wait()
        return await _Sessions.begin_resume(sessions, session_id)

    sessions.begin_resume = claim_after_the_turn_ends  # type: ignore[method-assign]
    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await claiming.wait()
    sessions.tokens.pop("a")
    ended.set()
    stopped = (await stopping).turns

    assert [call for call in resumes.calls if call[1]] == []
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "already_ended")
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_taken_on_a_failed_listing_holds_for_its_relisting() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)

    async def failing() -> list[str]:
        raise RuntimeError("host closed")

    turns.resume_once(OWNER, failing, resumes, sessions, lender=_durable)
    await _settle()
    failed = turns._owners[OWNER]
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    failed.stop.set()
    await _settle()

    assert resumes.calls == [("a", True)]
    await turns.close()


@pytest.mark.asyncio
async def test_closing_while_a_turn_waits_for_a_lender_leaves_nothing_behind() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    lenders = PlatformCredentialLenders()
    turns.resume_once(
        OWNER,
        lambda: _listed("a"),
        _Resumes(sessions),
        sessions,
        lender=AccountLender(lenders, _ACCOUNT),
    )
    await _settle()

    await turns.close()
    await _settle()

    assert not lenders._waiters
    assert not turns._tasks


@pytest.mark.asyncio
async def test_a_turn_whose_lender_leaves_while_it_waits_for_a_slot_waits_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    lenders = PlatformCredentialLenders()
    durable = _lender()
    lenders.register(_ACCOUNT, durable, durable=True)
    turns.resume_once(
        OWNER,
        lambda: _listed("a", "b"),
        resumes,
        sessions,
        lender=AccountLender(lenders, _ACCOUNT),
    )
    await _settle()

    lenders.unregister(durable)
    _release(resumes, "a")
    await _settle()

    assert resumes.calls == [("a", False)]
    assert ("b", "waiting_for_lender") in [
        (turn.session_id, turn.state) for turn in (await turns.read(OWNER)).turns
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_resume_that_fails_after_its_turn_started_is_read_and_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.1)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)

    async def starts_then_hangs(session_id: str, interrupt: bool) -> None:
        if interrupt:
            await resumes(session_id, interrupt)
            return
        try:
            await asyncio.Event().wait()
        finally:
            # The channel closes on the timeout, which parks the running turn.
            sessions.tokens[session_id] = object()

    turns.resume_once(
        OWNER, lambda: _listed("a"), starts_then_hangs, sessions, lender=_durable
    )
    await asyncio.sleep(0.3)

    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert [(turn.session_id, turn.state) for turn in read.turns] == [("a", "running")]
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "interrupted")
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_whose_resume_fails_with_the_turn_running_reports_it_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.1)
    turns, sessions = LeftTurns(), _Sessions()

    async def parks_then_hangs(session_id: str, interrupt: bool) -> None:
        sessions.tokens[session_id] = object()
        await asyncio.Event().wait()

    turns.resume_once(
        OWNER, lambda: _listed("a"), parks_then_hangs, sessions, lender=_durable
    )
    stopped = (await asyncio.wait_for(turns.interrupt(OWNER), 2)).turns

    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "still_running")
    ]
    assert [turn.state for turn in (await turns.read(OWNER)).turns] == ["running"]
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_a_chat_held_while_its_resume_timed_out_is_left_to_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.1)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)

    async def busy(session_id: str) -> asyncio.Future[None]:
        sessions.held.add(session_id)
        await asyncio.Event().wait()
        raise AssertionError

    sessions.begin_resume = busy  # type: ignore[method-assign]
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    await asyncio.sleep(0.3)

    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert read.turns == []
    assert [(item.session_id, item.outcome) for item in stopped] == [
        ("a", "taken_by_client")
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_a_queued_turn_a_chat_holds_or_left_running_reads_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CONCURRENCY", 1)
    turns, sessions, resumes = _start("a", "b", "c")
    await _settle()
    sessions.held.add("b")
    sessions.tokens["c"] = object()

    read = await turns.read(OWNER)

    assert sorted((turn.session_id, turn.state) for turn in read.turns) == [
        ("a", "resuming"),
        ("c", "running"),
    ]
    _release(resumes, "a", "b", "c")
    await turns.close()


@pytest.mark.asyncio
async def test_a_failed_turn_a_chat_took_since_reads_and_stops_alike() -> None:
    turns, sessions, resumes = _start("a", "b")
    resumes.fail.update({"a", "b"})
    await _settle()
    resumes.fail.clear()
    sessions.tokens["a"] = object()
    sessions.held.add("b")

    read = await turns.read(OWNER)
    stopped = (await turns.interrupt(OWNER)).turns

    assert [(turn.session_id, turn.state) for turn in read.turns] == [("a", "running")]
    assert sorted((item.session_id, item.outcome) for item in stopped) == [
        ("a", "interrupted"),
        ("b", "taken_by_client"),
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_an_ended_turn_a_chat_left_running_reads_and_stops_alike() -> None:
    turns, sessions, resumes = _start("a")
    first = await turns.interrupt(OWNER)
    sessions.tokens["a"] = object()

    read = await turns.read(OWNER)

    assert [item.outcome for item in first.turns] == ["ended_before_running"]
    assert read.turns == []
    await turns.close()


def _busy(session_id: str) -> AppServerResponseError:
    return AppServerResponseError(
        ProtocolError(
            code=ProtocolErrorCode.CONFLICT,
            message=f"Session is already open: {session_id}",
            data={"harnessCode": "session_busy", "details": {"session_id": session_id}},
        )
    )


class _Claim:
    """The owner's claim among processes on one store, with ``held`` the
    sessions other processes hold open.
    """

    def __init__(self, *, free: bool = True) -> None:
        self.claimable = free
        self.held: set[str] = set()
        self.published: list[str] = []
        self.released = False
        self.freed: dict[str, asyncio.Event] = {}
        self.taken = 0

    async def acquire(self) -> bool:
        if self.claimable:
            self.taken += 1
        return self.claimable

    async def publish(self, session_ids: list[str]) -> None:
        self.published = session_ids

    async def running_elsewhere(self) -> set[str]:
        return self.held & set(self.published)

    async def release(self) -> None:
        self.released = True

    async def until_free(self, session_id: str) -> None:
        await self.freed.setdefault(session_id, asyncio.Event()).wait()

    def free(self, session_id: str) -> None:
        self.held.discard(session_id)
        self.freed.setdefault(session_id, asyncio.Event()).set()


@pytest.mark.asyncio
async def test_a_turn_another_process_holds_open_reads_and_stops_as_running_elsewhere() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    claim.held.add("a")

    async def resume(session_id: str, interrupt: bool) -> None:
        raise _busy(session_id)

    turns.resume_once(
        OWNER, lambda: _listed("a"), resume, sessions, lender=_durable, claim=claim
    )
    await _settle()

    read = await turns.read(OWNER)
    stopped = await turns.interrupt(OWNER)

    assert [(turn.session_id, turn.state) for turn in read.turns] == [
        ("a", "running_elsewhere")
    ]
    assert [(item.session_id, item.outcome) for item in stopped.turns] == [
        ("a", "running_elsewhere")
    ]
    assert resumes.calls == []
    await turns.close()


@pytest.mark.asyncio
async def test_a_process_without_the_owner_s_claim_resumes_nothing_and_reads_the_holder_s() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim(free=False)
    claim.published = ["a", "b"]
    claim.held.add("a")
    listings: list[str] = []

    async def list_left() -> list[str]:
        listings.append("listed")
        return ["a", "b"]

    turns.resume_once(OWNER, list_left, resumes, sessions, lender=_durable, claim=claim)
    await _settle()

    read = await turns.read(OWNER)
    stopped = await turns.interrupt(OWNER)

    assert (listings, resumes.calls) == ([], [])
    assert read == LeftTurnsRead(
        status="resumed_elsewhere", turns=[LeftTurn("a", "running_elsewhere")]
    )
    assert stopped == LeftTurnsStopped(
        status="resumed_elsewhere", turns=[StoppedLeftTurn("a", "running_elsewhere")]
    )
    await turns.close()


@pytest.mark.asyncio
async def test_the_owner_s_next_opt_in_takes_the_claim_once_its_holder_is_gone() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim(free=False)
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()
    claim.claimable = True

    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    _release(resumes, "a")
    await _settle()

    assert resumes.calls == [("a", False)]
    # Settled once resumed: let go, with nothing left published.
    assert claim.published == []
    assert (await turns.read(OWNER)).status == "listed"
    await turns.close()
    assert claim.released


@pytest.mark.asyncio
async def test_a_process_holds_an_owner_s_claim_until_it_releases_it(
    tmp_path: Path,
) -> None:
    from vibe.app_server._left_turns_claim import LeftTurnsClaim

    async def root() -> Path:
        return tmp_path

    first, second = LeftTurnsClaim(OWNER, root), LeftTurnsClaim(OWNER, root)
    bob = LeftTurnsClaim(BOB, root)

    taken = [await first.acquire(), await second.acquire(), await bob.acquire()]
    await first.release()
    taken_over = await second.acquire()
    await second.release()
    await bob.release()

    # Two opens of the lock in one process conflict, as two processes do.
    assert taken == [True, False, True]
    assert taken_over


@pytest.mark.asyncio
async def test_a_stop_asked_while_the_claim_is_taken_answers_as_a_non_holder() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim(free=False)
    claim.published = ["a"]
    claim.held.add("a")
    deciding = asyncio.Event()
    acquire = claim.acquire

    async def slow_acquire() -> bool:
        await deciding.wait()
        return await acquire()

    claim.acquire = slow_acquire  # type: ignore[method-assign]
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    stopping = asyncio.create_task(turns.interrupt(OWNER))
    await _settle()

    deciding.set()
    stopped = await asyncio.wait_for(stopping, 2)

    assert stopped == LeftTurnsStopped(
        status="resumed_elsewhere", turns=[StoppedLeftTurn("a", "running_elsewhere")]
    )
    await turns.close()


@pytest.mark.asyncio
async def test_a_failed_listing_lets_another_process_take_the_owner_s_claim(
    tmp_path: Path,
) -> None:
    from vibe.app_server._left_turns_claim import LeftTurnsClaim

    async def root() -> Path:
        return tmp_path

    async def failing() -> list[str]:
        raise RuntimeError("store unreadable")

    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    turns.resume_once(
        OWNER,
        failing,
        resumes,
        sessions,
        lender=_durable,
        claim=LeftTurnsClaim(OWNER, root),
    )
    await _until_listed(turns)
    other = LeftTurnsClaim(OWNER, root)

    taken = await other.acquire()

    await other.release()
    status = (await turns.read(OWNER)).status
    await turns.close()
    assert (status, taken) == ("listing_failed", True)


@pytest.mark.asyncio
async def test_a_claim_whose_first_publish_fails_is_still_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server import _left_turns_claim
    from vibe.app_server._left_turns_claim import LeftTurnsClaim

    async def root() -> Path:
        return tmp_path

    def sharing_violation(_path: Path, _data: bytes) -> None:
        raise PermissionError("the diagnostic is being read")

    monkeypatch.setattr(_left_turns_claim, "atomic_write_bytes", sharing_violation)
    first, second = LeftTurnsClaim(OWNER, root), LeftTurnsClaim(OWNER, root)

    taken = [await first.acquire(), await second.acquire()]

    await first.release()
    await second.release()
    assert taken == [True, False]


@pytest.mark.asyncio
async def test_a_stop_asked_while_another_process_held_the_claim_does_not_carry_over() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim(free=False)
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    await turns.interrupt(OWNER)
    claim.claimable = True

    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    _release(resumes, "a")
    await _settle()

    assert resumes.calls == [("a", False)]
    await turns.close()


class _BusyUntilFree:
    """Resumes refused as busy while ``claim`` says another process holds
    the session; once free, a plain resume parks it, an interrupt ends it.
    """

    def __init__(self, sessions: _Sessions, claim: _Claim) -> None:
        self.sessions, self.claim = sessions, claim
        self.calls: list[tuple[str, bool]] = []

    async def __call__(self, session_id: str, interrupt: bool) -> None:
        if session_id in self.claim.held:
            raise _busy(session_id)
        self.calls.append((session_id, interrupt))
        if not interrupt:
            self.sessions.tokens[session_id] = object()


def _busy_elsewhere() -> tuple[LeftTurns, _Sessions, _Claim, _BusyUntilFree]:
    turns, sessions = LeftTurns(), _Sessions()
    claim = _Claim()
    claim.held.add("a")
    resume = _BusyUntilFree(sessions, claim)
    turns.resume_once(
        OWNER, lambda: _listed("a"), resume, sessions, lender=_durable, claim=claim
    )
    return turns, sessions, claim, resume


@pytest.mark.asyncio
async def test_a_turn_running_elsewhere_is_resumed_once_its_lease_is_free() -> None:
    turns, _sessions, claim, resume = _busy_elsewhere()
    await _settle()
    elsewhere = [turn.state for turn in (await turns.read(OWNER)).turns]

    claim.free("a")
    await _settle()

    assert elsewhere == ["running_elsewhere"]
    assert resume.calls == [("a", False)]
    assert [turn.state for turn in (await turns.read(OWNER)).turns] == ["running"]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stopped_turn_running_elsewhere_ends_once_its_lease_is_free() -> None:
    turns, _sessions, claim, resume = _busy_elsewhere()
    await _settle()

    stopped = await asyncio.wait_for(turns.interrupt(OWNER), 2)
    claim.free("a")
    await _settle()
    again = await asyncio.wait_for(turns.interrupt(OWNER), 2)

    assert [item.outcome for item in stopped.turns] == ["running_elsewhere"]
    assert resume.calls == [("a", True)]
    assert [item.outcome for item in again.turns] == ["ended_before_running"]
    await turns.close()


class _ClaimCheckingParked(_Parked):
    def __init__(self, claim: _Claim) -> None:
        super().__init__()
        self.claim = claim
        self.claim_held_at_stop: list[bool] = []

    async def stop(self) -> None:
        self.claim_held_at_stop.append(not self.claim.released)
        await super().stop()


@pytest.mark.asyncio
async def test_the_exit_releases_the_owner_s_claim_only_once_parked_sessions_stop() -> (
    None
):
    turns, open_sessions = LeftTurns(), OpenSessions()
    claim = _Claim()
    parked = _ClaimCheckingParked(claim)

    async def resume(session_id: str, interrupt: bool) -> None:
        if session_id == "b":
            # Still being resumed at the exit: the claim is not settled.
            await asyncio.Event().wait()
        open_sessions.park(session_id, parked)

    turns.resume_once(
        OWNER,
        lambda: _listed("a", "b"),
        resume,
        _Sessions(),
        lender=_durable,
        claim=claim,
    )
    await _settle()

    await close_left_turns_first(turns, open_sessions)

    assert parked.claim_held_at_stop == [True]
    assert claim.released


@pytest.mark.asyncio
async def test_a_stop_answers_while_a_turn_freed_elsewhere_is_busy_again() -> None:
    turns, _sessions, claim, resume = _busy_elsewhere()
    await _settle()
    claim.freed["a"] = asyncio.Event()
    claim.freed["a"].set()
    # Freed, then held again before the resume gets to it.
    await _settle()

    stopped = await asyncio.wait_for(turns.interrupt(OWNER), 2)

    assert [item.outcome for item in stopped.turns] == ["running_elsewhere"]
    assert resume.calls == []
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_running_elsewhere_a_chat_here_holds_is_not_read_as_elsewhere() -> (
    None
):
    turns, sessions, _claim, _resume = _busy_elsewhere()
    await _settle()

    sessions.held.add("a")
    read = await turns.read(OWNER)

    assert read.turns == []
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_running_elsewhere_is_resumed_after_a_sweep_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_WATCH_RETRY_S", 0.01)
    turns, _sessions, claim, resume = _busy_elsewhere()
    until_free = claim.until_free
    sweeps: list[str] = []

    async def failing_once(session_id: str) -> None:
        sweeps.append(session_id)
        if len(sweeps) == 1:
            raise PermissionError("the lease registry is locked")
        await until_free(session_id)

    claim.until_free = failing_once  # type: ignore[method-assign]
    await _settle()
    claim.free("a")
    for _ in range(50):
        if resume.calls:
            break
        await asyncio.sleep(0.01)

    assert resume.calls == [("a", False)]
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_that_drops_a_turn_running_elsewhere_ends_its_watch() -> None:
    turns, sessions, _claim, _resume = _busy_elsewhere()
    await _settle()
    sessions.held.add("a")

    stopped = await asyncio.wait_for(turns.interrupt(OWNER), 2)
    await _settle()

    assert [item.outcome for item in stopped.turns] == ["taken_by_client"]
    assert [task for task in turns._tasks if not task.done()] == []
    await turns.close()


@pytest.mark.asyncio
async def test_the_holder_lets_the_owner_s_claim_go_once_its_turns_have_settled() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()
    held_while_resuming = not claim.released

    _release(resumes, "a")
    await _settle()

    assert held_while_resuming
    assert claim.released
    assert claim.published == []
    assert [
        (turn.session_id, turn.state) for turn in (await turns.read(OWNER)).turns
    ] == [("a", "running")]
    await turns.close()


@pytest.mark.asyncio
async def test_a_process_without_the_claim_takes_it_once_freed_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CLAIM_RETRY_S", 0.01)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim(free=False)
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()
    nothing_yet = list(resumes.calls)

    claim.claimable = True
    for _ in range(100):
        if resumes.calls:
            break
        await asyncio.sleep(0.01)

    assert nothing_yet == []
    assert resumes.calls == [("a", False)]
    assert (await turns.read(OWNER)).status == "listed"
    _release(resumes, "a")
    await turns.close()


@pytest.mark.asyncio
async def test_a_holder_relists_when_its_owner_opts_in_after_letting_the_claim_go() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    _release(resumes, "a", "b")
    await _settle()

    turns.resume_once(
        OWNER, lambda: _listed("b"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()

    assert resumes.calls == [("a", False), ("b", False)]
    assert sorted(turn.session_id for turn in (await turns.read(OWNER)).turns) == [
        "a",
        "b",
    ]
    await turns.close()


@pytest.mark.asyncio
async def test_the_holder_publishes_only_the_turns_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CLAIM_RETRY_S", 0.01)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    turns.resume_once(
        OWNER,
        lambda: _listed("a", "b"),
        resumes,
        sessions,
        lender=_durable,
        claim=claim,
    )
    await _settle()
    while_resuming = sorted(claim.published)
    _release(resumes, "a")
    await _settle()
    while_running = sorted(claim.published)

    # Its parked turn ends, as a parked session stops once idle.
    sessions.tokens.pop("a")
    for _ in range(50):
        if claim.published == ["b"]:
            break
        await asyncio.sleep(0.01)

    assert while_resuming == ["a", "b"]
    assert while_running == ["a", "b"]
    assert claim.published == ["b"]
    _release(resumes, "b")
    await turns.close()


@pytest.mark.asyncio
async def test_a_stop_does_not_hold_for_turns_listed_once_it_settled() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    _release(resumes, "a", "b")
    await _settle()
    await turns.interrupt(OWNER)
    await _settle()

    turns.resume_once(
        OWNER, lambda: _listed("b"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()

    assert ("b", False) in resumes.calls
    assert ("b", True) not in resumes.calls
    await turns.close()


@pytest.mark.asyncio
async def test_a_relisting_resumes_a_session_this_process_saw_end_before() -> None:
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    _release(resumes, "a")
    await _settle()
    # Its turn ended here; another process later left it mid-turn again.
    sessions.tokens.pop("a")
    resumes.release["a"] = asyncio.Event()

    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=claim
    )
    await _settle()

    assert resumes.calls == [("a", False), ("a", False)]
    _release(resumes, "a")
    await turns.close()


@pytest.mark.asyncio
async def test_a_takeover_cancelled_once_it_took_the_claim_lets_it_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "_CLAIM_RETRY_S", 0.01)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    first = _Claim(free=False)
    publishing = asyncio.Event()
    taken = first.acquire

    async def taken_then_publishing() -> bool:
        if await taken():
            # Taken, then still publishing when a new opt-in supersedes it.
            await publishing.wait()
            return True
        return False

    first.acquire = taken_then_publishing  # type: ignore[method-assign]
    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=first
    )
    await _settle()
    first.claimable = True
    for _ in range(100):
        if first.taken:
            break
        await asyncio.sleep(0.01)

    turns.resume_once(
        OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable, claim=_Claim()
    )
    await _settle()

    assert first.released
    _release(resumes, "a")
    await turns.close()


@pytest.mark.asyncio
async def test_a_listing_that_hangs_lets_the_claim_go_once_its_time_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.05)
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    claim = _Claim()

    async def hangs() -> list[str]:
        await asyncio.Event().wait()
        return []

    turns.resume_once(OWNER, hangs, resumes, sessions, lender=_durable, claim=claim)
    await asyncio.sleep(0.2)

    assert claim.released
    assert (await turns.read(OWNER)).status == "listing_failed"
    await turns.close()


@pytest.mark.asyncio
async def test_a_turn_a_chat_opened_while_it_was_being_resumed_does_not_read_resuming() -> (
    None
):
    turns, sessions = LeftTurns(), _Sessions()
    resumes = _Resumes(sessions)
    turns.resume_once(OWNER, lambda: _listed("a"), resumes, sessions, lender=_durable)
    await _settle()
    # Its own resume holds it: it reads as resuming.
    sessions.held.add("a")
    while_own = await turns.read(OWNER)

    # The resume let it go and a chat holds it before its state moves on.
    sessions.resuming_ids.remove("a")
    read = await turns.read(OWNER)
    sessions.resuming_ids.append("a")

    assert [turn.state for turn in while_own.turns] == ["resuming"]
    assert read.turns == []
    _release(resumes, "a")
    await turns.close()
