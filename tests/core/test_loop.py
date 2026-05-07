from __future__ import annotations

import math
from typing import cast

import pytest

from vibe.core.loop import (
    MAX_LOOPS_PER_SESSION,
    MIN_INTERVAL_SECONDS,
    LoopError,
    LoopErrorResult,
    LoopListResult,
    LoopManager,
    LoopOkResult,
    ScheduledLoop,
    format_duration,
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


class TestFormatInterval:
    @pytest.mark.parametrize("text", ["30s", "5m", "2h", "1d"])
    def test_round_trip_with_parse(self, text: str) -> None:
        assert format_duration(parse_interval(text)) == text

    def test_formats_non_round_values(self) -> None:
        assert format_duration(90) == "1m30s"
        assert format_duration(180) == "3m"
        assert format_duration(7200) == "2h"
        assert format_duration(259200) == "3d"

    def test_formats_short_version(self) -> None:
        assert format_duration(90, short=True) == "1m"
        assert format_duration(180, short=True) == "3m"
        assert format_duration(190, short=True) == "3m"
        assert format_duration(8000, short=True) == "2h"
        assert format_duration(260000, short=True) == "3d"


class TestLoopManagerHandleCommand:
    @pytest.mark.asyncio
    async def test_empty_returns_loops_list(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("")
        assert isinstance(result, LoopListResult)
        assert result.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", ["list", "ls"])
    async def test_list_alias_returns_loops_list(self, verb: str) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command(verb)
        assert isinstance(result, LoopListResult)
        assert result.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_add_success_persists_and_returns_id(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("1m hello world")
        assert isinstance(result, LoopOkResult)
        assert "every 1m" in result.message
        assert "hello world" in result.message
        assert len(manager.loops) == 1
        loop = manager.loops[0]
        assert loop.interval_seconds == 60
        assert loop.prompt == "hello world"
        assert result.message.count(loop.id) >= 1
        assert len(fake.persisted) == 1
        assert fake.persisted[0][0].id == loop.id

    @pytest.mark.asyncio
    async def test_add_bad_interval_returns_error_no_persist(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("nope hello")
        assert isinstance(result, LoopErrorResult)
        assert "Invalid interval" in result.message
        assert "Usage:" not in result.message
        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_add_empty_prompt_returns_error_no_persist(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("30s   ")
        assert isinstance(result, LoopErrorResult)
        assert "Missing prompt" in result.message
        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_add_prompt_starting_with_slash_returns_error(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("30s /config")
        assert isinstance(result, LoopErrorResult)
        assert "cannot start with '/'" in result.message
        assert manager.loops == []
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_over_limit_returns_error(self) -> None:
        manager, _ = _build_manager()
        for i in range(MAX_LOOPS_PER_SESSION):
            result = await manager.handle_command(f"30s prompt {i}")
            assert isinstance(result, LoopOkResult)
        result = await manager.handle_command("30s overflow")
        assert isinstance(result, LoopErrorResult)
        assert "limit" in result.message.lower()
        assert len(manager.loops) == MAX_LOOPS_PER_SESSION

    @pytest.mark.asyncio
    async def test_cancel_missing_target_is_error_no_persist(self) -> None:
        manager, fake = _build_manager()
        result = await manager.handle_command("cancel")
        assert isinstance(result, LoopErrorResult)
        assert "Missing loop id" in result.message
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_cancel_unknown_id_is_error_no_persist(self) -> None:
        manager, fake = _build_manager()
        await manager.handle_command("30s ping")
        fake.persisted.clear()
        result = await manager.handle_command("cancel deadbeef")
        assert isinstance(result, LoopErrorResult)
        assert "deadbeef" in result.message
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_cancel_known_id_persists(self) -> None:
        manager, fake = _build_manager()
        await manager.handle_command("30s ping")
        loop_id = manager.loops[0].id
        fake.persisted.clear()
        result = await manager.handle_command(f"cancel {loop_id}")
        assert isinstance(result, LoopOkResult)
        assert loop_id in result.message
        assert "ping" in result.message
        assert manager.loops == []
        assert fake.persisted == [[]]

    @pytest.mark.asyncio
    async def test_cancel_all_persists(self) -> None:
        manager, fake = _build_manager()
        await manager.handle_command("30s a")
        await manager.handle_command("30s b")
        fake.persisted.clear()
        result = await manager.handle_command("cancel all")
        assert isinstance(result, LoopOkResult)
        assert "2 scheduled loop(s)" in result.message
        assert manager.loops == []
        assert fake.persisted == [[]]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", ["rm", "stop", "delete"])
    async def test_cancel_alias_verbs_work(self, verb: str) -> None:
        manager, _ = _build_manager()
        await manager.handle_command("30s ping")
        loop_id = manager.loops[0].id
        result = await manager.handle_command(f"{verb} {loop_id}")
        assert isinstance(result, LoopOkResult)
        assert manager.loops == []


class TestLoopManagerPopDue:
    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self) -> None:
        manager, fake = _build_manager()
        assert await manager.pop_due() is None
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_due(self) -> None:
        manager, fake = _build_manager()
        await manager.handle_command("30s ping")
        fake.persisted.clear()
        result = await manager.pop_due(now=0.0)
        assert result is None
        assert fake.persisted == []

    @pytest.mark.asyncio
    async def test_returns_due_loop_advances_and_persists(self) -> None:
        manager, fake = _build_manager()
        await manager.handle_command("30s ping")
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
        await manager.handle_command("30s ping")
        loop = manager.loops[0]
        delta = manager.next_due_in(now=loop.next_fire_at - 7.0)
        assert delta == pytest.approx(7.0)

    @pytest.mark.asyncio
    async def test_zero_when_overdue(self) -> None:
        manager, _ = _build_manager()
        await manager.handle_command("30s ping")
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
        result = await manager.handle_command("30s ping")
        assert isinstance(result, LoopOkResult)
        assert len(manager.loops) == 1
        loop = manager.loops[0]
        due = await manager.pop_due(now=loop.next_fire_at + 1.0)
        assert due is not None
