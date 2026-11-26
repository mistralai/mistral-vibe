from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from vibe.app_server import _unified_scheduled_loops as scheduled_loops_module
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)
from vibe.core.loop import LoopError


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_scheduled_loops_store_file_is_owner_only(tmp_path: Path) -> None:
    """*Prepare*: A persistent Unified loop store with no file yet.
    *Do*: Create one scheduled loop, which persists the store.
    *Assert*: The store file is created owner-only.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)

    # Do
    await loops.create("30s", "check the build")

    # Assert
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


@pytest.mark.asyncio
async def test_scheduled_loops_persist_reschedule_and_restore(tmp_path: Path) -> None:
    """*Prepare*: A persistent Unified loop store with one scheduled prompt.
    *Do*: Mark the loop fired and restore the store from disk.
    *Assert*: The restored loop keeps its identity and rescheduled fire time.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)
    created = await loops.create("30s", "check the build")

    # Do
    rescheduled = await loops.mark_fired(created.id, now=100.0)
    restored = UnifiedScheduledLoops(path, persistent=lambda: True)
    await restored.restore()

    # Assert
    assert rescheduled is not None
    assert rescheduled.next_fire_at == 130.0
    assert await restored.list() == [rescheduled]
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == (
        "vibe.scheduled-loops/v1"
    )


@pytest.mark.asyncio
async def test_scheduled_loops_select_the_oldest_due_loop(tmp_path: Path) -> None:
    """*Prepare*: Two loops whose first and second fire times are both in the past.
    *Do*: Ask the store for the next due loop and reschedule it.
    *Assert*: The oldest loop fires first and moves one interval past the supplied time.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    first = await loops.create("30s", "first")
    second = await loops.create("1m", "second")
    await loops.replace([
        first.model_copy(update={"next_fire_at": 10.0}),
        second.model_copy(update={"next_fire_at": 20.0}),
    ])

    # Do
    due = await loops.due(now=30.0)
    rescheduled = await loops.mark_fired(first.id, now=30.0)

    # Assert
    assert due is not None
    assert due.id == first.id
    assert rescheduled is not None
    assert rescheduled.next_fire_at == 60.0
    assert await loops.next_due_in(now=30.0) == 0.0
    next_due = await loops.due(now=30.0)
    assert next_due is not None
    assert next_due.id == second.id


@pytest.mark.asyncio
async def test_scheduled_loops_report_a_corrupt_store(tmp_path: Path) -> None:
    """*Prepare*: A scheduled-loop file that is not valid JSON.
    *Do*: Restore it through the Unified loop store.
    *Assert*: Corruption is reported with the affected path and is not ignored.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    path.write_text("not-json", encoding="utf-8")
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)

    # Do
    with pytest.raises(ScheduledLoopStoreError) as exc_info:
        await loops.restore()
    await loops.persist()

    # Assert
    assert str(path) in str(exc_info.value)
    assert path.read_text(encoding="utf-8") == "not-json"


@pytest.mark.asyncio
async def test_scheduled_loops_quarantine_a_corrupt_store(tmp_path: Path) -> None:
    """*Prepare*: A loop store whose current file is corrupt.
    *Do*: Quarantine it after restore reports the corruption.
    *Assert*: The invalid bytes survive and a new empty store can be persisted.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    path.write_text("not-json", encoding="utf-8")
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)
    with pytest.raises(ScheduledLoopStoreError):
        await loops.restore()

    # Do
    quarantine_path = await loops.quarantine_corrupt_store()
    await loops.persist()

    # Assert
    assert not path.samefile(quarantine_path)
    assert quarantine_path.read_text(encoding="utf-8") == "not-json"
    assert await loops.list() == []
    assert json.loads(path.read_text(encoding="utf-8"))["loops"] == []


@pytest.mark.asyncio
async def test_scheduled_loop_mutations_are_atomic_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: One persisted loop and a store whose writes now fail.
    *Do*: Try every schedule mutation.
    *Assert*: Each reports failure without changing the live schedule.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)
    created = await loops.create("30s", "keep me")
    before = await loops.list()
    persisted = path.read_text(encoding="utf-8")

    async def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(scheduled_loops_module, "atomic_replace", fail_write)

    # Do / Assert
    with pytest.raises(ScheduledLoopStoreError):
        await loops.create("1m", "do not retain me")
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.update(created.id, "do not keep me", interval="1m")
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.run_now(created.id)
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.delete(created.id)
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.clear()
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.mark_fired(created.id, now=100.0)
    assert await loops.list() == before
    assert path.read_text(encoding="utf-8") == persisted


@pytest.mark.asyncio
async def test_updating_a_loop_prompt_keeps_its_identity_and_next_run(
    tmp_path: Path,
) -> None:
    """*Prepare*: A persistent store with one loop due at a known time.
    *Do*: Update its prompt with the same interval, then restore the store.
    *Assert*: The loop keeps its id, creation time and next run, with the new prompt.
    """
    # Prepare
    path = tmp_path / "scheduled-loops.json"
    loops = UnifiedScheduledLoops(path, persistent=lambda: True)
    created = await loops.create("10m", "Old\ncheck the build")
    await loops.replace([created.model_copy(update={"next_fire_at": 1234.0})])

    # Do
    updated = await loops.update(created.id, "New\ncheck the tests", interval="10M")
    restored = UnifiedScheduledLoops(path, persistent=lambda: True)
    await restored.restore()

    # Assert
    assert updated.model_dump() == {
        **created.model_dump(),
        "prompt": "New\ncheck the tests",
        "next_fire_at": 1234.0,
    }
    assert await restored.list() == [updated]


@pytest.mark.asyncio
async def test_a_cron_respaced_on_edit_keeps_its_next_run(tmp_path: Path) -> None:
    """*Prepare*: A cron loop stored with doubled spaces, due at a known time.
    *Do*: Update its prompt with the same cron written with single spaces.
    *Assert*: The schedule counts as unchanged, so the next run is kept.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    created = await loops.create_cron("0  9 * * 1-5", "check the build")
    await loops.replace([created.model_copy(update={"next_fire_at": 1234.0})])

    # Do
    updated = await loops.update(created.id, "check the tests", cron="0 9 * * 1-5")

    # Assert
    assert updated.next_fire_at == 1234.0


