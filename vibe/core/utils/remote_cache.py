"""A cache of one remote resource, shared by every session that reads it.

A session never waits on the network for a value it has seen before: it reads
the cached answer, and checking it starts a refresh in the background once the
answer is older than the resource's ``refresh_after_s``. Every reader shares
that refresh, so one request is in flight per key whatever the number of
sessions. A failed refresh is not retried before a delay that doubles with each
failure. A session reads the value held at its next turn boundary, and loads
it there when it differs from the one it was built from.

A cache is owned by a process service (see ``ProcessServices``), never by a
session or a channel: a multiplexed app server opens many of both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
import contextlib
import contextvars
from dataclasses import dataclass
import time
from typing import Protocol, cast

from vibe.observability.logging import logger


@dataclass(frozen=True, slots=True)
class RefreshPolicy:
    refresh_after_s: float
    """Older than this, checking the value starts a background refresh."""
    max_age_s: float
    """Older than this, the value is never served."""
    retry_after_s: float
    """The wait after a first failure, doubled after each further one."""
    max_retry_after_s: float
    fetch_timeout_s: float
    """Bounds one fetch, its own retries included."""


@dataclass(frozen=True, slots=True)
class Cached[V]:
    value: V
    stored_at: float
    """Wall-clock seconds, so a copy on disk keeps its age across processes."""


class CacheStore[K, V](Protocol):
    """A resource's own persistence; both calls run off the event loop."""

    def load(self, key: K) -> Cached[V] | None: ...

    def save(self, key: K, entry: Cached[V]) -> None: ...


class RemoteFetchFailedError(Exception):
    """No value: the fetch failed, or failed recently and is not retried yet.

    ``cause`` is the caller's own copy of the fetch's error, safe to raise:
    raising the one the cache keeps would attach the caller's frames to it,
    and so keep the caller's session alive for as long as the backoff.
    """

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause) or type(cause).__name__)
        self.cause = cause


@dataclass(slots=True)
class _KeyState[V]:
    entry: Cached[V] | None = None
    loading: asyncio.Task[None] | None = None
    loaded: bool = False
    flight: asyncio.Task[Cached[V]] | None = None
    flight_forced: bool = False
    failures: int = 0
    retry_at: float = 0.0
    error: Exception | None = None


