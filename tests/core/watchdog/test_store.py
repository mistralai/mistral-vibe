from __future__ import annotations

import os
from pathlib import Path

import pytest

from vibe.core.watchdog import (
    EventKind,
    RunState,
    WatchdogEvent,
    WatchdogPaths,
    WatchdogStorageError,
    WatchdogStore,
    apply_event,
)


def event(sequence: int, *, payload: dict | None = None) -> WatchdogEvent:
    return WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=sequence,
        observed_at_monotonic=float(sequence),
        kind=EventKind.RUN_STARTED,
        payload=payload or {},
    )


@pytest.mark.asyncio
async def test_persisted_events_replay_to_authoritative_state(tmp_path: Path) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    state = RunState.new(run_id="run-1", session_id="session-1")
    first = event(1)
    state = apply_event(state, first)

    await store.persist(first, state)

    assert await store.load_state() == state
    assert await store.replay() == state


@pytest.mark.asyncio
async def test_interrupted_replace_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    state = RunState.new(run_id="run-1", session_id="session-1")
    first = event(1)
    state = apply_event(state, first)
    await store.persist(first, state)
    previous = state
    second = event(2)
    state = apply_event(state, second)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(WatchdogStorageError):
        await store.persist(second, state)

    assert await store.load_state() == previous


@pytest.mark.asyncio
async def test_audit_artifact_redacts_secret_payload(tmp_path: Path) -> None:
    paths = WatchdogPaths.for_run("run-1", root=tmp_path)
    store = WatchdogStore(paths)
    item = event(1, payload={"api_key": "private-value", "command": "pwd"})
    state = apply_event(RunState.new(run_id="run-1", session_id="session-1"), item)

    await store.persist(item, state)

    audit = paths.events.read_text()
    assert "private-value" not in audit
    assert "[REDACTED]" in audit
