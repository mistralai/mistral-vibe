from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from vibe.app_server import _unified_scheduled_loops as scheduled_loops_module
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    ScheduledPrompt,
    UnifiedScheduledLoops,
)
from vibe.core.loop import LoopError


@pytest.fixture
def store(tmp_path: Path) -> UnifiedScheduledLoops:
    return UnifiedScheduledLoops(tmp_path / "loops.json", persistent=lambda: True)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = SimpleNamespace(now=datetime(2026, 1, 31, 12, 34, 45).timestamp())
    monkeypatch.setattr(
        scheduled_loops_module, "time", SimpleNamespace(time=lambda: clock.now)
    )
    return clock


@pytest.mark.asyncio
async def test_numeric_and_legacy_intervals_share_schedule_semantics(store, clock):
    numeric = await store.create_interval(30, "  check build  ")
    legacy = await store.create("1m", "check tests")

    assert numeric.interval_seconds == 30
    assert numeric.cron is None
    assert numeric.prompt == "check build"
    assert numeric.created_at == clock.now
    assert numeric.next_fire_at == clock.now + 30
    assert legacy.next_fire_at == clock.now + 60
    assert await store.next_due_in(clock.now) == 30
    assert await store.due(clock.now) is None
    assert await store.due(clock.now + 30) == numeric
    fired = await store.mark_fired(numeric.id, now=clock.now + 1000)
    assert fired is not None
    assert fired.next_fire_at == clock.now + 1030


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cron", "start", "expected"),
    [
        ("*/15 * * * *", "2026-01-31T12:34:45", "2026-01-31T12:45:00"),
        ("0 9 * * MON-FRI", "2026-01-30T10:00:00", "2026-02-02T09:00:00"),
        ("0 9 1 * *", "2026-01-31T12:00:00", "2026-02-01T09:00:00"),
        ("0 9 1 * *", "2026-02-01T09:00:00", "2026-03-01T09:00:00"),
        ("0 9 29 2 *", "2026-01-01T00:00:00", "2028-02-29T09:00:00"),
        ("0 9 1 * MON", "2026-01-02T00:00:00", "2026-01-05T09:00:00"),
        ("0 9 * * *", "2026-03-28T10:00:00", "2026-03-29T09:00:00"),
        ("0 9 * * *", "2026-10-24T10:00:00", "2026-10-25T09:00:00"),
        ("* * * * *", "2026-01-31T12:34:59", "2026-01-31T12:35:00"),
    ],
)
async def test_cron_uses_next_machine_local_calendar_time(
    store, clock, cron, start, expected
):
    clock.now = datetime.fromisoformat(start).timestamp()

    scheduled = await store.create_cron(cron, "check build")

    assert scheduled.interval_seconds is None
    assert scheduled.cron == cron
    assert scheduled.created_at == clock.now
    assert scheduled.next_fire_at == datetime.fromisoformat(expected).timestamp()
    assert scheduled.next_fire_at > clock.now


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cron", "start", "expected"),
    [
        ("* * * * *", "2026-11-01T01:59:00-04:00", "2026-11-01T01:00:00-05:00"),
        ("* * * * *", "2026-11-01T01:30:00-05:00", "2026-11-01T01:31:00-05:00"),
        ("30 1 * * *", "2026-11-01T01:59:00-04:00", "2026-11-01T01:30:00-05:00"),
        ("* * * * *", "2026-03-08T01:59:00-05:00", "2026-03-08T03:00:00-04:00"),
        ("30 * * * *", "2026-03-08T01:59:00-05:00", "2026-03-08T03:30:00-04:00"),
        ("30 2 * * *", "2026-03-08T01:59:00-05:00", "2026-03-08T03:00:00-04:00"),
        ("0 9 * * *", "2026-03-07T09:00:00-05:00", "2026-03-08T09:00:00-04:00"),
        ("0 9 * * *", "2026-10-31T09:00:00-04:00", "2026-11-01T09:00:00-05:00"),
    ],
)
async def test_cron_calendar_observes_pinned_new_york_transitions(
    store, clock, monkeypatch, cron, start, expected
):
    monkeypatch.setenv("TZ", "America/New_York")
    clock.now = datetime.fromisoformat(start).timestamp()

    created = await store.create_cron(cron, "check build")

    assert created.next_fire_at == datetime.fromisoformat(expected).timestamp()
    assert created.next_fire_at > clock.now
    assert await store.due(clock.now) is None
    fired = await store.mark_fired(created.id, now=clock.now)
    assert fired is not None
    assert fired.next_fire_at == created.next_fire_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start",
    [
        "2026-11-01T01:59:00-04:00",
        "2026-11-01T01:30:00-05:00",
        "2026-03-08T01:59:00-05:00",
    ],
)
async def test_every_minute_cron_advances_through_pinned_new_york_transitions(
    store, clock, monkeypatch, start
):
    monkeypatch.setenv("TZ", "America/New_York")
    clock.now = datetime.fromisoformat(start).timestamp()
    created = await store.create_cron("* * * * *", "check build")
    assert created.next_fire_at == clock.now + 60
    assert await store.due(clock.now) is None

    for _ in range(65):
        clock.now = created.next_fire_at
        fired = await store.mark_fired(created.id, now=clock.now)
        assert fired is not None
        assert fired.next_fire_at == clock.now + 60
        assert await store.next_due_in(clock.now) == 60
        assert await store.due(clock.now) is None
        created = fired


