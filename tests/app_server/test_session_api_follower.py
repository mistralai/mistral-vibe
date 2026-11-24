"""The Session follower's feed on the Session API path, over a real transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    TextContentBlock,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionReadParams,
    TurnStartParams,
    TurnStartResponse,
)
from mistralai_vibe_local_harness.session_protocol import SessionStartParams
from mistralai_vibe_local_harness.vibe import UnifiedHarnessSessionBackendHost
from mistralai_vibe_local_harness.vibe._host import _core_config
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._session_api_events import (
    SessionApiEventGapError,
)
from tests.app_server._session_api_harness import use_answering_model
from vibe.app_server import _session_api_follower as feed_module
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._session_api_follower import SessionApiFeed
from vibe.app_server._session_follower import (
    EndedTurn,
    SessionFollower,
    SessionFollowerConsumer,
)
from vibe.app_server.models import PublicTurn, PublicTurnStatus

_TIMEOUT_S = 5


class _Recorder(SessionFollowerConsumer):
    def __init__(self) -> None:
        self.turn_ends: list[EndedTurn] = []
        self.resets = 0
        self.changed = asyncio.Event()

    def turn_ended(self, ended: EndedTurn) -> None:
        self.turn_ends.append(ended)
        self.changed.set()

    def history_reset(self) -> None:
        self.resets += 1
        self.changed.set()


def _public_turn(turn: Any) -> PublicTurn:
    return PublicTurn(
        id=turn.id,
        session_id=turn.session_id,
        status=PublicTurnStatus(str(turn.status)),
        started_at=turn.started_at,
    )


class _Commands:
    """``SessionApiCommands`` whose reads can be held, and whose stream can fail."""

    def __init__(self, commands: SessionApiCommands) -> None:
        self._commands = commands
        self.transport = self
        self.session_id = commands.transport.session_id
        self.hold_reads = False
        self.reading = asyncio.Event()
        self.release = asyncio.Event()
        self.stream_failures = 0
        self.stream_gaps = 0
        self.hold_after_read = False
        self.read_taken = asyncio.Event()
        self.answer = asyncio.Event()
        self.read_failures = 0
        self.read_bookmarks: list[str] = []
        self.mute_streams = False
        self.streams_after: list[str | None] = []

    async def ensure_open(self) -> None:
        await self._commands.ensure_open()

    async def read_with_extensions(self, params: Any) -> Any:
        if self.hold_reads:
            self.reading.set()
            await self.release.wait()
        if self.read_failures and params.history is not None:
            self.read_failures -= 1
            raise ConnectionError("read failed")
        read = await self._commands.transport.read_with_extensions(params)
        if params.history is not None:
            self.read_bookmarks.append(read[0].last_event_id)
            if self.hold_after_read:
                self.read_taken.set()
                await self.answer.wait()
        return read

    async def read_stream(self, params: Any) -> AsyncIterator[Any]:
        self.streams_after.append(params.after_event_id)
        async for batch in self._commands.transport.read_stream(params):
            if self.mute_streams:
                continue
            if self.stream_gaps:
                self.stream_gaps -= 1
                raise SessionApiEventGapError("fell behind")
            if self.stream_failures:
                self.stream_failures -= 1
                raise ConnectionError("stream lost")
            yield batch


async def _session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[UnifiedHarnessSessionBackendHost, SessionApiCommands]:
    use_answering_model(monkeypatch)
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    commands = SessionApiCommands.local(session)
    await commands.ensure_open()
    return host, commands


def _feed(commands: _Commands) -> SessionApiFeed:
    return SessionApiFeed(
        commands,  # type: ignore[arg-type]
        display_roots=lambda: (),
        public_turn=_public_turn,
    )


async def _run_turn(commands: SessionApiCommands, text: str) -> None:
    await commands.transport.request(
        "app_server/session/turn/start",
        TurnStartParams(
            session_id=commands.transport.session_id,
            message=[TextContentBlock(text=text)],
        ),
        TurnStartResponse,
    )


async def _until(recorder: _Recorder, count: int) -> None:
    async with asyncio.timeout(_TIMEOUT_S):
        while len(recorder.turn_ends) < count:
            recorder.changed.clear()
            await recorder.changed.wait()


@pytest.mark.asyncio
async def test_a_feed_closed_while_it_reads_starts_no_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A feed whose start waits on the Session read.
    *Do*: Close the follower, then let the read answer.
    *Assert*: No stream is opened.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    commands.hold_reads = True
    follower = SessionFollower(commands.session_id)
    starting = asyncio.create_task(follower.start(_feed(commands)))
    await asyncio.wait_for(commands.reading.wait(), timeout=_TIMEOUT_S)

    # Do
    await follower.close()
    commands.release.set()
    await asyncio.wait_for(starting, timeout=_TIMEOUT_S)
    await asyncio.sleep(0)

    # Assert
    assert commands.streams_after == []
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_lost_stream_resumes_where_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose stream fails on its first batch.
    *Do*: Run two Turns.
    *Assert*: The stream is read again from where it stopped, and each Turn
    end is reported once.
    """
    # Prepare
    monkeypatch.setattr(feed_module, "_RETRY_SECONDS", 0.01)
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    commands.stream_failures = 1
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))

    # Do
    await _run_turn(real, "first")
    await _until(recorder, 1)
    await _run_turn(real, "second")
    await _until(recorder, 2)

    # Assert
    assert len(commands.streams_after) >= 2
    assert commands.streams_after[0] == commands.streams_after[1]
    turn_ids = [ended.turn_id for ended in recorder.turn_ends]
    assert len(turn_ids) == 2
    assert len(set(turn_ids)) == 2
    assert all(ended.turn is not None for ended in recorder.turn_ends)
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_rewind_with_nothing_left_to_drain_resets_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session with no event since the follower read it.
    *Do*: Tell the follower that the Session was rewound.
    *Assert*: The reset is reported without waiting for the drain's time limit.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    loop = asyncio.get_running_loop()

    # Do
    started = loop.time()
    await follower.history_rewound()
    elapsed = loop.time() - started

    # Assert
    assert recorder.resets == 1
    assert elapsed < feed_module._DRAIN_SECONDS / 2
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_feed_closed_during_a_rewind_starts_no_new_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session.
    *Do*: Start a rewind whose read waits, close the follower, let the read answer.
    *Assert*: No stream is opened after the first one, and no reset is reported.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    commands.hold_reads = True
    rewinding = asyncio.ensure_future(follower.history_rewound())
    await asyncio.wait_for(commands.reading.wait(), timeout=_TIMEOUT_S)

    # Do
    await follower.close()
    commands.release.set()
    await asyncio.wait_for(rewinding, timeout=_TIMEOUT_S)

    # Assert
    assert len(commands.streams_after) == 1
    assert recorder.resets == 0
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_failed_read_after_a_rewind_is_retried_and_following_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose reads fail after a rewind.
    *Do*: Tell the follower that the Session was rewound and run a Turn while
    the reads fail, then let them answer and run another Turn.
    *Assert*: The first Turn end comes from the old stream, resumed where it
    was; after a read answers, the follower follows a new stream and hears
    the second. The reset and each Turn end are reported once.
    """
    # Prepare
    monkeypatch.setattr(feed_module, "_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(feed_module, "_RETRY_MAX_SECONDS", 0.02)
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    commands.read_failures = 1_000_000

    # Do
    await follower.history_rewound()
    await _run_turn(real, "while reads fail")
    await _until(recorder, 1)
    streams_while_failing = list(commands.streams_after)
    commands.read_failures = 0
    async with asyncio.timeout(_TIMEOUT_S):
        while len(commands.streams_after) == len(streams_while_failing):
            await asyncio.sleep(0.01)
    await _run_turn(real, "after the retry")
    await _until(recorder, 2)

    # Assert
    # While the reads failed, the old stream went on from where it was;
    # afterwards, the new stream starts at the read that answered.
    first_read, new_read = commands.read_bookmarks
    assert streams_while_failing[0] == first_read
    assert new_read not in streams_while_failing
    assert commands.streams_after[-1] == new_read
    assert recorder.resets == 1
    turn_ids = [ended.turn_id for ended in recorder.turn_ends]
    assert len(turn_ids) == len(set(turn_ids)) == 2
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_turn_between_the_old_stream_and_a_retried_read_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose read after a rewind fails, and whose
    stream then stops bringing events.
    *Do*: Run a Turn the stream does not bring, then let a read answer.
    *Assert*: The read reports the Turn's end and its answer, once.
    """
    # Prepare
    monkeypatch.setattr(feed_module, "_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(feed_module, "_RETRY_MAX_SECONDS", 0.02)
    monkeypatch.setattr(feed_module, "_DRAIN_SECONDS", 0.05)
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    answers: list[str] = []

    class _Answers(SessionFollowerConsumer):
        def entry_reported(self, entry: Any) -> None:
            if (
                getattr(entry, "role", None) == "assistant"
                and entry.generation_status == "completed"
            ):
                answers.append(entry.id)

    follower.add_consumer(recorder)
    follower.add_consumer(_Answers())
    await follower.start(_feed(commands))
    commands.read_failures = 1_000_000
    await follower.history_rewound()
    commands.mute_streams = True

    # Do
    await _run_turn(real, "unheard")
    await real.transport.read_with_extensions(
        SessionReadParams(session_id=commands.session_id)
    )
    await asyncio.sleep(0.1)
    heard_before_read = len(recorder.turn_ends)
    commands.read_failures = 0
    await _until(recorder, 1)

    # Assert
    assert heard_before_read == 0
    assert len(recorder.turn_ends) == 1
    assert len(answers) == 1
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_every_turn_that_ends_while_a_rewind_reads_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose read after a rewind is held.
    *Do*: Run two Turns while the read is held, then let it answer.
    *Assert*: Both Turn ends are reported once, the older one too, though the
    read only holds the latest.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    commands.hold_reads = True
    rewinding = asyncio.ensure_future(follower.history_rewound())
    async with asyncio.timeout(_TIMEOUT_S):
        await commands.reading.wait()

    # Do
    await _run_turn(real, "first while the read waits")
    await _until(recorder, 1)
    await _run_turn(real, "second while the read waits")
    await _until(recorder, 2)
    commands.release.set()
    async with asyncio.timeout(_TIMEOUT_S):
        await rewinding

    # Assert
    turn_ids = [ended.turn_id for ended in recorder.turn_ends]
    assert len(turn_ids) == len(set(turn_ids)) == 2
    assert recorder.resets == 1
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_stream_that_fell_behind_is_picked_up_from_a_new_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose stream reports, once, that the
    follower fell behind the events it retains.
    *Do*: Run a Turn, then another.
    *Assert*: The follower reads the Session again and follows the stream
    from that read, and signals one history reset; both Turn ends are
    reported once.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    commands.stream_gaps = 1

    # Do
    await _run_turn(real, "while the stream falls behind")
    await _until(recorder, 1)
    await _run_turn(real, "after the new read")
    await _until(recorder, 2)

    # Assert
    assert len(commands.read_bookmarks) == 2
    assert commands.read_bookmarks[-1] in commands.streams_after
    assert recorder.resets == 1
    turn_ids = [ended.turn_id for ended in recorder.turn_ends]
    assert len(turn_ids) == len(set(turn_ids)) == 2
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_rewind_whose_caller_is_cancelled_during_the_read_is_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose read after a rewind is held.
    *Do*: Cancel the caller of the rewind (the client request) while the read
    waits, let the read answer, then run a Turn.
    *Assert*: The follower still switches to a stream from the new read,
    reports the reset, and hears the Turn's end.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    follower.add_consumer(recorder)
    await follower.start(_feed(commands))
    commands.hold_reads = True
    caller = asyncio.ensure_future(follower.history_rewound())
    await asyncio.wait_for(commands.reading.wait(), timeout=_TIMEOUT_S)

    # Do
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    commands.release.set()
    async with asyncio.timeout(_TIMEOUT_S):
        while recorder.resets == 0:
            await asyncio.sleep(0.01)
    await _run_turn(real, "after the cancelled rewind")
    await _until(recorder, 1)

    # Assert
    assert commands.streams_after[-1] == commands.read_bookmarks[-1]
    assert recorder.resets == 1
    assert len(recorder.turn_ends) == 1
    await follower.close()
    await real.close()
    await host.shutdown()


@pytest.mark.asyncio
async def test_entries_the_old_stream_brought_after_the_read_are_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A followed Session whose read after a rewind is taken, but
    answers late.
    *Do*: Run a Turn after the read was taken and before it answers, so the
    old stream brings its entries past the read's bookmark.
    *Assert*: Each entry is reported once, though the new stream follows from
    the read's bookmark and brings them again.
    """
    # Prepare
    host, real = await _session(tmp_path, monkeypatch)
    commands = _Commands(real)
    follower = SessionFollower(commands.session_id)
    recorder = _Recorder()
    reported: list[tuple[str, str]] = []

    class _Entries(SessionFollowerConsumer):
        def entry_reported(self, entry: Any) -> None:
            reported.append((entry.id, str(entry.generation_status)))

    follower.add_consumer(recorder)
    follower.add_consumer(_Entries())
    await follower.start(_feed(commands))
    commands.hold_after_read = True
    rewinding = asyncio.ensure_future(follower.history_rewound())
    await asyncio.wait_for(commands.read_taken.wait(), timeout=_TIMEOUT_S)

    # Do
    await _run_turn(real, "after the read was taken")
    await _until(recorder, 1)
    reported_by_old_stream = len(reported)
    commands.answer.set()
    await asyncio.wait_for(rewinding, timeout=_TIMEOUT_S)
    await _run_turn(real, "on the new stream")
    await _until(recorder, 2)

    # Assert
    assert reported_by_old_stream > 0
    assert commands.streams_after[-1] == commands.read_bookmarks[-1]
    assert len(reported) == len(set(reported))
    await follower.close()
    await real.close()
    await host.shutdown()
