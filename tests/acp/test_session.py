from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from vibe.acp.session import AcpSessionLoop


def _make_session() -> AcpSessionLoop:
    return AcpSessionLoop(
        id="test-session", agent_loop=MagicMock(), command_registry=MagicMock()
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


class TestPromptTask:
    @pytest.mark.asyncio
    async def test_set_prompt_task_tracks_task(self) -> None:
        session = _make_session()

        async def work() -> None:
            pass

        task = session.set_prompt_task(work())
        assert session.prompt_task is task
        await task
        assert session.prompt_task is None

    @pytest.mark.asyncio
    async def test_cancel_prompt_cancels_active_task(self) -> None:
        session = _make_session()

        async def hang() -> None:
            await asyncio.Event().wait()

        task = session.set_prompt_task(hang())
        await session.cancel_prompt()
        assert task.cancelled()
        assert session.prompt_task is None

    @pytest.mark.asyncio
    async def test_cancel_prompt_is_noop_without_task(self) -> None:
        session = _make_session()
        await session.cancel_prompt()

    @pytest.mark.asyncio
    async def test_cancel_prompt_does_not_cancel_background_tasks(self) -> None:
        session = _make_session()
        gate = asyncio.Event()

        async def bg() -> None:
            await gate.wait()

        bg_task = session.spawn(bg())
        assert bg_task is not None

        async def hang() -> None:
            await asyncio.Event().wait()

        session.set_prompt_task(hang())
        await session.cancel_prompt()

        assert not bg_task.cancelled()
        gate.set()
        await bg_task


class TestClose:
    @pytest.mark.asyncio
    async def test_close_cancels_all_tasks(self) -> None:
        session = _make_session()

        async def hang() -> None:
            await asyncio.Event().wait()

        bg = session.spawn(hang())
        prompt = session.set_prompt_task(hang())

        assert bg is not None

        await session.close()

        assert bg.cancelled()
        assert prompt.cancelled()
        assert session.prompt_task is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        session = _make_session()
        await session.close()
        await session.close()

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
