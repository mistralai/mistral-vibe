from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from tests.stubs.app_server import FakeSessionBackendServices
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._session_follower import EndedTurn, SessionFollowerConsumer
from vibe.app_server.models import (
    CompletedEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicTurnStatus,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
    TurnStartParams,
)
from vibe.app_server.session import AppServerSession

pytestmark = pytest.mark.both_paths

_TIMEOUT_S = 10


class _Recorder(SessionFollowerConsumer):
    """Everything a consumer hears, with a way to wait for Turn ends."""

    def __init__(self) -> None:
        self.entries: list[PublicHistoryEntry] = []
        self.turn_ends: list[EndedTurn] = []
        self.resets = 0
        self._changed = asyncio.Event()

    def entry_reported(self, entry: PublicHistoryEntry) -> None:
        self.entries.append(entry)
        self._wake()

    def turn_ended(self, ended: EndedTurn) -> None:
        self.turn_ends.append(ended)
        self._wake()

    def history_reset(self) -> None:
        self.resets += 1
        self._wake()

    async def wait_for(self, done: Callable[[], bool]) -> None:
        async def wait() -> None:
            while not done():
                changed = self._changed
                await changed.wait()

        await asyncio.wait_for(wait(), timeout=_TIMEOUT_S)

    def completed(self) -> list[PublicHistoryEntry]:
        return [
            entry
            for entry in self.entries
            if entry.generation_status is PublicEntryGenerationStatus.COMPLETED
        ]

    def _wake(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()


class _Failing(SessionFollowerConsumer):
    def __init__(self) -> None:
        self.calls = 0

    def entry_reported(self, entry: PublicHistoryEntry) -> None:
        self.calls += 1
        raise RuntimeError("consumer failed on an entry")

    def turn_ended(self, ended: EndedTurn) -> None:
        self.calls += 1
        raise RuntimeError("consumer failed on a Turn end")


def _texts(entries: list[PublicHistoryEntry], role: str) -> list[str]:
    return [
        entry.text
        for entry in entries
        if isinstance(entry, PublicMessageEntry) and entry.role == role
    ]


@pytest.mark.asyncio
async def test_a_turn_no_client_follows_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    process_services: ProcessServices,
    tmp_path: Path,
) -> None:
    """*Prepare*: A session opened with no client subscribed to it, and a model
    that reads a file, then answers.
    *Do*: Run one Turn.
    *Assert*: The follower reports the user message, the completed tool call with
    its output, the completed answer, and the Turn's end, once.
    """
    # Prepare
    (tmp_path / "notes.txt").write_text("from file\n", encoding="utf-8")
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "read-1",
                        "index": 0,
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({
                                "path": str(tmp_path / "notes.txt")
                            }),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("read done"),
        ]
    )
    process = HarnessProcess(services=process_services)
    monkeypatch.setattr(
        process.connector_catalog, "resolve_catalog", AsyncMock(return_value=None)
    )
    host = cast(
        Any,
        process.create_session_backend_host(cast(Any, FakeSessionBackendServices())),
    )
    recorder = _Recorder()
    try:
        started = await host.start(
            SessionStartParams(
                agent_config=SessionOptions(
                    cwd=str(tmp_path), enabled_tools=["read_file"], auto_approve=True
                )
            )
        )
        backend = started.backend
        backend.session_follower.add_consumer(recorder)

        # Do
        result = await backend.start_turn(
            TurnStartParams(
                session_id=backend.session_id,
                message=[TextContentBlock(text="read notes")],
            )
        )
        if result.after_response is not None:
            result.after_response()
        await recorder.wait_for(lambda: bool(recorder.turn_ends))
    finally:
        await host.shutdown()
        await process.close()

    # Assert
    completed = recorder.completed()
    assert _texts(completed, "user") == ["read notes"]
    assert _texts(completed, "assistant") == ["read done"]
    effects = [entry for entry in completed if isinstance(entry, PublicEffectEntry)]
    assert len(effects) == 1
    assert isinstance(effects[0].state, CompletedEffectState)
    # Vibe's projection moves a read's text from ``output_text`` to ``output``.
    assert effects[0].state.output or effects[0].state.output_text
    (ended,) = recorder.turn_ends
    assert ended.turn is not None
    assert ended.turn.id == ended.turn_id
    assert ended.turn.status is PublicTurnStatus.COMPLETED


