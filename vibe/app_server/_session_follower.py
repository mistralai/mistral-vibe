"""Follow one open root Session, whether or not a client is subscribed.

A Session's adapter owns one ``SessionFollower`` from its open to its close.
It hears every update of the root Session (not its subagents) once, however
many clients follow the Session, and none, and reports it to the consumers
registered on it:

- each history entry when it first appears, and once more when it completes
  if it was still in progress then;
- each Turn end, once;
- a history reset, when entries were removed (an in-place rewind).

A consumer is called synchronously, in the order updates arrive, and must not
block: it updates its own state, and starts a task of its own for slow work.
A consumer that raises is logged and skipped; the follower and the Session's
event stream go on. The follower never keeps the Session open or busy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Final, Protocol, cast

from pydantic import TypeAdapter, ValidationError

from mistralai_vibe_local_harness.session_protocol import (
    JsonObject,
    PublicTurn as HarnessPublicTurn,
    SessionReadParams as HarnessSessionReadParams,
    SessionReadResult,
)
from vibe.app_server._unified_history_projection import project_raw_history_entry
from vibe.app_server.models import PublicHistoryEntry, PublicTurn

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EndedTurn:
    """A root Turn that ended."""

    turn_id: str
    turn: PublicTurn | None
    """The Turn in its final status. None when its final status was never
    published: on the direct path, a queued Turn can start in the same update
    that ends the one before it. The Session API path always has it."""


class SessionFollowerConsumer:
    """What a consumer of a ``SessionFollower`` hears. Override what you need.

    Every method is called synchronously and must not block or await.
    """

    def entry_reported(self, entry: PublicHistoryEntry) -> None:
        """A root history entry appeared, or completed.

        An entry is reported when it first appears, whatever its
        ``generation_status``, and once more when it reaches ``completed`` if
        it was still ``in_progress`` then. Updates in between (streamed text,
        a running tool's output) are not reported. A completed assistant
        message is a ``PublicMessageEntry`` with ``role == "assistant"``; a
        completed tool call is a ``PublicEffectEntry`` whose ``state`` carries
        its output; a compaction is a ``PublicCheckpointEntry`` with
        ``kind == "compaction"``.

        Entries already in the history when the follower started, or when the
        consumer registered, are not reported: read them yourself, and tell
        entries apart by ID, as an entry can be both in that read and reported.
        The same holds around a history reset: an entry the rewind itself added
        can be reported just before or just after it.
        """

    def turn_ended(self, ended: EndedTurn) -> None:
        """A root Turn ended: it reached a final status, or a newer Turn
        replaced it while it was still in progress. Reported once per Turn.

        After a start that had to be retried, the latest Turn is reported
        even if it ended before the Session opened: the follower cannot tell
        it from one that ended while it could not follow.
        """

    def history_reset(self) -> None:
        """Root history entries were removed (an in-place rewind), or the
        follower could not follow every update and read the Session again.

        Entries reported before may be gone, and some may have changed without
        being reported. Read the history again to recount; entries reported
        from now on are new or completed after the reset.
        """


class SessionFollowerFeed(Protocol):
    """Where a follower hears the root Session's updates: one per runtime path."""

    async def start(self, follower: SessionFollower) -> None:
        """Take the Session's current state as the baseline, then follow it."""
        ...

    async def history_rewound(self) -> None:
        """The adapter rewound the Session in place."""
        ...

    async def close(self) -> None:
        """Stop following. Never raises."""
        ...


class SessionFollower:
    """Reports the updates of one open root Session to its consumers."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._consumers: list[SessionFollowerConsumer] = []
        self._feed: SessionFollowerFeed | None = None
        self._starting: asyncio.Task[None] | None = None
        self._retrying_start = False
        self._rewinds: set[asyncio.Task[None]] = set()
        self._closed = False
        # The latest Turn ends reported, so a Turn is reported once.
        self._ended_turns: dict[str, None] = {}

    def add_consumer(self, consumer: SessionFollowerConsumer) -> Callable[[], None]:
        """Register ``consumer`` for the updates from now on.

        Returns the function that unregisters it; calling it twice is harmless.
        """
        self._consumers.append(consumer)

        def remove() -> None:
            if consumer in self._consumers:
                self._consumers.remove(consumer)

        return remove

    async def start(self, feed: SessionFollowerFeed) -> None:
        """Follow the Session through ``feed``, once.

        A failed start does not fail the Session's open: it is tried again in
        the background, with growing waits, until it succeeds or the follower
        closes. Once a retry succeeds, consumers get a history reset: the
        history may have changed meanwhile, a rewind included, without them
        hearing of it.
        """
        if self._feed is not None or self._closed:
            return
        self._feed = feed
        try:
            await feed.start(self)
        except Exception:
            logger.warning(
                "The Session follower could not start; retrying",
                extra={"session_id": self.session_id},
                exc_info=True,
            )
            self._starting = asyncio.create_task(
                self._start_again(feed),
                name=f"vibe-session-follower-start-{self.session_id}",
            )

    async def _start_again(self, feed: SessionFollowerFeed) -> None:
        self._retrying_start = True
        delay = _START_RETRY_SECONDS
        while not self._closed:
            await asyncio.sleep(delay)
            delay = min(delay * 2, _START_RETRY_MAX_SECONDS)
            try:
                await feed.start(self)
            except Exception:
                continue
            if not self._closed:
                self.report_history_reset()
            return

    def history_rewound(self) -> asyncio.Future[None]:
        """Tell the feed that the adapter rewound the Session in place.

        Call it as soon as the rewind lands. The follower owns what follows,
        from this call on: cancelling the caller, such as the client request
        that rewound, does not stop it; only ``close`` does. Awaiting the
        result waits until the follower follows the rewound Session.
        """
        feed = self._feed
        if feed is None or self._closed:
            done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            done.set_result(None)
            return done
        task = asyncio.create_task(
            self._follow_rewind(feed),
            name=f"vibe-session-follower-rewind-{self.session_id}",
        )
        self._rewinds.add(task)
        task.add_done_callback(self._rewinds.discard)
        return asyncio.shield(task)

    async def _follow_rewind(self, feed: SessionFollowerFeed) -> None:
        try:
            await feed.history_rewound()
        except asyncio.CancelledError:
            # Only ``close`` cancels it: whoever waits on the rewind sees it end.
            if not self._closed:
                raise
        except Exception:
            logger.exception(
                "The Session follower could not follow a rewind",
                extra={"session_id": self.session_id},
            )

    async def close(self) -> None:
        self._closed = True
        self._consumers.clear()
        starting, self._starting = self._starting, None
        tasks = [*self._rewinds, *([starting] if starting is not None else [])]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        feed, self._feed = self._feed, None
        if feed is not None:
            await feed.close()

    def report_entries(self, entries: Iterable[PublicHistoryEntry]) -> None:
        """For feeds: entries that appeared, or completed."""
        for entry in entries:
            for consumer in tuple(self._consumers):
                try:
                    consumer.entry_reported(entry)
                except Exception:
                    logger.exception(
                        "A Session follower consumer failed on an entry",
                        extra={"session_id": self.session_id},
                    )

    def turn_end_reported(self, turn_id: str) -> bool:
        """For feeds: whether this Turn's end was already reported or seen."""
        return turn_id in self._ended_turns

    def mark_turn_ended(self, turn_id: str) -> None:
        """For feeds: a Turn whose end must not be reported."""
        self._ended_turns[turn_id] = None
        while len(self._ended_turns) > _REMEMBERED_TURN_ENDS:
            del self._ended_turns[next(iter(self._ended_turns))]

    def baseline_turn_ended(self, ended: EndedTurn) -> None:
        """For feeds: the latest Turn had ended when the feed started.

        At the first start it ended before the Session opened: not reported.
        At a retry, it may have ended while the follower could not follow:
        reported, once.
        """
        if self._retrying_start:
            self.report_turn_end(ended)
        else:
            self.mark_turn_ended(ended.turn_id)

    def report_turn_end(self, ended: EndedTurn) -> None:
        """For feeds: a Turn ended. A Turn already reported is not reported again."""
        if self.turn_end_reported(ended.turn_id):
            return
        self.mark_turn_ended(ended.turn_id)
        for consumer in tuple(self._consumers):
            try:
                consumer.turn_ended(ended)
            except Exception:
                logger.exception(
                    "A Session follower consumer failed on a Turn end",
                    extra={"session_id": self.session_id},
                )

    def report_history_reset(self) -> None:
        """For feeds: entries were removed."""
        for consumer in tuple(self._consumers):
            try:
                consumer.history_reset()
            except Exception:
                logger.exception(
                    "A Session follower consumer failed on a history reset",
                    extra={"session_id": self.session_id},
                )


class EntryLedger:
    """Which entries a feed has seen, and whether each was completed.

    Keyed by whatever ID the feed reads: a raw Runtime entry's or a public one's.
    """

    def __init__(self) -> None:
        self._completed: dict[str, bool] = {}

    def reset(self, entries: Iterable[tuple[str, bool]]) -> None:
        self._completed = dict(entries)

    def keep(self, entry_ids: Iterable[str | None]) -> None:
        """Forget the entries that are gone."""
        kept = set(entry_ids)
        self._completed = {
            entry_id: completed
            for entry_id, completed in self._completed.items()
            if entry_id in kept
        }

    def absorb(self, other: EntryLedger) -> None:
        """Also hold what ``other`` recorded, completed if either says so."""
        for entry_id, completed in other._completed.items():
            self._completed[entry_id] = completed or self._completed.get(
                entry_id, False
            )

    def changed(self, entry_id: str, *, completed: bool) -> bool:
        """Record the entry; whether it is new or newly completed.

        A completion stays recorded: a stream that replays the entry's earlier,
        in-progress update does not make its completion new again.
        """
        previous = self._completed.get(entry_id)
        self._completed[entry_id] = completed or bool(previous)
        return previous is None or (completed and not previous)


class _ObservedSession(Protocol):
    """What the direct feed needs of the Runtime session."""

    @property
    def session_id(self) -> str: ...

    async def read(self, params: HarnessSessionReadParams) -> SessionReadResult: ...

    def observe_events(
        self,
        observer_id: str,
        observer: Callable[[JsonObject], None],
        *,
        places_notices: bool = True,
    ) -> object: ...

    def stop_observing_events(self, observer_id: str) -> None: ...


_OBSERVER_ID: Final = "vibe-session-follower"
_FULL_HISTORY: Final = 1_000_000
_HARNESS_TURN: Final = TypeAdapter(HarnessPublicTurn)


class RuntimeSessionFeed:
    """The direct path: the Runtime session's own events, through an observer.

    Each root update carries the whole state. An entry is projected only when it
    is new or newly completed; one whose object did not change is skipped. The
    observer runs inside the Runtime's publish, so it never raises.
    """

    def __init__(
        self,
        session: _ObservedSession,
        *,
        display_roots: Callable[[], Sequence[Path]],
        public_turn: Callable[[object], PublicTurn],
    ) -> None:
        self._session = session
        self._display_roots = display_roots
        self._public_turn = public_turn
        self._follower: SessionFollower | None = None
        # The root's raw entries as last published, and which were completed.
        self._raw_entries: list[object] = []
        self._ledger = EntryLedger()
        self._latest_turn_id: str | None = None
        self._latest_turn_in_progress = False
        self._closed = False

    async def start(self, follower: SessionFollower) -> None:
        session_id = self._session.session_id
        read = await self._session.read(
            HarnessSessionReadParams(session_id=session_id, history_limit=_FULL_HISTORY)
        )
        state = read.snapshot.state
        self._raw_entries = list(state.history.entries)
        self._ledger.reset(
            (entry_id, raw.get("generationStatus") == "completed")
            for raw in state.history.entries
            if isinstance(entry_id := raw.get("id"), str)
        )
        if self._closed:
            return
        latest = state.latest_turn
        if latest is not None:
            self._latest_turn_id = latest.id
            self._latest_turn_in_progress = latest.status == "in_progress"
            if not self._latest_turn_in_progress:
                follower.baseline_turn_ended(
                    EndedTurn(
                        turn_id=latest.id,
                        turn=ended_public_turn(
                            latest.model_dump(mode="json", by_alias=True),
                            self._public_turn,
                        ),
                    )
                )
        self._follower = follower
        # It shows nothing to anyone, so held notices wait for a client.
        self._session.observe_events(_OBSERVER_ID, self._observe, places_notices=False)

    async def history_rewound(self) -> None:
        """Nothing to do: the update that removes entries says so."""

    async def close(self) -> None:
        self._closed = True
        self._follower = None
        with contextlib.suppress(Exception):
            self._session.stop_observing_events(_OBSERVER_ID)

    def _observe(self, event: JsonObject) -> None:
        follower = self._follower
        if follower is None or event.get("type") != "session_state_updated":
            return
        if event.get("sessionId") not in {None, follower.session_id}:
            return
        try:
            self._update(follower, event.get("state"))
        except Exception:
            logger.exception(
                "The Session follower could not read an update",
                extra={"session_id": follower.session_id},
            )

    def _update(self, follower: SessionFollower, state: object) -> None:
        if not isinstance(state, Mapping):
            return
        state = cast(Mapping[str, Any], state)
        history = state.get("history")
        raw_entries = history.get("entries") if isinstance(history, Mapping) else None
        reset = False
        if isinstance(raw_entries, list):
            try:
                reset = self._update_entries(follower, cast(list[object], raw_entries))
            except Exception:
                logger.exception(
                    "The Session follower could not read an update's history",
                    extra={"session_id": follower.session_id},
                )
        self._update_turn(follower, state.get("latestTurn"), reset=reset)

    def _update_entries(
        self, follower: SessionFollower, raw_entries: list[object]
    ) -> bool:
        """Report the entries this update added or completed; whether it
        removed entries.

        The Runtime replaces an entry instead of changing it, so an entry still
        the same object is skipped without being read. Entries are only ever
        appended, except when history is rewritten: then every entry is read.
        """
        previous, self._raw_entries = self._raw_entries, raw_entries
        appended = len(raw_entries) >= len(previous) and all(
            raw is old or _entry_id(raw) == _entry_id(old)
            for raw, old in zip(raw_entries, previous, strict=False)
        )
        reset = False
        if not appended:
            current = {_entry_id(raw) for raw in raw_entries}
            self._ledger.keep(current)
            # Streamed text a retry dropped is not history: only a completed
            # entry gone means entries were removed.
            reset = any(
                _completed(old) and _entry_id(old) not in current for old in previous
            )
            if reset:
                follower.report_history_reset()
        changed: list[Mapping[str, Any]] = []
        for position, raw in enumerate(raw_entries):
            if appended and position < len(previous) and raw is previous[position]:
                continue
            entry_id = _entry_id(raw)
            if entry_id is None:
                continue
            if self._ledger.changed(entry_id, completed=_completed(raw)):
                changed.append(cast(Mapping[str, Any], raw))
        roots = self._display_roots()
        for raw in changed:
            try:
                projected = project_raw_history_entry(raw, roots)
            except Exception:
                logger.warning(
                    "The Session follower could not project an entry",
                    extra={"session_id": follower.session_id},
                    exc_info=True,
                )
                continue
            follower.report_entries(projected)
        return reset

    def _update_turn(
        self, follower: SessionFollower, raw_turn: object, *, reset: bool
    ) -> None:
        turn = (
            cast(Mapping[str, Any], raw_turn) if isinstance(raw_turn, Mapping) else None
        )
        if turn is not None and turn.get("sessionId") != follower.session_id:
            turn = None
        turn_id = turn.get("id") if turn is not None else None
        turn_id = turn_id if isinstance(turn_id, str) else None
        in_progress = turn is not None and turn.get("status") == "in_progress"
        previous_id = self._latest_turn_id
        if (
            previous_id is not None
            and self._latest_turn_in_progress
            and turn_id != previous_id
        ):
            # A queued Turn can start in the update that ends the one before:
            # the ended Turn's final state is never published.
            follower.report_turn_end(EndedTurn(turn_id=previous_id, turn=None))
        self._latest_turn_id = turn_id
        self._latest_turn_in_progress = in_progress
        if turn is None or turn_id is None or in_progress:
            return
        if reset:
            # A rewind can leave an older Turn, long ended, as the latest.
            follower.mark_turn_ended(turn_id)
            return
        if follower.turn_end_reported(turn_id):
            return
        follower.report_turn_end(
            EndedTurn(turn_id=turn_id, turn=ended_public_turn(turn, self._public_turn))
        )


def ended_public_turn(
    raw: object, public_turn: Callable[[object], PublicTurn]
) -> PublicTurn | None:
    """Vibe's Turn from a Runtime-format Turn; None, logged, when it cannot be read."""
    try:
        return public_turn(_HARNESS_TURN.validate_python(raw))
    except (ValidationError, ValueError):
        logger.warning(
            "The Session follower could not read an ended Turn", exc_info=True
        )
        return None


_REMEMBERED_TURN_ENDS: Final = 256
_START_RETRY_SECONDS: Final = 1.0
_START_RETRY_MAX_SECONDS: Final = 60.0


def _entry_id(raw: object) -> str | None:
    entry_id = (
        cast(Mapping[str, Any], raw).get("id") if isinstance(raw, Mapping) else None
    )
    return entry_id if isinstance(entry_id, str) else None


def _completed(raw: object) -> bool:
    return (
        isinstance(raw, Mapping)
        and cast(Mapping[str, Any], raw).get("generationStatus") == "completed"
    )


__all__ = [
    "EndedTurn",
    "EntryLedger",
    "RuntimeSessionFeed",
    "SessionFollower",
    "SessionFollowerConsumer",
    "SessionFollowerFeed",
    "ended_public_turn",
]
