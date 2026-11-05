"""Following a root Session's stream and its subagents' streams as one sequence."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from mistralai_vibe_local_harness.protocol import RustEnabledAgentTools
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    Event,
    PageRequest,
    TextContentBlock,
    TurnTerminatedEvent,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionReadParams,
    TurnStartParams,
    TurnStartResponse,
)
from mistralai_vibe_local_harness.session_protocol import SessionStartParams
from mistralai_vibe_local_harness.vibe import (
    UnifiedHarnessSessionBackendHost,
    _local_actions,
)
from mistralai_vibe_local_harness.vibe._host import _core_config
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    SUBAGENT_REGISTERED,
    StreamBatch,
    VibeExtensionEvent,
)
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    LocalSessionApiTransport,
    session_api_view_of,
)
from tests.stubs.local_sandbox import ScriptedModel
from tests.stubs.subagent_programs import spawn_only
from vibe.app_server._session_api_streams import (
    REMEMBERED_EVENT_IDS,
    SessionApiStreams,
    SessionBatch,
)

_SPAWNING_TASK = "SPAWNING_TASK"
_CHILD_TASK = "CHILD_TASK"
_ALL = PageRequest(limit=1_000, direction="forward")


class _HeldChildModel:
    """The scripted model, holding each subagent's answer until released."""

    def __init__(self) -> None:
        self._model = ScriptedModel({
            _SPAWNING_TASK: [
                ("run_typescript", {"code": spawn_only("researcher", _CHILD_TASK)})
            ]
        })
        self.child_started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self, action: Any, messages: list[Any], tools: object, config: object, **_: Any
    ) -> object:
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            self.child_started.set()
            await self.release.wait()
        return await self._model(action, messages, tools, config)


class _Root:
    def __init__(self, host: Any, session: Any, model: _HeldChildModel) -> None:
        self.host = host
        self.session = session
        self.model = model
        self.transport = LocalSessionApiTransport(
            session, session_api_view_of(session), children=host
        )
        self.streams = SessionApiStreams(self.transport)

    @property
    def id(self) -> str:
        return self.session.session_id

    async def bookmark(self) -> str:
        read, _ = await self.transport.read_with_extensions(
            SessionReadParams(session_id=self.id, history=_ALL)
        )
        return read.last_event_id

    async def start_turn(self, text: str) -> TurnStartResponse:
        return await self.transport.request(
            "app_server/session/turn/start",
            TurnStartParams(session_id=self.id, message=[TextContentBlock(text=text)]),
            TurnStartResponse,
        )

    async def spawn_child(self) -> str:
        """Run the root Turn that spawns a subagent; return the subagent's ID."""
        await self.start_turn(_SPAWNING_TASK)
        await asyncio.wait_for(self.model.child_started.wait(), timeout=5)
        await self.session._wait_for_pending_turns()
        [record] = self.host.child_session_records(self.id)
        return record.child_session_id


@pytest_asyncio.fixture
async def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Root]:
    """A root Session that may spawn one subagent, held until released."""
    model = _HeldChildModel()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    config = _core_config("runtime-template")
    tools = config.settings.tools.model_copy(
        update={"subagents": RustEnabledAgentTools()}, deep=True
    )
    host = UnifiedHarnessSessionBackendHost(tmp_path / "sessions")
    host.configure_runtime(
        config.model_copy(
            update={
                "settings": config.settings.model_copy(
                    update={"tools": tools}, deep=True
                )
            },
            deep=True,
        ),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, roots=(tmp_path,), session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=20), cwd=str(tmp_path))
    state = _Root(host, session, model)
    try:
        await state.transport.open()
        yield state
    finally:
        model.release.set()
        await state.streams.close()
        await state.transport.close()
        await host.shutdown()


async def _next_until(
    streams: SessionApiStreams, done: Callable[[SessionBatch], bool]
) -> list[SessionBatch]:
    """Every batch of the sequence up to and including the first ``done`` one."""
    batches: list[SessionBatch] = []
    async with asyncio.timeout(5):
        async for item in streams:
            batches.append(item)
            if done(item):
                return batches
    raise AssertionError("the sequence ended first")


def _holds(event_id: str) -> Callable[[SessionBatch], bool]:
    return lambda item: any(event.event_id == event_id for event in item.batch.events)