@pytest.mark.asyncio
async def test_two_subscriptions_hear_each_update_once(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A session its client follows, and a second subscription to it.
    *Do*: Run one Turn.
    *Assert*: The follower reports each entry and the Turn end once.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("Hello")
    )
    session = backend_contract_session
    (backend,) = opened_backends
    recorder = _Recorder()
    backend.session_follower.add_consumer(recorder)
    second = await backend.subscribe(SessionReadParams(session_id=session.session_id))

    async def drain() -> None:
        async for _event in second.events:
            pass

    draining = asyncio.create_task(drain())
    try:
        # Do
        _ = [event async for event in session.act("Hi")]
        await recorder.wait_for(lambda: bool(recorder.turn_ends))
    finally:
        draining.cancel()
        with contextlib.suppress(BaseException):
            await draining

    # Assert
    reported = [(entry.id, entry.generation_status) for entry in recorder.entries]
    assert len(reported) == len(set(reported))
    assert _texts(recorder.completed(), "assistant") == ["Hello"]
    assert len(recorder.turn_ends) == 1


@pytest.mark.asyncio
async def test_a_compaction_checkpoint_is_reported(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A session with one Turn.
    *Do*: Compact it.
    *Assert*: The follower reports the completed compaction checkpoint.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before compaction"),
            backend_contract_mistral_response("<summary>First turn</summary>"),
        ]
    )
    session = backend_contract_persistent_session
    (backend,) = opened_backends
    recorder = _Recorder()
    backend.session_follower.add_consumer(recorder)
    _ = [event async for event in session.act("first question")]

    def compacted() -> bool:
        return any(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
            for entry in recorder.completed()
        )

    # Do
    await session.compact()
    await recorder.wait_for(compacted)

    # Assert
    checkpoints = [
        entry.id
        for entry in recorder.completed()
        if isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
    ]
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_a_turn_a_queued_turn_follows_is_reported_ended(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A running Turn, and a prompt queued behind it.
    *Do*: Let the running Turn end, so the queued one starts right away.
    *Assert*: The follower reports both Turns ended, each once.
    """
    # Prepare
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_gated_mistral_response(
                "first", started=started, release=release
            ),
            backend_contract_mistral_response("second"),
        ]
    )
    session = backend_contract_session
    (backend,) = opened_backends
    recorder = _Recorder()
    backend.session_follower.add_consumer(recorder)
    await session.enqueue("first")
    await asyncio.wait_for(started.wait(), timeout=_TIMEOUT_S)
    await session.enqueue("second")

    # Do
    release.set()
    await recorder.wait_for(lambda: len(recorder.turn_ends) >= 2)

    # Assert
    turn_ids = [ended.turn_id for ended in recorder.turn_ends]
    assert len(turn_ids) == 2
    assert len(set(turn_ids)) == 2
    assert _texts(recorder.completed(), "assistant") == ["first", "second"]


@pytest.mark.asyncio
async def test_an_in_place_rewind_resets_the_history(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A session with two Turns.
    *Do*: Rewind in place to the second, then run another Turn.
    *Assert*: The follower reports one history reset, then the new Turn's
    entries and end.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("answer")
    )
    session = backend_contract_persistent_session
    (backend,) = opened_backends
    recorder = _Recorder()
    backend.session_follower.add_consumer(recorder)
    _ = [event async for event in session.act("First", client_message_id="u-1")]
    _ = [event async for event in session.act("Second", client_message_id="u-2")]
    await recorder.wait_for(lambda: len(recorder.turn_ends) == 2)

    # Do
    await session.resources.sessions.rewind("u-2", restore_files=False, inplace=True)
    await recorder.wait_for(lambda: recorder.resets > 0)
    reported_before = len(recorder.entries)
    _ = [event async for event in session.act("Third", client_message_id="u-3")]
    await recorder.wait_for(lambda: len(recorder.turn_ends) == 3)

    # Assert
    assert recorder.resets == 1
    after_rewind = [
        entry
        for entry in recorder.entries[reported_before:]
        if entry.generation_status is PublicEntryGenerationStatus.COMPLETED
    ]
    assert _texts(after_rewind, "user") == ["Third"]
    assert _texts(after_rewind, "assistant") == ["answer"]


@pytest.mark.asyncio
async def test_a_failing_consumer_breaks_neither_the_follower_nor_the_stream(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_session: AppServerSession,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A consumer that raises on every call, registered first.
    *Do*: Run two Turns.
    *Assert*: The client hears both answers, and the next consumer every Turn end.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("one"),
            backend_contract_mistral_response("two"),
        ]
    )
    session = backend_contract_session
    (backend,) = opened_backends
    failing = _Failing()
    recorder = _Recorder()
    backend.session_follower.add_consumer(failing)
    backend.session_follower.add_consumer(recorder)

    # Do
    _ = [event async for event in session.act("first")]
    _ = [event async for event in session.act("second")]
    await recorder.wait_for(lambda: len(recorder.turn_ends) == 2)

    # Assert
    assert failing.calls > 0
    assert _texts(list(session.history), "assistant") == ["one", "two"]
    assert _texts(recorder.completed(), "assistant") == ["one", "two"]