@pytest.mark.asyncio
async def test_interval_model_serialization_keeps_legacy_shape(store, clock):
    created = await store.create_interval(30, "check build")
    expected = {
        "id": created.id,
        "interval_seconds": 30,
        "prompt": "check build",
        "next_fire_at": clock.now + 30,
        "created_at": clock.now,
    }
    assert created.model_dump() == expected
    assert json.loads(created.model_dump_json()) == expected
    cron = await store.create_cron("0 9 * * *", "report")
    assert cron.model_dump()["cron"] == "0 9 * * *"


@pytest.mark.asyncio
async def test_cron_mark_fired_skips_missed_occurrences_and_restores(
    store, clock, tmp_path
):
    created = await store.create_cron("  0 9 1 * *  ", "  monthly report  ")
    assert created.cron == "0 9 1 * *"
    assert created.prompt == "monthly report"
    clock.now = datetime(2026, 5, 15, 12).timestamp()

    fired = await store.mark_fired(created.id)
    restored = UnifiedScheduledLoops(tmp_path / "loops.json", persistent=lambda: True)
    await restored.restore()

    assert fired is not None
    assert fired.next_fire_at == datetime(2026, 6, 1, 9).timestamp()
    assert fired.id == created.id
    assert fired.created_at == created.created_at
    assert await restored.list() == [fired]
    assert await restored.due(clock.now) is None
    assert await restored.mark_fired("unknown", clock.now) is None


@pytest.mark.asyncio
async def test_restore_and_rewrite_legacy_v1_store_without_changing_shape(
    store, tmp_path
):
    legacy = {
        "format": "vibe.scheduled-loops/v1",
        "loops": [
            {
                "id": "legacy",
                "interval_seconds": 30,
                "prompt": "check build",
                "next_fire_at": 100.0,
                "created_at": 70.0,
            }
        ],
    }
    path = tmp_path / "loops.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    await store.restore()
    restored = await store.list()
    await store.persist()

    assert len(restored) == 1
    assert isinstance(restored[0], ScheduledPrompt)
    assert restored[0].cron is None
    assert restored[0].next_fire_at == 100
    assert json.loads(path.read_text(encoding="utf-8")) == legacy


@pytest.mark.asyncio
async def test_replace_copies_typed_scheduled_prompts(store, clock, tmp_path):
    interval = ScheduledPrompt(
        id="interval",
        interval_seconds=30,
        prompt="check build",
        next_fire_at=clock.now + 30,
        created_at=clock.now,
    )
    cron = await store.create_cron("0 9 * * *", "report")

    await store.replace([interval, cron])
    interval.prompt = "changed outside store"
    cron.prompt = "also changed outside store"
    listed = await store.list()
    assert [item.prompt for item in listed] == ["check build", "report"]
    listed[0].prompt = "changed returned copy"
    assert (await store.list())[0].prompt == "check build"
    payload = json.loads((tmp_path / "loops.json").read_text(encoding="utf-8"))
    assert "cron" not in payload["loops"][0]
    assert "interval_seconds" not in payload["loops"][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [0, -30, 29, 30.5, True, "30", None])
