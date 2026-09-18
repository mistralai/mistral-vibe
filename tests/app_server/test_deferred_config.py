from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_concurrent_writes_leave_the_latest_configuration_running() -> None:
    """*Prepare*: A derivation that reads the configuration before it completes.
    *Do*: Apply twice at once, with the configuration changing in between.
    *Assert*: The session ends up running what was written last. Deriving and
    pushing are two awaits, so unserialised callers can push in reverse order
    and leave the session on a configuration older than the persisted one.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._deferred_config import DeferredConfiguration

    written = ["first"]
    delays = iter([0.05, 0.0])
    pushed: list[str] = []
    running: list[str] = []

    async def derive() -> str:
        value = written[-1]
        await asyncio.sleep(next(delays))
        return value

    async def push(value: str) -> None:
        pushed.append(value)

    deferred = DeferredConfiguration(
        derive=derive, push=push, adopt=running.append, turn_running=lambda: False
    )

    first = asyncio.create_task(deferred.apply())
    await asyncio.sleep(0)
    written.append("second")
    second = asyncio.create_task(deferred.apply())
    await asyncio.gather(first, second)

    assert pushed == ["first", "second"]
    assert running[-1] == "second"


@pytest.mark.asyncio
async def test_a_turn_that_wins_the_push_leaves_the_write_parked() -> None:
    """*Prepare*: A session that refuses the push because a turn just opened.
    *Do*: Apply.
    *Assert*: Parked rather than raised. The event pump applies from here, and
    an exception there would end the session's live stream; parking leaves the
    next turn boundary to settle it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import HarnessTurnConflictError

    from vibe.app_server._deferred_config import DeferredConfiguration

    async def derive() -> str:
        return "picked"

    async def push(value: str) -> None:
        raise HarnessTurnConflictError("turn-1")

    deferred = DeferredConfiguration(
        derive=derive,
        push=push,
        adopt=lambda value: pytest.fail("A refused push must not be adopted"),
        turn_running=lambda: False,
    )

    await deferred.apply()

    assert deferred.parked is True


@pytest.mark.asyncio
async def test_a_cancelled_apply_leaves_the_write_parked() -> None:
    """*Prepare*: A push that is cancelled while it waits, as the event pump is
    when the session's stream ends mid-apply.
    *Do*: Cancel the apply.
    *Assert*: Still parked. The apply clears the flag before it pushes, so
    anything that stops it in between drops a pick the user already made --
    the next turn boundary has to still find it waiting.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._deferred_config import DeferredConfiguration

    pushing = asyncio.Event()

    async def derive() -> str:
        return "picked"

    async def push(value: str) -> None:
        pushing.set()
        await asyncio.Event().wait()

    deferred = DeferredConfiguration(
        derive=derive,
        push=push,
        adopt=lambda value: pytest.fail("A cancelled push must not be adopted"),
        turn_running=lambda: False,
    )
    deferred.park()

    applying = asyncio.create_task(deferred.settle())
    await pushing.wait()
    applying.cancel()
    with pytest.raises(asyncio.CancelledError):
        await applying

    assert deferred.parked is True


@pytest.mark.asyncio
async def test_settle_waits_for_an_apply_already_in_flight() -> None:
    """*Prepare*: An apply that has cleared the parked flag and is mid-derive.
    *Do*: Settle again, as the next turn opening does.
    *Assert*: The second caller waits. It is the turn boundary asking whether it
    may open, and the flag is cleared before the push, so returning early would
    let the turn start on the configuration the apply is in the middle of
    replacing -- and the apply would then find a turn running and re-park.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._deferred_config import DeferredConfiguration

    deriving = asyncio.Event()
    release = asyncio.Event()
    pushed: list[str] = []

    async def derive() -> str:
        deriving.set()
        await release.wait()
        return "picked"

    async def push(value: str) -> None:
        pushed.append(value)

    deferred = DeferredConfiguration(
        derive=derive, push=push, adopt=lambda value: None, turn_running=lambda: False
    )
    deferred.park()

    in_flight = asyncio.create_task(deferred.settle())
    await deriving.wait()
    at_turn_start = asyncio.create_task(deferred.settle())
    await asyncio.sleep(0)

    assert at_turn_start.done() is False

    release.set()
    await asyncio.gather(in_flight, at_turn_start)

    assert pushed == ["picked"]


@pytest.mark.asyncio
async def test_a_caller_pushing_its_own_configuration_waits_its_turn() -> None:
    """*Prepare*: A settle already deriving.
    *Do*: Take the deferred configuration exclusively, as an agent switch does
    when it pushes a derivation of its own.
    *Assert*: It waits, and lands after. Pushing outside the lock lets a
    derivation computed before the switch arrive after it, putting the session
    back on the agent the user just moved off.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._deferred_config import DeferredConfiguration

    landed: list[str] = []
    release = asyncio.Event()

    async def derive() -> str:
        await release.wait()
        return "settled"

    async def push(value: str) -> None:
        landed.append(value)

    deferred = DeferredConfiguration(
        derive=derive, push=push, adopt=lambda value: None, turn_running=lambda: False
    )
    deferred.park()

    async def switch() -> None:
        async with deferred.exclusive():
            landed.append("switched")

    settling = asyncio.create_task(deferred.settle())
    await asyncio.sleep(0)
    switching = asyncio.create_task(switch())
    await asyncio.sleep(0)

    assert switching.done() is False

    release.set()
    await asyncio.gather(settling, switching)

    assert landed == ["settled", "switched"]


@pytest.mark.asyncio
async def test_settle_reports_whether_the_session_moved() -> None:
    """*Prepare*: A parked derivation.
    *Do*: Settle it, then settle again with nothing parked, then park once more
    under a running turn.
    *Assert*: Only the settle that pushed answers yes. The write that parked the
    derivation answered ``pending``, so its caller announces the change from
    here, and announcing a settle that moved nothing would tell subscribers a
    configuration changed when it did not.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._deferred_config import DeferredConfiguration

    turn: list[str | None] = [None]

    async def derive() -> str:
        return "picked"

    async def push(value: str) -> None:
        return None

    deferred = DeferredConfiguration(
        derive=derive,
        push=push,
        adopt=lambda value: None,
        turn_running=lambda: turn[0] is not None,
    )
    deferred.park()

    landed = await deferred.settle()
    nothing_to_land = await deferred.settle()
    turn[0] = "turn-1"
    deferred.park()
    still_running = await deferred.settle()

    assert landed is True
    assert nothing_to_land is False
    assert still_running is False
