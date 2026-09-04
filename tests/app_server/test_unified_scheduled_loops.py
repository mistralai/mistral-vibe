from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.app_server import _unified_scheduled_loops as scheduled_loops_module
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)


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
        await loops.delete(created.id)
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.clear()
    assert await loops.list() == before

    with pytest.raises(ScheduledLoopStoreError):
        await loops.mark_fired(created.id, now=100.0)
    assert await loops.list() == before
    assert path.read_text(encoding="utf-8") == persisted
