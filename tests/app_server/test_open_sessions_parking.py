from __future__ import annotations

import asyncio

import pytest

from vibe.app_server import _open_sessions
from vibe.app_server._open_sessions import OpenSessions


class _Session:
    def __init__(self) -> None:
        self.working = True
        self.stops = 0
        self.stopping = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.stopped = False

    def has_turn_work(self) -> bool:
        return self.working

    async def stop(self) -> None:
        self.stops += 1
        self.stopping.set()
        await self.release.wait()
        self.stopped = True


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_open_sessions, "_PARK_POLL_S", 0.01)


@pytest.mark.asyncio
async def test_a_parked_session_stops_once_its_turn_work_ends() -> None:
    sessions = OpenSessions()
    session = _Session()
    sessions.park("s", session)

    session.working = False
    await asyncio.wait_for(session.stopping.wait(), 1)

    assert session.stops == 1
    assert sessions.parked("s") is None


@pytest.mark.asyncio
async def test_a_taken_session_is_not_stopped_by_its_watcher() -> None:
    sessions = OpenSessions()
    session = _Session()
    sessions.park("s", session)

    assert sessions.take_parked("s") is session
    session.working = False
    await asyncio.sleep(0.05)

    assert session.stops == 0


@pytest.mark.asyncio
async def test_a_resume_during_the_stop_waits_for_it() -> None:
    sessions = OpenSessions()
    session = _Session()
    session.release.clear()
    sessions.park("s", session)
    session.working = False
    await asyncio.wait_for(session.stopping.wait(), 1)

    waiting = asyncio.create_task(sessions.wait_stopped("s"))
    await asyncio.sleep(0.02)
    assert not waiting.done()
    session.release.set()

    await asyncio.wait_for(waiting, 1)


@pytest.mark.asyncio
async def test_closing_stops_every_parked_session() -> None:
    sessions = OpenSessions()
    first, second = _Session(), _Session()
    sessions.park("a", first)
    sessions.park("b", second)

    await sessions.close()

    assert (first.stops, second.stops) == (1, 1)
    assert sessions.parked("a") is None


class _FailingStop(_Session):
    async def stop(self) -> None:
        self.stops += 1
        self.stopping.set()
        if self.stops == 1:
            raise RuntimeError("stop failed")


@pytest.mark.asyncio
async def test_a_session_whose_stop_failed_stays_parked() -> None:
    sessions = OpenSessions()
    session = _FailingStop()
    sessions.park("s", session)

    session.working = False
    await asyncio.wait_for(session.stopping.wait(), 1)
    await asyncio.sleep(0.05)

    assert sessions.parked("s") is session
    await sessions.close()
    assert session.stops == 2


@pytest.mark.asyncio
async def test_a_resume_during_an_attach_waits_for_it() -> None:
    sessions = OpenSessions()
    session = _Session()
    sessions.park("s", session)

    attaching = sessions.take_parked("s")
    waiting = asyncio.create_task(sessions.wait_stopped("s"))
    await asyncio.sleep(0.02)
    assert attaching is session
    assert not waiting.done()
    sessions.attached("s")

    await asyncio.wait_for(waiting, 1)


@pytest.mark.asyncio
async def test_closing_lets_a_stop_already_under_way_finish() -> None:
    sessions = OpenSessions()
    session = _Session()
    session.release.clear()
    sessions.park("s", session)
    session.working = False
    await asyncio.wait_for(session.stopping.wait(), 1)

    closing = asyncio.create_task(sessions.close())
    await asyncio.sleep(0.02)
    session.release.set()
    await asyncio.wait_for(closing, 1)

    assert session.stops == 1
    assert session.stopped


@pytest.mark.asyncio
async def test_a_server_resume_waits_for_a_session_being_settled() -> None:
    sessions = OpenSessions()
    session = _Session()
    sessions.park("s", session)
    assert sessions.take_parked("s") is session

    claiming = asyncio.create_task(sessions.begin_resume("s"))
    await asyncio.sleep(0.02)
    waited = not claiming.done()
    sessions.attached("s")
    claim = await asyncio.wait_for(claiming, 1)

    assert waited
    sessions.end_resume("s", claim)


@pytest.mark.asyncio
async def test_parking_or_attaching_leaves_a_server_resume_claimed() -> None:
    sessions = OpenSessions()
    session = _Session()
    claim = await sessions.begin_resume("s")

    sessions.park("s", session)
    sessions.take_parked("s")
    sessions.attached("s")

    assert sessions.resuming("s")
    sessions.end_resume("s", claim)
    assert not sessions.resuming("s")


@pytest.mark.asyncio
async def test_ending_a_server_resume_leaves_a_client_s_attach_settling() -> None:
    sessions = OpenSessions()
    session = _Session()
    claim = await sessions.begin_resume("s")
    sessions.park("s", session)
    sessions.take_parked("s")

    sessions.end_resume("s", claim)

    assert sessions.settling("s")
    sessions.attached("s")


@pytest.mark.asyncio
async def test_a_server_resume_waits_for_a_client_opening_the_session() -> None:
    sessions = OpenSessions()
    opened = asyncio.Event()

    async def open_from_store() -> None:
        async with sessions.opening("s"):
            await opened.wait()

    opening = asyncio.create_task(open_from_store())
    await asyncio.sleep(0)
    claiming = asyncio.create_task(sessions.begin_resume("s"))
    await asyncio.sleep(0.02)
    waited = not claiming.done()
    opened.set()
    await opening
    claim = await asyncio.wait_for(claiming, 1)

    assert waited
    sessions.end_resume("s", claim)


@pytest.mark.asyncio
async def test_an_open_leaves_a_settle_it_did_not_start_alone() -> None:
    sessions = OpenSessions()
    session = _Session()
    sessions.park("s", session)
    sessions.take_parked("s")

    opening = asyncio.create_task(_open_while(sessions, "s"))
    await asyncio.sleep(0.02)
    waited = not opening.done()
    sessions.attached("s")
    await asyncio.wait_for(opening, 1)

    assert waited
    assert not sessions.settling("s")


async def _open_while(sessions: OpenSessions, session_id: str) -> None:
    async with sessions.opening(session_id):
        assert sessions.settling(session_id)
