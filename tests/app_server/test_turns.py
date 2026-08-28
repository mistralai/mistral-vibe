from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import threading

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import (
    attach_test_app_server_session,
    build_test_app_server,
    legacy_backend,
)
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import (
    HistoryEntryAdded,
    SessionSnapshot,
    StatsUpdated,
    TurnRetrying,
)
from vibe.app_server.models import PublicRetryCategory
from vibe.app_server.transport import memory_transport_pair
from vibe.core.types import AssistantEvent, UserMessageEvent
from vibe.core.utils import RetryReason


@pytest.mark.asyncio
async def test_retry_state_survives_unrelated_updates_until_generation_resumes() -> (
    None
):
    retry_started = asyncio.Event()
    resume_generation = asyncio.Event()
    finish_turn = asyncio.Event()
    agent_loop = build_test_agent_loop()

    async def retrying_act(message: str, *, turn_options, **_kwargs):
        assert turn_options.retry_sink is not None
        await turn_options.retry_sink(RetryReason.from_http_status(429))
        retry_started.set()
        await resume_generation.wait()
        yield UserMessageEvent(content=message, message_id="user-1")
        await finish_turn.wait()
        yield AssistantEvent(content="done", message_id="assistant-1")

    agent_loop.act = retrying_act
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(agent_loop, server_transport)
    session = await attach_test_app_server_session(
        AppServerClient(client_transport, run_peer=server.serve)
    )
    events = session.act("hello")
    retry_snapshot_task = asyncio.create_task(_next_event(events, SessionSnapshot))
    turns = legacy_backend(server).session.turns

    try:
        await asyncio.wait_for(retry_started.wait(), timeout=1)
        retry_snapshot = await asyncio.wait_for(retry_snapshot_task, timeout=1)
        legacy_retry = await _next_event(events, TurnRetrying)
        retrying = turns.retrying
        active_turn = turns.active_turn
        assert retrying is not None
        assert active_turn is not None
        assert retrying.turn_id == active_turn.id
        assert retrying.category is PublicRetryCategory.RATE_LIMITED
        assert retrying.detail == "HTTP 429"
        assert retry_snapshot.state.retrying == retrying
        assert session.state.retrying == retrying
        assert legacy_retry.params.category is PublicRetryCategory.RATE_LIMITED
        assert legacy_retry.params.detail == "HTTP 429"

        await turns._emit_stats()
        await turns._emit_status("running")
        stats = await _next_event(events, StatsUpdated)
        assert stats.params.event_id == retry_snapshot.state.event_id + 1
        assert turns.retrying == retrying
        assert session.state.retrying == retrying

        resume_generation.set()
        resumed = await _next_event(events, SessionSnapshot)
        assert resumed.state.retrying is None
        assert resumed.state.event_id > stats.params.event_id
        assert turns.retrying is None
        await _next_event(events, HistoryEntryAdded)

        finish_turn.set()
        await _consume(events)
    finally:
        resume_generation.set()
        finish_turn.set()
        if not retry_snapshot_task.done():
            retry_snapshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await retry_snapshot_task
        await events.aclose()
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_interrupt_clears_retry_state() -> None:
    retry_started = asyncio.Event()
    agent_loop = build_test_agent_loop()

    async def retrying_act(*_args, turn_options, **_kwargs):
        retry_sink = turn_options.retry_sink
        assert retry_sink is not None
        loop = asyncio.get_running_loop()

        def emit_retry() -> None:
            async def report_retry() -> None:
                await retry_sink(RetryReason.from_http_status(503))

            future = asyncio.run_coroutine_threadsafe(report_retry(), loop)
            future.result(timeout=1)

        await asyncio.to_thread(emit_retry)
        retry_started.set()
        await asyncio.Event().wait()
        yield AssistantEvent(content="unreachable", message_id="assistant-1")

    agent_loop.act = retrying_act
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(agent_loop, server_transport)
    session = await attach_test_app_server_session(
        AppServerClient(client_transport, run_peer=server.serve)
    )
    events = session.act("hello")
    retry_snapshot_task = asyncio.create_task(_next_event(events, SessionSnapshot))
    turns = legacy_backend(server).session.turns

    try:
        await asyncio.wait_for(retry_started.wait(), timeout=1)
        retry_snapshot = await asyncio.wait_for(retry_snapshot_task, timeout=1)
        await _next_event(events, TurnRetrying)
        assert turns.retrying is not None
        assert retry_snapshot.state.retrying == turns.retrying

        await session.interrupt()
        cleared = await _next_event(events, SessionSnapshot)
        assert cleared.state.retrying is None
        assert turns.retrying is None
        await _consume(events)
    finally:
        if not retry_snapshot_task.done():
            retry_snapshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await retry_snapshot_task
        await events.aclose()
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_retry_from_interrupted_turn_is_not_applied_to_next_turn() -> None:
    first_turn_started = asyncio.Event()
    second_turn_started = asyncio.Event()
    release_late_retry = threading.Event()
    late_retry_finished = asyncio.Event()
    finish_second_turn = asyncio.Event()

    class DelayedRetryBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0
            self.late_retry_task: asyncio.Task[None] | None = None

        async def complete(self, **_kwargs):
            self.call_count += 1
            if self.call_count == 1:
                loop = asyncio.get_running_loop()

                def emit_late_retry() -> None:
                    release_late_retry.wait()

                    async def report_retry() -> None:
                        assert self.on_retry is not None
                        await self.on_retry(RetryReason.from_http_status(503))

                    future = asyncio.run_coroutine_threadsafe(report_retry(), loop)
                    future.result(timeout=1)
                    loop.call_soon_threadsafe(late_retry_finished.set)

                self.late_retry_task = asyncio.create_task(
                    asyncio.to_thread(emit_late_retry)
                )
                first_turn_started.set()
                await asyncio.Event().wait()
                return mock_llm_chunk(content="unreachable")

            second_turn_started.set()
            await finish_second_turn.wait()
            return mock_llm_chunk(content="done")

    backend = DelayedRetryBackend()
    agent_loop = build_test_agent_loop(backend=backend)
    backend.on_retry = agent_loop.notice_retry
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(agent_loop, server_transport)
    session = await attach_test_app_server_session(
        AppServerClient(client_transport, run_peer=server.serve)
    )
    turns = legacy_backend(server).session.turns
    first_events = session.act("first")
    first_consumer = asyncio.create_task(_consume(first_events))
    second_events: AsyncIterator[object] | None = None
    second_consumer: asyncio.Task[list[object]] | None = None

    try:
        await asyncio.wait_for(first_turn_started.wait(), timeout=1)
        await session.interrupt()
        await asyncio.wait_for(first_consumer, timeout=1)
        assert turns.retrying is None

        second_events = session.act("second")
        second_consumer = asyncio.create_task(_consume(second_events))
        await asyncio.wait_for(second_turn_started.wait(), timeout=1)

        release_late_retry.set()
        await asyncio.wait_for(late_retry_finished.wait(), timeout=1)

        assert turns.retrying is None
        assert session.state.retrying is None

        finish_second_turn.set()
        await asyncio.wait_for(second_consumer, timeout=1)
    finally:
        release_late_retry.set()
        finish_second_turn.set()
        if not first_consumer.done():
            first_consumer.cancel()
            with suppress(asyncio.CancelledError):
                await first_consumer
        if second_consumer is not None and not second_consumer.done():
            second_consumer.cancel()
            with suppress(asyncio.CancelledError):
                await second_consumer
        if backend.late_retry_task is not None:
            if not backend.late_retry_task.done():
                backend.late_retry_task.cancel()
            with suppress(asyncio.CancelledError):
                await backend.late_retry_task
        await first_events.aclose()
        if second_events is not None:
            await second_events.aclose()
        await session.close()
        await agent_loop.aclose()


async def _consume(events: AsyncIterator[object]) -> list[object]:
    return [event async for event in events]


async def _next_event[EventT](
    events: AsyncIterator[object], event_type: type[EventT]
) -> EventT:
    async for event in events:
        if isinstance(event, event_type):
            return event
    raise AssertionError(f"Event stream ended before {event_type.__name__}")