def _ends_turn_of(session_id: str) -> Callable[[SessionBatch], bool]:
    return lambda item: (
        item.session_id == session_id
        and any(
            isinstance(event, Event) and isinstance(event.payload, TurnTerminatedEvent)
            for event in item.batch.events
        )
    )


def _registers_subagent(item: SessionBatch) -> bool:
    return any(
        isinstance(event, VibeExtensionEvent) and event.type == SUBAGENT_REGISTERED
        for event in item.batch.events
    )


def _ids(batches: list[SessionBatch]) -> list[list[str]]:
    return [[event.event_id for event in item.batch.events] for item in batches]


async def _direct_batches(
    transport: LocalSessionApiTransport, session_id: str, after: str, last: str
) -> list[StreamBatch]:
    """The batches ``read_stream`` itself gives after ``after``, up to ``last``."""
    batches: list[StreamBatch] = []
    stream = transport.read_stream(
        EventsReadParams(session_id=session_id, after_event_id=after)
    )
    async with asyncio.timeout(5):
        async for batch in stream:
            batches.append(batch)
            if any(event.event_id == last for event in batch.events):
                break
    await stream.aclose()  # type: ignore[attr-defined]
    return batches


@pytest.mark.asyncio
async def test_root_batches_arrive_in_order_and_wait_for_observation(
    root: _Root,
) -> None:
    """*Prepare*: Streams started at the root's bookmark, and a Turn started,
        whose response names its last event.
    *Do*: Wait for that event while reading the sequence up to the batch that
        holds it, then mark the batches observed.
    *Assert*: The sequence gives the root's batches in ``read_stream``'s own
        order; the wait is still pending once the batch arrived, and returns
        once the batch is observed.
    """
    # Prepare
    bookmark = await root.bookmark()
    await root.streams.start(bookmark)
    response = await root.start_turn("Hello")
    assert response.last_event_id is not None
    waiter = asyncio.create_task(
        root.streams.wait_observed(root.id, response.last_event_id)
    )

    # Do
    batches = await _next_until(root.streams, _holds(response.last_event_id))
    await asyncio.sleep(0.05)
    pending = not waiter.done()
    for item in batches:
        root.streams.observed(
            item.session_id, [event.event_id for event in item.batch.events]
        )
    await asyncio.wait_for(waiter, timeout=1)

    # Assert
    direct = await _direct_batches(
        root.transport, root.id, bookmark, response.last_event_id
    )
    assert {item.session_id for item in batches} == {root.id}
    assert _ids(batches) == [
        [event.event_id for event in batch.events] for batch in direct
    ]
    assert pending


@pytest.mark.asyncio
async def test_is_observed_names_only_remembered_ids_of_that_session(
    root: _Root,
) -> None:
    """*Prepare*: Streams over a root, with one ID marked observed and a
        baseline marked.
    *Do*: Ask about them, about the same ID on another Session, about an
        unknown ID, and again once more IDs than are remembered were observed.
    *Assert*: Only the marked IDs of the root are observed, a wait for the
        baseline returns at once, and the oldest ID is forgotten once more
        than ``REMEMBERED_EVENT_IDS`` follow it.
    """
    # Prepare
    root.streams.observed(root.id, ["first"])
    root.streams.mark_baseline(root.id, "baseline")

    # Do
    before = (
        root.streams.is_observed(root.id, "first"),
        root.streams.is_observed(root.id, "baseline"),
        root.streams.is_observed("another-session", "first"),
        root.streams.is_observed(root.id, "unknown"),
    )
    root.streams.observed(
        root.id, [f"later-{index}" for index in range(REMEMBERED_EVENT_IDS - 2)]
    )
    kept = root.streams.is_observed(root.id, "first")
    root.streams.observed(root.id, ["one-more"])

    # Assert
    await asyncio.wait_for(root.streams.wait_observed(root.id, "baseline"), 0.1)
    assert before == (True, True, False, False)
    assert kept
    assert not root.streams.is_observed(root.id, "first")
    assert root.streams.is_observed(root.id, "baseline")
    assert root.streams.is_observed(root.id, "one-more")


