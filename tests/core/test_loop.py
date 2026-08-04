from __future__ import annotations

import math
from typing import cast

import pytest

from vibe.core.loop import (
    MAX_LOOPS_PER_SESSION,
    MIN_INTERVAL_SECONDS,
    LoopError,
    LoopManager,
    ScheduledLoop,
    parse_interval,
)
from vibe.core.session.session_logger import SessionLogger


class FakeMetadata:
    def __init__(self) -> None:
        self.loops: list[ScheduledLoop] = []


class FakeSessionLogger:
    def __init__(self) -> None:
        self.session_metadata = FakeMetadata()
        self.persisted: list[list[ScheduledLoop]] = []

    async def persist_loops(self) -> None:
        self.persisted.append([*self.session_metadata.loops])


class RaisingSessionLogger:
    def __init__(self) -> None:
        self.session_metadata = FakeMetadata()

    async def persist_loops(self) -> None:
        raise RuntimeError("disk on fire")


def _build_manager() -> tuple[LoopManager, FakeSessionLogger]:
    fake = FakeSessionLogger()
    return LoopManager(cast(SessionLogger, fake)), fake


class TestParseInterval:
    def test_parses_supported_units(self) -> None:
        assert parse_interval("30s") == 30
        assert parse_interval("5m") == 300
        assert parse_interval("2h") == 7200
        assert parse_interval("1d") == 86400

    def test_parses_case_insensitively(self) -> None:
        assert parse_interval("30S") == 30
        assert parse_interval("2H") == 7200

    def test_strips_whitespace(self) -> None:
        assert parse_interval("  30s  ") == 30

    @pytest.mark.parametrize("bad", ["", "5", "5x", "-5m", "5 m", "1.5m", "abc"])
    def test_rejects_invalid_inputs(self, bad: str) -> None:
        with pytest.raises(LoopError):
            parse_interval(bad)

    def test_rejects_below_minimum(self) -> None:
        with pytest.raises(LoopError, match=f"at least {MIN_INTERVAL_SECONDS}"):
            parse_interval("29s")


class TestLoopManagerMutations:
    @pytest.mark.asyncio
    async def test_create_persists_and_returns_loop(self) -> None:
        manager, fake = _build_manager()
        loop = await manager.create("1m", "hello world")

        assert len(manager.loops) == 1
        assert loop.interval_seconds == 60
        assert loop.prompt == "hello world"
        assert len(fake.persisted) == 1
        assert fake.persisted[0][0].id == loop.id

    @pytest.mark.asyncio
    async def test_create_rejects_bad_interval_without_persisting(self) -> None:
        manager, fake = _build_manager()

        with pytest.raises(LoopError, match="Invalid interval"):
            await manager.create("nope", "hello")

        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_create_rejects_empty_prompt_without_persisting(self) -> None:
        manager, fake = _build_manager()

        with pytest.raises(LoopError, match="Missing prompt"):
            await manager.create("30s", "   ")

        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_create_rejects_prompt_starting_with_slash(self) -> None:
        manager, fake = _build_manager()

        with pytest.raises(LoopError, match="cannot start with '/'"):
            await manager.create("30s", "/config")

        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_create_rejects_over_limit(self) -> None:
        manager, _ = _build_manager()
        for i in range(MAX_LOOPS_PER_SESSION):
            await manager.create("30s", f"prompt {i}")

        with pytest.raises(LoopError, match="limit"):
            await manager.create("30s", "overflow")

        assert len(manager.loops) == MAX_LOOPS_PER_SESSION

    @pytest.mark.asyncio
    async def test_delete_rejects_unknown_id_without_persisting(self) -> None:
        manager, fake = _build_manager()
        await manager.create("30s", "ping")
        fake.persisted.clear()

        with pytest.raises(LoopError, match="deadbeef"):
            await manager.delete("deadbeef")

        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_delete_returns_removed_loop_and_persists(self) -> None:
        manager, fake = _build_manager()
        created = await manager.create("30s", "ping")
        loop_id = manager.loops[0].id
        fake.persisted.clear()

        deleted = await manager.delete(loop_id)

        assert deleted is created
        assert manager.loops == []
        assert fake.persisted == [[]]

    @pytest.mark.asyncio
    async def test_clear_returns_count_and_persists(self) -> None:
        manager, fake = _build_manager()
        await manager.create("30s", "a")
        await manager.create("30s", "b")
        fake.persisted.clear()

        count = await manager.clear()

        assert count == 2
        assert manager.loops == []
        assert fake.persisted == [[]]


class TestLoopManagerPopDue:
    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self) -> None:
        manager, fake = _build_manager()
        assert await manager.pop_due() is None
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_due(self) -> None:
        manager, fake = _build_manager()
        await manager.create("30s", "ping")
        fake.persisted.clear()
        result = await manager.pop_due(now=0.0)
        assert result is None
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_returns_due_loop_advances_and_persists(self) -> None:
        manager, fake = _build_manager()
        await manager.create("30s", "ping")
        fake.persisted.clear()
        loop = manager.loops[0]
        original_next = loop.next_fire_at
        due = await manager.pop_due(now=original_next + 100.0)
        assert due is not None
        assert due.id == loop.id
        assert manager.loops[0].next_fire_at == original_next + 100.0 + 30.0
        assert len(fake.persisted) == 1


class TestLoopManagerNextDueIn:
    def test_inf_when_empty(self) -> None:
        manager, _ = _build_manager()
        assert manager.next_due_in() == math.inf

    @pytest.mark.asyncio
    async def test_correct_delta_when_populated(self) -> None:
        manager, _ = _build_manager()
        await manager.create("30s", "ping")
        loop = manager.loops[0]
        delta = manager.next_due_in(now=loop.next_fire_at - 7.0)
        assert delta == pytest.approx(7.0)

    @pytest.mark.asyncio
    async def test_zero_when_overdue(self) -> None:
        manager, _ = _build_manager()
        await manager.create("30s", "ping")
        loop = manager.loops[0]
        delta = manager.next_due_in(now=loop.next_fire_at + 100.0)
        assert delta == 0.0


class TestLoopManagerRestore:
    def test_replaces_in_memory_list_without_persist(self) -> None:
        manager, fake = _build_manager()
        loops = [
            ScheduledLoop(
                id="aabbccdd",
                interval_seconds=30,
                prompt="x",
                next_fire_at=1.0,
                created_at=0.0,
            ),
            ScheduledLoop(
                id="11223344",
                interval_seconds=60,
                prompt="y",
                next_fire_at=2.0,
                created_at=0.0,
            ),
        ]
        manager.restore(loops)
        assert [loop.id for loop in manager.loops] == ["aabbccdd", "11223344"]
        assert fake.persisted == []


class TestLoopManagerPersisterErrors:
    @pytest.mark.asyncio
    async def test_persister_exception_does_not_propagate(self) -> None:
        manager = LoopManager(cast(SessionLogger, RaisingSessionLogger()))
        result = await manager.create("30s", "ping")
        assert result.prompt == "ping"
        assert len(manager.loops) == 1
        loop = manager.loops[0]
        due = await manager.pop_due(now=loop.next_fire_at + 1.0)
        assert due is not None