@pytest.mark.asyncio
async def test_updating_a_loop_schedule_counts_its_next_run_from_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A loop every ten minutes, due at a known time.
    *Do*: Change it to every hour, then to a cron, at a fixed time.
    *Assert*: Each new schedule replaces the old one and its next run counts from now.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    created = await loops.create("10m", "check the build")
    await loops.replace([created.model_copy(update={"next_fire_at": 1234.0})])
    monkeypatch.setattr(scheduled_loops_module.time, "time", lambda: 10_000.0)

    # Do
    hourly = await loops.update(created.id, "check the build", interval="1h")
    cron = await loops.update(created.id, "check the build", cron=" 0 9 * * 1-5 ")

    # Assert
    assert (hourly.interval_seconds, hourly.cron, hourly.next_fire_at) == (
        3600,
        None,
        13_600.0,
    )
    assert (cron.interval_seconds, cron.cron) == (None, "0 9 * * 1-5")
    assert cron.next_fire_at > 10_000.0
    assert await loops.list() == [cron]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loop_id", "prompt", "interval", "message"),
    [
        ("missing", "check the build", "10m", "No scheduled loop with id `missing`."),
        (None, "/compact", "10m", "Prompt cannot start with '/'."),
        (None, "check the build", "5s", "Interval must be at least 30s."),
    ],
)
async def test_an_invalid_loop_update_changes_nothing(
    tmp_path: Path, loop_id: str | None, prompt: str, interval: str, message: str
) -> None:
    """*Prepare*: A store with one loop.
    *Do*: Update an unknown loop, or the loop with an invalid prompt or interval.
    *Assert*: The update is refused with a readable reason and the loop is unchanged.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    created = await loops.create("10m", "check the build")

    # Do / Assert
    with pytest.raises(LoopError) as caught:
        await loops.update(loop_id or created.id, prompt, interval=interval)
    assert str(caught.value) == message
    assert await loops.list() == [created]


@pytest.mark.asyncio
async def test_running_a_loop_now_makes_it_due_then_it_keeps_its_schedule(
    tmp_path: Path,
) -> None:
    """*Prepare*: A loop every ten minutes, next due far in the future.
    *Do*: Run it now, then mark it fired as the runner does.
    *Assert*: It is due at once, and its next run is one interval after the fire.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    created = await loops.create("10m", "check the build")
    await loops.replace([created.model_copy(update={"next_fire_at": 99_999.0})])

    # Do
    due = await loops.run_now(created.id, now=1_000.0)
    picked = await loops.due(now=1_000.0)
    fired = await loops.mark_fired(created.id, now=1_000.0)

    # Assert
    assert due.next_fire_at == 1_000.0
    assert picked == due
    assert fired is not None
    assert (fired.id, fired.prompt, fired.next_fire_at) == (
        created.id,
        "check the build",
        1_600.0,
    )


@pytest.mark.asyncio
async def test_running_an_unknown_loop_is_refused(tmp_path: Path) -> None:
    """*Prepare*: A store with one loop.
    *Do*: Run a loop id it does not have.
    *Assert*: The run is refused with a readable reason and the loop is unchanged.
    """
    # Prepare
    loops = UnifiedScheduledLoops(
        tmp_path / "scheduled-loops.json", persistent=lambda: False
    )
    created = await loops.create("10m", "check the build")

    # Do / Assert
    with pytest.raises(LoopError) as caught:
        await loops.run_now("missing")
    assert str(caught.value) == "No scheduled loop with id `missing`."
    assert await loops.list() == [created]
