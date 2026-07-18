from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from vibe.core.types import AssistantEvent, ToolResultEvent
from vibe.core.watchdog import (
    EventKind,
    ObserveOnlySupervisor,
    WatchdogPaths,
    WatchdogStore,
    observe_stream,
)


async def fake_turn() -> AsyncGenerator[AssistantEvent | ToolResultEvent, None]:
    yield AssistantEvent(content="checking", message_id="message-1")
    yield ToolResultEvent(
        tool_name="bash",
        tool_class=None,
        tool_call_id="call-1",
        result=None,
        error=None,
    )


@pytest.mark.asyncio
async def test_fake_turn_produces_replayable_observe_only_timeline(
    tmp_path: Path,
) -> None:
    paths = WatchdogPaths.for_run("run-1", root=tmp_path)
    store = WatchdogStore(paths)
    supervisor = ObserveOnlySupervisor(
        run_id="run-1", session_id="session-1", store=store
    )

    observed = [event async for event in observe_stream(fake_turn(), supervisor)]
    persisted = await store.load_events()

    assert len(observed) == 2
    assert [event.kind for event in persisted] == [
        EventKind.RUN_STARTED,
        EventKind.MODEL_ACTIVITY,
        EventKind.TOOL_FINISHED,
        EventKind.RUN_FINISHED,
    ]
    assert await store.replay() == await store.load_state()


@pytest.mark.asyncio
async def test_agent_loop_emits_replayable_observe_only_timeline(
    tmp_path: Path, agent_loop
) -> None:
    paths = WatchdogPaths.for_run("run-2", root=tmp_path)
    store = WatchdogStore(paths)
    supervisor = ObserveOnlySupervisor(
        run_id="run-2", session_id=agent_loop.session_id, store=store
    )
    agent_loop.set_event_observer(supervisor)

    emitted = [event async for event in agent_loop.act("hello")]
    persisted = await store.load_events()

    assert emitted
    assert persisted[0].kind == EventKind.RUN_STARTED
    assert persisted[-1].kind == EventKind.RUN_FINISHED
    assert await store.replay() == await store.load_state()
