from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.types import LLMMessage, Role
from vibe.core.watchdog import RunState
from vibe.core.watchdog.snapshots import (
    ConversationSnapshotStore,
    SnapshotNotFoundError,
)


@pytest.mark.asyncio
async def test_snapshot_store_creates_lists_resolves_and_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConversationSnapshotStore(tmp_path / "snapshots")
    snapshot_ids = iter(["1234567", "abcdef0"])
    monkeypatch.setattr(store, "_new_id", lambda: next(snapshot_ids))
    state = RunState.new(run_id="run-1", session_id="session-1")
    messages = [LLMMessage(role=Role.user, content="first")]
    first = await store.create(
        label=None, session_id="session-1", messages=messages, watchdog_state=state
    )
    second = await store.create(
        label="parser before refactor",
        session_id="session-1",
        messages=messages,
        watchdog_state=state,
    )

    snapshots = await store.list()

    assert len(first.snapshot_id) == 7
    assert first.display_name == first.snapshot_id
    assert second.display_name == "parser before refactor"
    assert snapshots == [second, first]
    assert await store.resolve("0") == second
    assert await store.resolve(first.snapshot_id) == first
    assert await store.drop("1") == first
    assert await store.list() == [second]


@pytest.mark.asyncio
async def test_snapshot_store_clear_and_missing_reference(tmp_path: Path) -> None:
    store = ConversationSnapshotStore(tmp_path / "snapshots")
    state = RunState.new(run_id="run-1", session_id="session-1")
    await store.create(
        label=None, session_id="session-1", messages=[], watchdog_state=state
    )

    assert await store.clear() == 1
    assert await store.clear() == 0
    with pytest.raises(SnapshotNotFoundError):
        await store.resolve("0")


@pytest.mark.asyncio
async def test_agent_loop_restores_conversation_into_forked_session(agent_loop) -> None:
    original_session = agent_loop.session_id
    saved = [LLMMessage(role=Role.user, content="saved point")]
    agent_loop.messages.reset([
        *saved,
        LLMMessage(role=Role.assistant, content="later work"),
    ])

    await agent_loop.restore_conversation_snapshot(saved)

    assert list(agent_loop.messages) == saved
    assert agent_loop.session_id != original_session
    assert agent_loop.parent_session_id == original_session