async def test_numeric_interval_validation_leaves_store_unchanged(store, interval):
    with pytest.raises(LoopError):
        await store.create_interval(interval, "check build")
    assert await store.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cron",
    [
        "",
        "@daily",
        "* * * *",
        "* * * * * *",
        "* * * * * * *",
        "60 * * * *",
        "0 24 * * *",
        "0 0 31 2 *",
        "0 0 30 2 *",
        "0 0 * 13 *",
        "*/0 * * * *",
        "not a valid cron expression",
    ],
)
async def test_cron_validation_rejects_invalid_and_impossible_schedules(store, cron):
    with pytest.raises(LoopError):
        await store.create_cron(cron, "check build")
    assert await store.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["", "  ", "/clear", "  /loop stop"])
@pytest.mark.parametrize("kind", ["interval", "cron"])
async def test_prompt_validation_for_both_schedule_kinds(store, prompt, kind):
    with pytest.raises(LoopError):
        if kind == "interval":
            await store.create_interval(30, prompt)
        else:
            await store.create_cron("* * * * *", prompt)
    assert await store.list() == []


@pytest.mark.parametrize(
    "schedule", [{}, {"interval_seconds": 30, "cron": "* * * * *"}]
)
def test_model_requires_exactly_one_schedule(schedule):
    with pytest.raises(ValidationError, match="Exactly one"):
        ScheduledPrompt.model_validate({
            "id": "test",
            "prompt": "test",
            "created_at": 0,
            "next_fire_at": 60,
            **schedule,
        })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schedule",
    [
        {},
        {"interval_seconds": 29},
        {"cron": "0 0 31 2 *"},
        {"interval_seconds": 30, "cron": "* * * * *"},
    ],
)
async def test_restore_validates_schedule_without_overwriting_corrupt_store(
    store, tmp_path, schedule
):
    path = tmp_path / "loops.json"
    raw = json.dumps({
        "format": "vibe.scheduled-loops/v1",
        "loops": [
            {
                "id": "test",
                "prompt": "test",
                "created_at": 0,
                "next_fire_at": 60,
                **schedule,
            }
        ],
    })
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ScheduledLoopStoreError):
        await store.restore()
    await store.persist()

    assert await store.list() == []
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.asyncio
async def test_mixed_schedules_share_fifty_entry_limit(store, clock):
    for index in range(25):
        await store.create_interval(30, f"interval {index}")
        await store.create_cron("0 9 * * *", f"cron {index}")

    with pytest.raises(LoopError, match="Loop limit reached"):
        await store.create_interval(30, "one too many")
    with pytest.raises(LoopError, match="Loop limit reached"):
        await store.create_cron("0 9 * * *", "one too many")
    assert len(await store.list()) == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["interval", "cron", "fire", "replace", "delete", "clear"]
)
async def test_cron_mutations_persist_before_changing_memory(
    store, clock, tmp_path, monkeypatch, operation
):
    created = await store.create_cron("0 9 * * *", "keep me")
    path = tmp_path / "loops.json"
    persisted = path.read_text(encoding="utf-8")

    async def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(scheduled_loops_module, "atomic_replace", fail_write)
    with pytest.raises(ScheduledLoopStoreError):
        match operation:
            case "interval":
                await store.create_interval(30, "do not retain")
            case "cron":
                await store.create_cron("0 10 * * *", "do not retain")
            case "fire":
                await store.mark_fired(created.id, now=created.next_fire_at)
            case "replace":
                await store.replace([])
            case "delete":
                await store.delete(created.id)
            case "clear":
                await store.clear()

    assert await store.list() == [created]
    assert path.read_text(encoding="utf-8") == persisted
