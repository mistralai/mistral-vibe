"""Follow a root Session's Session API stream and its subagents' streams as one.

``SessionApiStreams`` merges the stream of the root Session and of each of its
subagents into one sequence of batches, and remembers which event IDs the
adapter has published, so a command's reply can wait for the event its
response names. Event IDs are opaque: they are only ever compared for
equality, never parsed or ordered.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass
import logging
from typing import cast

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    ListPageRequest,
    PageRequest,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionReadParams,
    SessionReadResponse,
    SubagentsListParams,
    SubagentsListResponse,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    StreamBatch,
    VibeSessionReadResponse,
)
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    LocalSessionApiTransport,
)

logger = logging.getLogger(__name__)

REMEMBERED_EVENT_IDS = 10_000
"""How many of the most recent published event IDs are kept per Session."""

_ALL = PageRequest(limit=1_000_000, direction="forward")
_LATEST_TURN = PageRequest(limit=1, direction="backward")

type SessionRead = tuple[SessionReadResponse, VibeSessionReadResponse]


@dataclass(frozen=True, slots=True)
class SessionBatch:
    session_id: str
    batch: StreamBatch


@dataclass(frozen=True, slots=True)
class _StreamEnded:
    session_id: str
    error: BaseException | None


class SessionApiStreams:
    """Follow one root Session's stream and its subagents' streams as one sequence."""

    def __init__(self, transport: LocalSessionApiTransport) -> None:
        self._transport = transport
        self._root_id = transport.session_id
        self._queue: asyncio.Queue[SessionBatch | _StreamEnded | None] = asyncio.Queue()
        self._followers: dict[str, asyncio.Task[None]] = {}
        self._child_reads: dict[str, SessionRead] = {}
        self._opened_children: set[str] = set()
        self._observed: dict[str, dict[str, None]] = {}
        self._ended: set[str] = set()
        self._stopped = False
        self._changed = asyncio.Event()

    async def start(self, after_event_id: str) -> None:
        """Open the root's stream after the bookmark of the root's read.

        Also lists the root's subagents (``app_server/subagents/list``), reads
        each one with ``transport.read_with_extensions`` and opens its stream
        after that read's bookmark. The reads are kept: see ``child_reads``. A
        subagent that cannot be read is logged and skipped, and so is the whole
        list when it cannot be read: the root still opens without its subagents.
        """
        self._follow(self._root_id, after_event_id)
        try:
            child_ids = await self._list_subagents()
        except Exception:
            logger.warning(
                "Could not list the subagents of Session %s",
                self._root_id,
                exc_info=True,
            )
            return
        for child_id in child_ids:
            try:
                await self.add_child(child_id)
            except Exception:
                logger.warning(
                    "Could not open subagent Session %s", child_id, exc_info=True
                )

    def child_reads(self) -> dict[str, SessionRead]:
        """The read of each subagent opened so far, taken just before its stream opened."""
        return dict(self._child_reads)

    async def add_child(self, session_id: str) -> SessionRead | None:
        """Read and open one subagent's stream, once; None when it was already open."""
        if session_id in self._opened_children:
            return None
        self._opened_children.add(session_id)
        try:
            read = await self._transport.read_with_extensions(
                SessionReadParams(
                    session_id=session_id, history=_ALL, turns=_LATEST_TURN
                )
            )
        except BaseException:
            self._opened_children.discard(session_id)
            raise
        self._child_reads[session_id] = read
        self._follow(session_id, read[0].last_event_id)
        return read

    async def __aiter__(self) -> AsyncIterator[SessionBatch]:
        """Every batch of every followed Session, in the order each arrives.

        Batches of one Session keep their order. Ends when the root's stream
        ends or ``close`` is called; a failure of the root's stream is raised.
        A child stream that ends or fails is logged and dropped.
        """
        while (item := await self._queue.get()) is not None:
            if isinstance(item, SessionBatch):
                yield item
                continue
            self._end(item)
            if item.session_id != self._root_id:
                continue
            self._stop()
            if item.error is not None:
                raise item.error
            return

    def observed(self, session_id: str, event_ids: Sequence[str]) -> None:
        """Mark these IDs as published by the adapter, after it emitted their batch."""
        remembered = self._observed.setdefault(session_id, {})
        for event_id in event_ids:
            remembered[event_id] = None
        while len(remembered) > REMEMBERED_EVENT_IDS:
            del remembered[next(iter(remembered))]
        self._notify()

    def mark_baseline(self, session_id: str, event_id: str) -> None:
        """Mark a read's bookmark as already seen (it is in the read's state)."""
        self.observed(session_id, [event_id])

    def is_observed(self, session_id: str, event_id: str) -> bool:
        """Whether ``event_id`` was marked observed or as a baseline (and is still remembered)."""
        return event_id in self._observed.get(session_id, {})

    async def wait_observed(self, session_id: str, event_id: str) -> None:
        """Return once ``event_id`` was marked observed or as a baseline, or once
        following that Session stopped (its stream ended or ``close`` was called).
        """
        while not (
            self.is_observed(session_id, event_id)
            or self._stopped
            or session_id in self._ended
        ):
            await self._changed.wait()

    async def close(self) -> None:
        followers, self._followers = self._followers, {}
        for task in followers.values():
            task.cancel()
        for task in followers.values():
            with suppress(asyncio.CancelledError):
                await task
        self._stop()
        self._queue.put_nowait(None)

    def _follow(self, session_id: str, after_event_id: str) -> None:
        self._followers[session_id] = asyncio.create_task(
            self._pump(session_id, after_event_id),
            name=f"session-api-stream-{session_id}",
        )

    async def _pump(self, session_id: str, after_event_id: str) -> None:
        stream = cast(
            AsyncGenerator[StreamBatch],
            self._transport.read_stream(
                EventsReadParams(session_id=session_id, after_event_id=after_event_id)
            ),
        )
        error: BaseException | None = None
        try:
            async with aclosing(stream):
                async for batch in stream:
                    self._queue.put_nowait(SessionBatch(session_id, batch))
        except Exception as exc:
            error = exc
        self._queue.put_nowait(_StreamEnded(session_id, error))

    async def _list_subagents(self) -> list[str]:
        child_ids: list[str] = []
        cursor: str | None = None
        while True:
            response = await self._transport.request(
                "app_server/subagents/list",
                SubagentsListParams(
                    parent_session_id=self._root_id, page=ListPageRequest(cursor=cursor)
                ),
                SubagentsListResponse,
            )
            child_ids.extend(item.session.id for item in response.items)
            cursor = response.next_cursor
            if cursor is None:
                return child_ids

    def _end(self, ended: _StreamEnded) -> None:
        """Drop a Session whose stream ended; its waiters stop waiting."""
        self._followers.pop(ended.session_id, None)
        if ended.session_id != self._root_id:
            logger.warning(
                "Stopped following subagent Session %s",
                ended.session_id,
                exc_info=ended.error,
            )
        self._ended.add(ended.session_id)
        self._notify()

    def _stop(self) -> None:
        self._stopped = True
        self._notify()

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()


__all__ = ["REMEMBERED_EVENT_IDS", "SessionApiStreams", "SessionBatch"]
