"""The Session follower's feed on the Session API path.

``SessionApiFeed`` reads the root Session once with ``session/read`` and
``vibe/session/read``, then follows its own Session API event stream from that
read's bookmark, next to the streams of client subscriptions. It reads only
the root's stream, never a subagent's, and uses Session API procedures only.

No Session API event removes history entries, so after an in-place rewind the
adapter tells the feed, which reads the Session again and follows the stream
from that read's bookmark once the old stream has caught up with it, so no
entry or Turn end in between is missed. When that read fails, the feed keeps
following the old stream where it was, and reads again later.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Final, cast

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    PageRequest,
    TurnState,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionReadParams,
)
from mistralai_vibe_local_harness.vibe._session_api_events import (
    SessionApiEventGapError,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import StreamBatch
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._session_api_session import ApiSessionState
from vibe.app_server._session_follower import (
    EndedTurn,
    EntryLedger,
    SessionFollower,
    ended_public_turn,
)
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicTurn,
)

logger = logging.getLogger(__name__)

_HISTORY_WINDOW: Final = PageRequest(limit=500, direction="backward")
_LATEST_TURN: Final = PageRequest(limit=1, direction="backward")
_RETRY_SECONDS: Final = 1.0
_RETRY_MAX_SECONDS: Final = 30.0
_REMEMBERED_EVENT_IDS: Final = 1_000
_DRAIN_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class _Read:
    state: ApiSessionState
    bookmark: str


class SessionApiFeed:
    """The root Session's own Session API event stream, for its follower."""

    def __init__(
        self,
        commands: SessionApiCommands,
        *,
        display_roots: Callable[[], Sequence[Path]],
        public_turn: Callable[[object], PublicTurn],
    ) -> None:
        self._commands = commands
        self._display_roots = display_roots
        self._public_turn = public_turn
        self._session_id = commands.transport.session_id
        self._follower: SessionFollower | None = None
        self._state: ApiSessionState | None = None
        self._ledger = EntryLedger()
        self._pump: asyncio.Task[None] | None = None
        # The IDs of the latest events applied, and a wake-up for who waits.
        self._applied: deque[str] = deque(maxlen=_REMEMBERED_EVENT_IDS)
        self._changed = asyncio.Event()
        # Held across a read and the stream it starts, so a rewind's read and
        # the stream it restarts never interleave with another.
        self._following = asyncio.Lock()
        self._rereading: asyncio.Task[None] | None = None
        # The last event the pump applied, or the bookmark it follows from.
        self._cursor: str | None = None
        self._closed = False

    async def start(self, follower: SessionFollower) -> None:
        async with self._following:
            if self._closed:
                return
            read = await self._take_read()
            if self._closed:
                return
            bookmark = self._install(read)
            state = self._state
            turn = state.latest_turn_state if state is not None else None
            if state is not None and turn is not None and turn.status == "terminated":
                follower.baseline_turn_ended(self._ended(state, turn))
            self._follower = follower
            self._follow(bookmark)

    async def history_rewound(self) -> None:
        await self._stop_rereading()
        async with self._following:
            if self._closed or self._follower is None:
                return
            if not await self._switch_to_a_new_read():
                self._rereading = asyncio.create_task(
                    self._reread_later(),
                    name=f"vibe-session-follower-reread-{self._session_id}",
                )
        follower = self._follower
        if follower is not None and not self._closed:
            follower.report_history_reset()

    async def _switch_to_a_new_read(self) -> bool:
        """Read the Session again, then follow the stream from that read.

        The old stream keeps running until it has applied every event up to
        the read's bookmark, so what happened in between (entries, and each
        Turn that ended, the latest or not) reaches consumers the usual way.
        Only when it cannot catch up in time does the read stand in for it.
        On a failed read, the old stream goes on where it was.

        Called under ``_following``. True once the new read is followed, or
        when the feed closed meanwhile.
        """
        if self._pump is None and self._cursor is not None:
            # A retried read was cut off between two streams: pick the old
            # one up again, so it can catch up.
            self._follow(self._cursor)
        try:
            read = await self._take_read()
        except Exception:
            logger.warning(
                "The Session follower could not read the rewound Session; retrying",
                extra={"session_id": self._session_id},
                exc_info=True,
            )
            return False
        if self._closed:
            return True
        caught_up = await self._drain_to(read.bookmark)
        await self._stop_pump()
        if self._closed:
            return True
        reported = self._ledger
        bookmark = self._install(read)
        if not caught_up:
            logger.warning(
                "The Session follower's stream did not catch up with a read",
                extra={"session_id": self._session_id},
            )
        self._report_unseen(reported)
        # The old stream may have brought events past the read's bookmark: the
        # new stream brings them again, and they were reported already.
        self._ledger.absorb(reported)
        self._follow(bookmark)
        return True

    def _report_unseen(self, reported: EntryLedger) -> None:
        """Report what the new read holds that was not reported before.

        Once the old stream caught up, this finds nothing new. When it could
        not, this reports the entries the read holds that were not reported
        and the read's latest Turn end; an older Turn that ended meanwhile
        cannot be told from a read. A Turn end already reported is not
        reported again.
        """
        follower = self._follower
        state = self._state
        if follower is None or state is None:
            return
        follower.report_entries(
            entry
            for entry in state.history
            if reported.changed(entry.id, completed=_completed(entry))
        )
        turn = state.latest_turn_state
        if turn is not None and turn.status == "terminated":
            follower.report_turn_end(self._ended(state, turn))

    def _read_again_soon(self) -> None:
        """Pick a stream that cannot resume up again from a new read."""
        task = self._rereading
        if self._closed or (task is not None and not task.done()):
            return
        self._rereading = asyncio.create_task(
            self._reread_later(delay=0.0, then_reset=True),
            name=f"vibe-session-follower-reread-{self._session_id}",
        )

    async def _reread_later(
        self, *, delay: float = _RETRY_SECONDS, then_reset: bool = False
    ) -> None:
        """Read the Session again until it answers, then follow from there.

        ``then_reset`` reports a history reset once the read is followed: after
        a gap, a Turn end older than the read's latest cannot be recovered, and
        consumers recount from the history.
        """
        while not self._closed:
            await asyncio.sleep(delay)
            delay = min(max(delay * 2, _RETRY_SECONDS), _RETRY_MAX_SECONDS)
            async with self._following:
                if not await self._switch_to_a_new_read():
                    continue
            follower = self._follower
            if then_reset and follower is not None and not self._closed:
                follower.report_history_reset()
            return

    async def _stop_rereading(self) -> None:
        task, self._rereading = self._rereading, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def close(self) -> None:
        # Not under the lock: a start or rewind waiting on a read sees the flag
        # once it resumes, and starts nothing.
        self._closed = True
        self._follower = None
        await self._stop_rereading()
        await self._stop_pump()

    async def _drain_to(self, target: str) -> bool:
        """Wait until the pump applied ``target``; whether it did in time."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _DRAIN_SECONDS
        while target not in self._applied:
            pump = self._pump
            remaining = deadline - loop.time()
            if pump is None or pump.done() or remaining <= 0:
                return False
            changed = self._changed
            with suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=remaining)
        return True

    async def _take_read(self) -> _Read:
        """Read the root: its state and the bookmark its stream follows from.

        The latest window of history is enough: a Turn's entries and the tool
        calls their results complete are recent.
        """
        await self._commands.ensure_open()
        read, extensions = await self._commands.transport.read_with_extensions(
            SessionReadParams(
                session_id=self._session_id, history=_HISTORY_WINDOW, turns=_LATEST_TURN
            )
        )
        state = ApiSessionState(self._session_id, self._display_roots())
        state.reset(read, extensions)
        return _Read(state=state, bookmark=read.last_event_id)

    def _install(self, read: _Read) -> str:
        """Take ``read`` as what the feed knows; returns its bookmark."""
        self._state = read.state
        # A new ledger, so a caller can still compare with the one it had.
        self._ledger = EntryLedger()
        self._ledger.reset(
            (entry.id, _completed(entry)) for entry in read.state.history
        )
        # The read covers its bookmark: a drain to it has nothing to wait for.
        self._applied.append(read.bookmark)
        return read.bookmark

    def _follow(self, after_event_id: str) -> None:
        self._cursor = after_event_id
        self._pump = asyncio.create_task(
            self._read_stream(after_event_id),
            name=f"vibe-session-follower-{self._session_id}",
        )

    async def _stop_pump(self) -> None:
        pump, self._pump = self._pump, None
        if pump is None:
            return
        pump.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pump

    async def _read_stream(self, after_event_id: str) -> None:
        """Follow the stream until it ends with the Session; a failure resumes
        it from the last event applied, after a growing wait.

        A cursor the Session no longer retains events after cannot resume: a
        new read picks the stream up again from its bookmark.
        """
        cursor = after_event_id
        delay = _RETRY_SECONDS
        logged = False
        while not self._closed:
            stream = cast(
                AsyncGenerator[StreamBatch],
                self._commands.transport.read_stream(
                    EventsReadParams(session_id=self._session_id, after_event_id=cursor)
                ),
            )
            try:
                async with aclosing(stream):
                    async for batch in stream:
                        self._apply(batch)
                        cursor = batch.events[-1].event_id
                        self._cursor = cursor
                        self._applied.extend(item.event_id for item in batch.events)
                        self._changed.set()
                        self._changed = asyncio.Event()
                        delay = _RETRY_SECONDS
                return
            except asyncio.CancelledError:
                raise
            except SessionApiEventGapError:
                logger.warning(
                    "The Session follower fell behind the Session API stream; "
                    "reading the Session again",
                    extra={"session_id": self._session_id},
                    exc_info=True,
                )
                self._read_again_soon()
                return
            except Exception:
                if not logged:
                    logger.warning(
                        "The Session follower lost the Session API stream; resuming",
                        extra={"session_id": self._session_id},
                        exc_info=True,
                    )
                    logged = True
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RETRY_MAX_SECONDS)

    def _apply(self, batch: StreamBatch) -> None:
        follower = self._follower
        state = self._state
        if follower is None or state is None:
            return
        try:
            applied = state.apply(batch)
        except Exception:
            logger.exception(
                "The Session follower could not apply a batch",
                extra={"session_id": self._session_id},
            )
            return
        follower.report_entries(
            change.entry
            for change in applied.history_changes
            if self._ledger.changed(change.entry.id, completed=_completed(change.entry))
        )
        for turn in applied.terminated_turns:
            follower.report_turn_end(self._ended(state, turn))

    def _ended(self, state: ApiSessionState, turn: TurnState) -> EndedTurn:
        try:
            raw: object = state.runtime_turn(turn)
        except Exception:
            logger.warning(
                "The Session follower could not read an ended Turn", exc_info=True
            )
            return EndedTurn(turn_id=turn.id, turn=None)
        return EndedTurn(
            turn_id=turn.id, turn=ended_public_turn(raw, self._public_turn)
        )


def _completed(entry: PublicHistoryEntry) -> bool:
    return entry.generation_status is PublicEntryGenerationStatus.COMPLETED


__all__ = ["SessionApiFeed"]
