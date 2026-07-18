from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest

from vibe.core.types import AssistantEvent, ToolResultEvent
from vibe.core.watchdog import (
    EventKind,
    ObserveOnlySupervisor,
    RecoveryCoordinator,
    WatchdogPaths,
    WatchdogStore,
    observe_stream,
)
from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import IncidentState
from vibe.core.watchdog.recovery_port import RecoveryPort
from vibe.core.watchdog.runtime import attach_watchdog


class InjectionPort:
    def __init__(self) -> None:
        self.injected: list[str] = []

    async def inject_context(self, content: str) -> None:
        self.injected.append(content)


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


@pytest.mark.asyncio
async def test_supervisor_detects_recovers_and_replays_incident(tmp_path: Path) -> None:
    paths = WatchdogPaths.for_run("run-3", root=tmp_path)
    store = WatchdogStore(paths)
    port = InjectionPort()
    recovery = RecoveryCoordinator(store=store, port=cast(RecoveryPort, port))
    supervisor = ObserveOnlySupervisor(
        run_id="run-3",
        session_id="session-3",
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(threshold=2),)),
        recovery=recovery,
    )
    await supervisor.start()
    for call_id in ("a", "b"):
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.TOOL_STARTED,
                observed_at_monotonic=1,
                payload={
                    "tool_call_id": call_id,
                    "tool_name": "bash",
                    "arguments": {"cmd": "false"},
                },
                critical=True,
            )
        )
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.TOOL_FINISHED,
                observed_at_monotonic=2,
                payload={
                    "tool_call_id": call_id,
                    "result": "exit 1",
                    "repository_fingerprint": "repo-1",
                },
                critical=True,
            )
        )
    await supervisor.finish()

    state = await store.load_state()

    assert state is not None
    assert state.incident is not None
    assert state.incident.state == IncidentState.VERIFYING
    assert state.incident.epoch == 1
    assert len(port.injected) == 1
    assert await store.replay() == state


@pytest.mark.asyncio
async def test_changed_next_action_closes_recovered_incident(tmp_path: Path) -> None:
    paths = WatchdogPaths.for_run("run-verify", root=tmp_path)
    store = WatchdogStore(paths)
    port = InjectionPort()
    supervisor = ObserveOnlySupervisor(
        run_id="run-verify",
        session_id="session-verify",
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(threshold=2),)),
        recovery=RecoveryCoordinator(store=store, port=cast(RecoveryPort, port)),
    )
    await supervisor.start()
    for call_id in ("a", "b"):
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.TOOL_STARTED,
                observed_at_monotonic=1,
                payload={
                    "tool_call_id": call_id,
                    "tool_name": "bash",
                    "arguments": {"cmd": "false"},
                },
                critical=True,
            )
        )
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.TOOL_FINISHED,
                observed_at_monotonic=2,
                payload={
                    "tool_call_id": call_id,
                    "result": "exit 1",
                    "repository_fingerprint": "repo-1",
                },
                critical=True,
            )
        )
    supervisor._queue.put_nowait(
        PendingWatchdogEvent(
            kind=EventKind.TOOL_STARTED,
            observed_at_monotonic=3,
            payload={
                "tool_call_id": "c",
                "tool_name": "edit",
                "arguments": {"path": "parser.py"},
            },
            critical=True,
        )
    )
    await supervisor.finish()

    state = await store.load_state()
    events = await store.load_events()

    assert state is not None and state.incident is not None
    assert state.incident.state == IncidentState.CLOSED
    assert [event.kind for event in events].count(EventKind.RECOVERY_STARTED) == 1
    assert [event.kind for event in events].count(EventKind.VERIFICATION_FINISHED) == 1


@pytest.mark.asyncio
async def test_attached_runtime_survives_multiple_agent_turns(
    tmp_path: Path, agent_loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    runtime = attach_watchdog(
        agent_loop, objective="finish task", cwd=tmp_path, run_id="run-live"
    )

    for prompt in ("first", "second"):
        assert [event async for event in agent_loop.act(prompt)]

    persisted = await WatchdogStore(runtime.paths).load_events()

    assert [event.kind for event in persisted].count(EventKind.RUN_STARTED) == 2
    assert [event.kind for event in persisted].count(EventKind.RUN_FINISHED) == 2