class RemoteCache[K: Hashable, V]:
    """Keys tell entries apart by equality; a key may carry what a fetch needs
    (a credential) in fields left out of it.
    """

    def __init__(
        self,
        name: str,
        policy: RefreshPolicy,
        fetch: Callable[[K], Awaitable[V]],
        *,
        store: CacheStore[K, V],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._name = name
        self._policy = policy
        self._fetch = fetch
        self._store = store
        self._clock = clock
        self._states: dict[K, _KeyState[V]] = {}

    def cached(self, key: K) -> Cached[V] | None:
        """The value held in memory, without starting anything."""
        state = self._states.get(key)
        return self._servable(state) if state is not None else None

    async def load(self, key: K) -> Cached[V] | None:
        """``cached``, reading the copy on disk the first time a key is used."""
        state = self._state(key)
        if state.loaded:
            return self._servable(state)
        # Shared, so a second caller does not take the cache for empty while
        # the first is still reading the disk. In a context of its own, and
        # let go once done, as a fetch is: it outlives the session that started it.
        if state.loading is None:
            loading = asyncio.create_task(
                self._load_once(key, state),
                name=f"vibe-load-{self._name}",
                context=contextvars.Context(),
            )
            state.loading = loading

            def settle(task: asyncio.Task[None]) -> None:
                if state.loading is task:
                    state.loading = None
                    state.loaded = not task.cancelled()

            loading.add_done_callback(settle)
        await asyncio.shield(state.loading)
        return self._servable(state)

    def check(self, key: K) -> None:
        """Start a refresh when the value is old or missing; never waits."""
        state = self._state(key)
        entry = self._servable(state)
        if state.flight is not None or self._clock() < state.retry_at:
            return
        if entry is None or self._stale(entry):
            self._start(key, state, force=False)

    async def first(self, key: K, *, timeout_s: float) -> Cached[V] | None:
        """The cached value, or, with none, the first fetch's.

        Starts a refresh when the cached value is old. ``None`` when the first
        fetch outlasts ``timeout_s``: it carries on in the background. Raises
        ``RemoteFetchFailedError`` when it fails, or failed recently, rather
        than wait out a fetch known not to answer.
        """
        if (entry := await self.load(key)) is not None:
            self.check(key)
            return entry
        flight = self._flight(key)
        try:
            return await _joined(asyncio.wait_for(asyncio.shield(flight), timeout_s))
        except TimeoutError:
            return None

    async def refresh(self, key: K) -> Cached[V]:
        """Fetch now, whatever the value's age or a recent failure.

        Joins a forced fetch already in flight rather than start a second one.
        A background refresh in flight may answer from another process's copy
        without asking the server, so it is let finish and followed by one.
        """
        state = self._state(key)
        # A check() during the wait can start another background one.
        while state.flight is not None and not state.flight_forced:
            with contextlib.suppress(Exception):
                await asyncio.shield(state.flight)
        if state.flight is None:
            self._start(key, state, force=True)
        assert state.flight is not None
        return await _joined(asyncio.shield(state.flight))

    def failure(self, key: K) -> Exception | None:
        """Why the last fetch of ``key`` failed, while it is not retried yet."""
        state = self._states.get(key)
        if state is None or self._clock() >= state.retry_at or state.error is None:
            return None
        return _copy(state.error)

    def followed(self, key: K) -> Followed[K, V]:
        """``key``'s value, as a session's ``RemoteFollower`` follows it."""
        return Followed(self, key)

    async def close(self) -> None:
        """Cancel the fetches in flight and forget every value."""
        flights = [state.flight for state in self._states.values() if state.flight]
        self._states.clear()
        for flight in flights:
            flight.cancel()
        await asyncio.gather(*flights, return_exceptions=True)

    def _state(self, key: K) -> _KeyState[V]:
        state = self._states.get(key)
        if state is None:
            state = self._states[key] = _KeyState()
        return state

    def _servable(self, state: _KeyState[V]) -> Cached[V] | None:
        entry = state.entry
        if (
            entry is not None
            and self._clock() - entry.stored_at >= self._policy.max_age_s
        ):
            return None
        return entry

    def _stale(self, entry: Cached[V]) -> bool:
        return self._clock() - entry.stored_at >= self._policy.refresh_after_s

    def _flight(self, key: K) -> asyncio.Task[Cached[V]]:
        state = self._state(key)
        if state.flight is not None:
            return state.flight
        if self._clock() < state.retry_at:
            assert state.error is not None
            raise RemoteFetchFailedError(_copy(state.error))
        return self._start(key, state, force=False)

    def _start(
        self, key: K, state: _KeyState[V], *, force: bool
    ) -> asyncio.Task[Cached[V]]:
        # In a context of its own: one copied from the session that happened
        # to start it would keep that session alive as long as the fetch runs.
        flight = asyncio.create_task(
            self._run(key, state, force=force),
            name=f"vibe-refresh-{self._name}",
            context=contextvars.Context(),
        )
        state.flight = flight
        state.flight_forced = force

        def settle(task: asyncio.Task[Cached[V]]) -> None:
            # Let go of the task: its result is held as the entry, and its
            # error's traceback would keep its callers' frames alive.
            if state.flight is task:
                state.flight = None
            if not task.cancelled():
                task.exception()

        flight.add_done_callback(settle)
        return flight

    async def _run(self, key: K, state: _KeyState[V], *, force: bool) -> Cached[V]:
        if not force:
            # Another process may have refreshed it: its copy saves a request.
            await self._load_once(key, state)
            if state.entry is not None and not self._stale(state.entry):
                return state.entry
        failed: Exception | None = None
        value: V | None = None
        try:
            async with asyncio.timeout(self._policy.fetch_timeout_s):
                value = await self._fetch(key)
        except Exception as exc:
            # Only a copy leaves this block: the error itself holds the frames
            # it was raised through, the fetch's and its first caller's.
            failed = _copy(exc)
        if failed is not None:
            state.failures += 1
            state.retry_at = self._clock() + min(
                self._policy.retry_after_s * 2 ** (state.failures - 1),
                self._policy.max_retry_after_s,
            )
            state.error = failed
            logger.info(
                "%s refresh failed (attempt %d): %s",
                self._name,
                state.failures,
                str(failed) or type(failed).__name__,
            )
            raise RemoteFetchFailedError(failed)
        entry = Cached(value=cast("V", value), stored_at=self._clock())
        state.failures = 0
        state.retry_at = 0.0
        state.error = None
        try:
            await asyncio.to_thread(self._store.save, key, entry)
        except Exception:
            logger.warning("%s cache write failed", self._name, exc_info=True)
        self._adopt(state, entry)
        return entry

    async def _load_once(self, key: K, state: _KeyState[V]) -> None:
        try:
            stored = await asyncio.to_thread(self._store.load, key)
        except Exception:
            logger.debug("%s cache read failed", self._name, exc_info=True)
            return
        if stored is not None and (
            state.entry is None or stored.stored_at > state.entry.stored_at
        ):
            self._adopt(state, stored)

    def _adopt(self, state: _KeyState[V], entry: Cached[V]) -> None:
        state.entry = entry


@dataclass(frozen=True, slots=True)
class Followed[K: Hashable, V]:
    """One key of a ``RemoteCache``, as a ``RemoteFollower`` follows it."""

    _cache: RemoteCache[K, V]
    _key: K

    def held(self) -> V | None:
        entry = self._cache.cached(self._key)
        return entry.value if entry is not None else None

    def check(self) -> None:
        self._cache.check(self._key)


async def _joined[T](awaitable: Awaitable[T]) -> T:
    """Await a shared fetch, raising a failure of this caller's own.

    Every caller of one fetch is handed the same error object, and raising it
    adds the caller's frames to it: a copy keeps each session's frames off an
    error the others, and the backoff, still hold.
    """
    try:
        return await awaitable
    except RemoteFetchFailedError as exc:
        # Raising it here put this caller's frames on the shared error.
        exc.__traceback__ = None
        failed = RemoteFetchFailedError(_copy(exc.cause))
    raise failed


def _copy(exc: Exception) -> Exception:
    """``exc``'s type, arguments and attributes, without its traceback."""
    try:
        copy = type(exc).__new__(type(exc), *exc.args)
    except Exception:
        # A type built from more than its args (pydantic's ValidationError).
        return RuntimeError(f"{type(exc).__name__}: {exc}")
    copy.args = exc.args
    copy.__dict__.update(getattr(exc, "__dict__", {}))
    return copy


__all__ = [
    "CacheStore",
    "Cached",
    "Followed",
    "RefreshPolicy",
    "RemoteCache",
    "RemoteFetchFailedError",
]
