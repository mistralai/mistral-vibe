from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import gc
import weakref

import pytest

from vibe.core.utils.remote_cache import (
    Cached,
    RefreshPolicy,
    RemoteCache,
    RemoteFetchFailedError,
)

_POLICY = RefreshPolicy(
    refresh_after_s=600,
    max_age_s=7 * 24 * 3600,
    retry_after_s=30,
    max_retry_after_s=600,
    fetch_timeout_s=5,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _Fetch:
    values: list[object] = field(default_factory=lambda: ["v1"])
    calls: int = 0
    release: asyncio.Event | None = None

    async def __call__(self, _key: str) -> object:
        self.calls += 1
        if self.release is not None:
            await self.release.wait()
        value = self.values[min(self.calls, len(self.values)) - 1]
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class _Store:
    entries: dict[str, Cached[object]] = field(default_factory=dict)

    def load(self, key: str) -> Cached[object] | None:
        return self.entries.get(key)

    def save(self, key: str, entry: Cached[object]) -> None:
        self.entries[key] = entry


def _cache(
    fetch: _Fetch, clock: _Clock, store: _Store | None = None
) -> RemoteCache[str, object]:
    return RemoteCache("Test", _POLICY, fetch, store=store or _Store(), clock=clock)


async def _get(cache: RemoteCache[str, object], key: str) -> Cached[object] | None:
    """What a session open does: read the cached value, and check its age."""
    entry = await cache.load(key)
    cache.check(key)
    return entry


async def _first(cache: RemoteCache[str, object], key: str) -> Cached[object] | None:
    return await cache.first(key, timeout_s=5)


async def _settle() -> None:
    """Let every refresh started so far finish, or reach a fetch held open."""
    for _ in range(20):
        await asyncio.sleep(0)
    refreshes = [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("vibe-refresh-")
    ]
    if refreshes:
        await asyncio.wait(refreshes, timeout=0.05)


@pytest.mark.asyncio
async def test_a_cached_value_is_served_at_once_without_a_fetch() -> None:
    clock, fetch = _Clock(), _Fetch()
    store = _Store({"k": Cached("disk", stored_at=clock.now - 60)})
    cache = _cache(fetch, clock, store)

    entry = await _get(cache, "k")
    await _settle()

    assert entry is not None and entry.value == "disk"
    assert fetch.calls == 0


@pytest.mark.asyncio
async def test_a_stale_value_is_served_and_refreshed_once_in_the_background() -> None:
    clock, fetch = _Clock(), _Fetch(values=["fresh"])
    fetch.release = asyncio.Event()
    store = _Store({"k": Cached("old", stored_at=clock.now - 601)})
    cache = _cache(fetch, clock, store)

    served = [await _get(cache, "k") for _ in range(3)]
    await _settle()
    fetch.release.set()
    await _settle()

    assert [entry.value for entry in served if entry is not None] == ["old"] * 3
    assert fetch.calls == 1
    assert cache.cached("k") == Cached("fresh", stored_at=clock.now)
    assert store.entries["k"].value == "fresh"


@pytest.mark.asyncio
async def test_concurrent_first_reads_share_one_fetch() -> None:
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    cache = _cache(fetch, clock)

    readers = [asyncio.create_task(_first(cache, "k")) for _ in range(4)]
    await _settle()
    fetch.release.set()
    results = await asyncio.gather(*readers)

    assert fetch.calls == 1
    assert {entry.value for entry in results if entry is not None} == {"v1"}


@pytest.mark.asyncio
async def test_a_first_read_stops_waiting_at_its_limit_and_the_fetch_carries_on() -> (
    None
):
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    cache = _cache(fetch, clock)

    assert await cache.first("k", timeout_s=0.01) is None
    fetch.release.set()
    await _settle()

    assert cache.cached("k") is not None


@pytest.mark.asyncio
async def test_a_reader_giving_up_does_not_cancel_the_shared_fetch() -> None:
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    cache = _cache(fetch, clock)

    reader = asyncio.create_task(_first(cache, "k"))
    await _settle()
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    fetch.release.set()
    await _settle()

    assert cache.cached("k") == Cached("v1", stored_at=clock.now)


@pytest.mark.asyncio
async def test_a_failure_backs_off_doubling_up_to_its_cap() -> None:
    clock = _Clock()
    fetch = _Fetch(values=[OSError("down")] * 10)
    cache = _cache(fetch, clock)

    delays = []
    for _ in range(7):
        with pytest.raises(RemoteFetchFailedError):
            await _first(cache, "k")
        with pytest.raises(RemoteFetchFailedError):
            await _first(cache, "k")
        retry_at = cache._states["k"].retry_at
        delays.append(retry_at - clock.now)
        clock.now = retry_at

    assert delays == [30, 60, 120, 240, 480, 600, 600]
    assert fetch.calls == 7


@pytest.mark.asyncio
async def test_a_stale_value_is_not_refreshed_during_the_backoff() -> None:
    clock = _Clock()
    fetch = _Fetch(values=[OSError("down"), "fresh"])
    store = _Store({"k": Cached("old", stored_at=clock.now - 601)})
    cache = _cache(fetch, clock, store)

    await _get(cache, "k")
    await _settle()
    await _get(cache, "k")
    await _settle()
    assert fetch.calls == 1

    clock.now += 30
    await _get(cache, "k")
    await _settle()

    assert fetch.calls == 2
    assert cache.cached("k") is not None
    assert cache.failure("k") is None


@pytest.mark.asyncio
async def test_an_explicit_refresh_ignores_the_age_and_the_backoff() -> None:
    clock = _Clock()
    fetch = _Fetch(values=[OSError("down"), "forced"])
    cache = _cache(fetch, clock)
    with pytest.raises(RemoteFetchFailedError):
        await _first(cache, "k")

    entry = await cache.refresh("k")

    assert entry.value == "forced"
    assert fetch.calls == 2


@pytest.mark.asyncio
async def test_a_follower_reads_the_value_a_refresh_brings() -> None:
    clock = _Clock()
    fetch = _Fetch(values=["v1", "v2"])
    cache = _cache(fetch, clock)
    followed = cache.followed("k")
    await _first(cache, "k")
    assert followed.held() == "v1"

    await cache.refresh("k")

    assert followed.held() == "v2"


@pytest.mark.asyncio
async def test_a_fresher_copy_written_by_another_process_saves_the_request() -> None:
    clock, fetch = _Clock(), _Fetch()
    store = _Store({"k": Cached("old", stored_at=clock.now - 601)})
    cache = _cache(fetch, clock, store)
    await _get(cache, "k")
    store.entries["k"] = Cached("theirs", stored_at=clock.now - 5)
    await _settle()

    assert fetch.calls == 0
    assert cache.cached("k") == Cached("theirs", stored_at=clock.now - 5)


@pytest.mark.asyncio
async def test_a_value_past_its_maximum_age_is_never_served() -> None:
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    store = _Store({"k": Cached("ancient", stored_at=clock.now - 8 * 24 * 3600)})
    cache = _cache(fetch, clock, store)

    assert await _get(cache, "k") is None
    assert await cache.first("k", timeout_s=0.01) is None
    fetch.release.set()
    await _settle()

    assert cache.cached("k") == Cached("v1", stored_at=clock.now)


@pytest.mark.asyncio
async def test_an_explicit_refresh_asks_the_server_even_after_a_background_one() -> (
    None
):
    clock, fetch = _Clock(), _Fetch(values=["forced"])
    store = _Store({"k": Cached("old", stored_at=clock.now - 601)})
    cache = _cache(fetch, clock, store)
    await _get(cache, "k")
    # Another process's copy lands while the background refresh reads it.
    store.entries["k"] = Cached("theirs", stored_at=clock.now - 5)

    entry = await cache.refresh("k")

    assert entry.value == "forced"
    assert fetch.calls == 1


@pytest.mark.asyncio
async def test_an_explicit_refresh_asks_the_server_after_back_to_back_background_ones() -> (
    None
):
    # Another session's check starts a second background refresh while the
    # explicit one waits for the first: it waits for that one too, then asks.
    clock, fetch = _Clock(), _Fetch(values=[RuntimeError("down"), "forced"])
    store = _Store({"k": Cached("old", stored_at=clock.now - 601)})
    cache = _cache(fetch, clock, store)
    fetch.release = asyncio.Event()
    await _get(cache, "k")
    await _settle()
    refreshing = asyncio.create_task(cache.refresh("k"))
    await asyncio.sleep(0)
    # The first background fetch fails; the backoff is over by the next check,
    # which another session runs before the explicit refresh resumes, and which
    # finds another process's fresh copy instead of asking the server.
    held = cache._state("k").flight
    assert held is not None

    def another_session_checks(_task: object) -> None:
        clock.now += 3600
        store.entries["k"] = Cached("theirs", stored_at=clock.now)
        cache.check("k")

    held.add_done_callback(another_session_checks)
    fetch.release.set()

    entry = await refreshing

    assert entry.value == "forced"
    assert fetch.calls == 2


@pytest.mark.asyncio
async def test_a_follower_reads_the_first_value_a_late_fetch_brings() -> None:
    # A session that opened before the first answer landed still finds it.
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    cache = _cache(fetch, clock)
    followed = cache.followed("k")
    assert await cache.first("k", timeout_s=0.01) is None
    assert followed.held() is None

    fetch.release.set()
    await _settle()

    assert followed.held() == "v1"


@pytest.mark.asyncio
async def test_a_failure_holds_no_frame_of_the_reader_it_failed() -> None:
    clock = _Clock()
    fetch = _Fetch(values=[TimeoutError("down")])
    cache = _cache(fetch, clock)

    class _Reader:
        async def read(self) -> None:
            with contextlib.suppress(RemoteFetchFailedError):
                await _first(cache, "k")

    reader = _Reader()
    alive = weakref.ref(reader)
    await reader.read()
    del reader
    gc.collect()

    assert alive() is None
    stored = cache.failure("k")
    assert stored is not None and stored.__traceback__ is None


@pytest.mark.asyncio
async def test_an_error_built_from_more_than_its_args_still_backs_off() -> None:
    class _Strict(Exception):
        def __new__(cls, message: str, detail: str) -> _Strict:
            return super().__new__(cls, message, detail)

    clock = _Clock()
    fetch = _Fetch(values=[_Strict("bad", "detail")])
    cache = _cache(fetch, clock)

    with pytest.raises(RemoteFetchFailedError) as raised:
        await _first(cache, "k")

    assert "bad" in str(raised.value)
    assert cache.failure("k") is not None


@pytest.mark.asyncio
async def test_closing_cancels_a_fetch_in_flight() -> None:
    clock, fetch = _Clock(), _Fetch()
    fetch.release = asyncio.Event()
    cache = _cache(fetch, clock)
    assert await cache.first("k", timeout_s=0.01) is None

    await cache.close()

    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("vibe-refresh-")
    ]
    assert cache.cached("k") is None


@pytest.mark.asyncio
async def test_concurrent_first_reads_all_find_the_copy_on_disk() -> None:
    clock, fetch = _Clock(), _Fetch()
    store = _Store({"k": Cached("disk", stored_at=clock.now - 60)})
    cache = _cache(fetch, clock, store)

    entries = await asyncio.gather(cache.load("k"), cache.load("k"))

    assert [entry.value if entry else None for entry in entries] == ["disk", "disk"]


@pytest.mark.asyncio
async def test_a_disk_read_holds_nothing_of_the_reader_that_started_it() -> None:
    # The first session to read a key must not stay reachable through the
    # cache, which lives as long as the process.
    import contextvars

    clock, fetch = _Clock(), _Fetch()
    store = _Store({"k": Cached("disk", stored_at=clock.now - 60)})
    cache = _cache(fetch, clock, store)
    session: contextvars.ContextVar[object] = contextvars.ContextVar("session")

    class _Session:
        pass

    opened = _Session()
    alive = weakref.ref(opened)
    session.set(opened)
    entry = await cache.load("k")
    session.set(None)
    del opened
    gc.collect()

    assert entry is not None and entry.value == "disk"
    assert alive() is None