@pytest.mark.asyncio
async def test_close_releases_waiters_and_ends_the_sequence(root: _Root) -> None:
    """*Prepare*: Streams started at the root's bookmark, and a wait for an ID
        no stream will deliver.
    *Do*: Close the streams.
    *Assert*: The wait returns and the sequence ends.
    """
    # Prepare
    await root.streams.start(await root.bookmark())
    waiter = asyncio.create_task(root.streams.wait_observed(root.id, "never"))
    await asyncio.sleep(0)

    # Do
    await root.streams.close()

    # Assert
    await asyncio.wait_for(waiter, timeout=1)
    async with asyncio.timeout(1):
        assert [item async for item in root.streams] == []


@pytest.mark.asyncio
async def test_start_opens_a_subagent_that_already_exists(root: _Root) -> None:
    """*Prepare*: A root whose Turn spawned a subagent that is still running.
    *Do*: Start the streams at the root's bookmark, then let the subagent end
        its Turn.
    *Assert*: ``start`` read the subagent, and the sequence carries the
        subagent's own Turn end, from its read's running Turn.
    """
    # Prepare
    child_id = await root.spawn_child()

    # Do
    await root.streams.start(await root.bookmark())
    reads = root.streams.child_reads()
    root.model.release.set()
    batches = await _next_until(root.streams, _ends_turn_of(child_id))

    # Assert
    [(read, extensions)] = reads.values()
    assert list(reads) == [child_id]
    assert read.state.session.id == child_id
    assert extensions.last_event_id == read.last_event_id
    ended = [
        event.payload.turn.id
        for item in batches
        if item.session_id == child_id
        for event in item.batch.events
        if isinstance(event, Event) and isinstance(event.payload, TurnTerminatedEvent)
    ]
    assert ended == [read.state.active_turn_id]


@pytest.mark.asyncio
async def test_a_registered_subagent_is_opened_once_by_add_child(root: _Root) -> None:
    """*Prepare*: Streams started at the root's bookmark, before any subagent.
    *Do*: Run a Turn that spawns a subagent, read the sequence up to its
        registration, add the subagent twice, then let it end its Turn.
    *Assert*: The first add reads the subagent and the second does nothing;
        the subagent's batches each arrive once, up to its Turn end.
    """
    # Prepare
    await root.streams.start(await root.bookmark())

    # Do
    child_id = await root.spawn_child()
    registered = await _next_until(root.streams, _registers_subagent)
    first = await root.streams.add_child(child_id)
    second = await root.streams.add_child(child_id)
    root.model.release.set()
    batches = await _next_until(root.streams, _ends_turn_of(child_id))
    await asyncio.sleep(0.05)
    await root.streams.close()
    batches.extend([item async for item in root.streams])

    # Assert
    announced = [
        event.data
        for event in registered[-1].batch.events
        if isinstance(event, VibeExtensionEvent) and event.type == SUBAGENT_REGISTERED
    ]
    assert announced == [{"sessionId": child_id}]
    assert first is not None
    assert first[0].state.session.id == child_id
    assert second is None
    assert list(root.streams.child_reads()) == [child_id]
    child_ids = [
        event_id
        for item in batches
        if item.session_id == child_id
        for event_id in _ids([item])[0]
    ]
    assert child_ids
    assert len(child_ids) == len(set(child_ids))


@pytest.mark.asyncio
async def test_a_failed_subagent_stream_does_not_end_the_root_sequence(
    root: _Root, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A running subagent whose stream fails as soon as it opens,
        and streams started at the root's bookmark.
    *Do*: Wait on an event of the subagent, then run another root Turn.
    *Assert*: The wait returns because the subagent was dropped, the failure
        is logged, and the root's new Turn still arrives on the sequence.
    """
    # Prepare
    child_id = await root.spawn_child()
    read_stream = root.transport.read_stream

    async def failing(params: EventsReadParams) -> AsyncIterator[StreamBatch]:
        if params.session_id == child_id:
            raise RuntimeError("child stream broke")
        async for batch in read_stream(params):
            yield batch

    with patch.object(root.transport, "read_stream", failing):
        await root.streams.start(await root.bookmark())

        # Do
        waiter = asyncio.create_task(root.streams.wait_observed(child_id, "never"))
        response = await root.start_turn("Hello")
        assert response.last_event_id is not None
        batches = await _next_until(root.streams, _holds(response.last_event_id))
        await asyncio.wait_for(waiter, timeout=1)

    # Assert
    assert {item.session_id for item in batches} == {root.id}
    assert any(
        record.exc_info is not None
        and str(record.exc_info[1]) == "child stream broke"
        and child_id in record.getMessage()
        for record in caplog.records
    )
