from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.acp.session import AcpSession
from vibe.app_server.session import AppServerSession


def _make_session(*, turn_active: bool = False) -> AcpSession:
    app_server = MagicMock(spec=AppServerSession)
    app_server.turn_active = turn_active
    app_server.interrupt = AsyncMock()
    app_server.close = AsyncMock()
    return AcpSession(
        session_id="test-session",
        app_server=app_server,
        cwd=Path.cwd(),
        commands=MagicMock(),
    )


class TestSpawn:
    @pytest.mark.asyncio
    async def test_spawn_creates_task(self) -> None:
        session = _make_session()
        ran = asyncio.Event()

        async def work() -> None:
            ran.set()

        task = session.spawn(work())

        assert task is not None
        await task
        assert ran.is_set()

    @pytest.mark.asyncio
    async def test_spawn_returns_none_after_close(self) -> None:
        session = _make_session()
        await session.close()

        async def noop() -> None:
            pass

        assert session.spawn(noop()) is None

    @pytest.mark.asyncio
    async def test_spawn_tracks_multiple_tasks(self) -> None:
        session = _make_session()
        gate = asyncio.Event()

        async def wait_for_gate() -> None:
            await gate.wait()

        t1 = session.spawn(wait_for_gate())
        t2 = session.spawn(wait_for_gate())

        assert t1 is not None
        assert t2 is not None
        assert not t1.done()
        assert not t2.done()

        gate.set()
        await asyncio.gather(t1, t2)

    @pytest.mark.asyncio
    async def test_spawn_logs_task_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = _make_session()
        error = RuntimeError("boom")

        async def fail() -> None:
            raise error

        with caplog.at_level(logging.ERROR, logger="vibe"):
            task = session.spawn(fail())
            assert task is not None
            with pytest.raises(RuntimeError, match="boom"):
                await task
            await asyncio.sleep(0)

        record = caplog.records[-1]
        assert record.getMessage() == (
            "ACP background task failed session_id=test-session error=boom"
        )
        assert record.exc_info is not None
        assert record.exc_info[1] is error


class TestCancelPrompt:
    @pytest.mark.asyncio
    async def test_cancel_prompt_interrupts_active_app_server_turn(self) -> None:
        session = _make_session(turn_active=True)
        await session.cancel_prompt()
        cast(AsyncMock, session.app_server.interrupt).assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_cancel_prompt_is_noop_without_active_turn(self) -> None:
        session = _make_session()
        await session.cancel_prompt()
        cast(AsyncMock, session.app_server.interrupt).assert_not_awaited()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_cancels_all_tasks(self) -> None:
        session = _make_session()

        async def hang() -> None:
            await asyncio.Event().wait()

        bg = session.spawn(hang())

        assert bg is not None

        await session.close()

        assert bg.cancelled()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        session = _make_session()
        await session.close()
        await session.close()

    @pytest.mark.asyncio
    async def test_close_can_retry_failed_app_server_cleanup(self) -> None:
        session = _make_session()
        close = cast(AsyncMock, session.app_server.close)
        close.side_effect = [RuntimeError("cleanup failed"), None]

        with pytest.raises(RuntimeError, match="cleanup failed"):
            await session.close()

        async def noop() -> None:
            pass

        assert session.spawn(noop()) is None

        await session.close()
        await session.close()

        assert close.await_count == 2

    @pytest.mark.asyncio
    async def test_close_waits_for_task_cleanup(self) -> None:
        session = _make_session()
        cleanup_ran = asyncio.Event()

        async def task_with_cleanup() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_ran.set()
                raise

        session.spawn(task_with_cleanup())
        await asyncio.sleep(0)  # let the task start
        await session.close()

        assert cleanup_ran.is_set()
