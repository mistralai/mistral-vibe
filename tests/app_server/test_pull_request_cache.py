from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest

from vibe.app_server import _pull_request_cache as cache_module
from vibe.app_server._pull_request_cache import (
    CachedPullRequest,
    PullRequestCache,
    Read,
    ReadFailure,
)
from vibe.core.git.forge_sync import (
    ForgeSyncError,
    GitHubPullRequestRef,
    GitHubQuota,
    PullRequestSnapshot,
    RateLimitedSyncError,
)
from vibe.core.session.artifacts import GitHubPullRequestDetails


def _details(number: int) -> GitHubPullRequestDetails:
    return GitHubPullRequestDetails(
        repo_host="github.com",
        repo_owner="mistralai",
        repo_name="vibe",
        pull_request_number=number,
        state="open",
        source_branch="",
        target_branch="",
        author="",
        provider_created_at=None,
        provider_updated_at=None,
        provider_closed_at=None,
        provider_merged_at=None,
        merge_commit_sha=None,
    )


def _key(number: int) -> str:
    return f"github.com/mistralai/vibe/pull/{number}"


def _snapshot(state: str = "open") -> PullRequestSnapshot:
    return PullRequestSnapshot(
        state=state,  # type: ignore[arg-type]
        title="Fix login",
        source_branch="fix/login",
        target_branch="main",
        author="pierre",
    )


class _GitHub:
    """GitHub as the cache's batch readers see it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fingerprints: dict[int, str] = {}
        self.states: dict[int, str] = {}
        self.missing: set[int] = set()
        self.check_batches: list[list[int]] = []
        self.full_batches: list[list[int]] = []
        self.quota: GitHubQuota | None = None
        self.error: Exception | None = None
        monkeypatch.setattr(cache_module, "find_cli_binary", lambda name: name)
        monkeypatch.setattr(cache_module, "read_github_checks", self.checks)
        monkeypatch.setattr(cache_module, "read_github_pull_requests", self.full)

    def checks(self, gh_path, host, refs: list[GitHubPullRequestRef]):
        if self.error is not None:
            raise self.error
        self.check_batches.append([ref.number for ref in refs])
        return (
            {
                ref: None
                if ref.number in self.missing
                else self.fingerprints.get(ref.number, "a")
                for ref in refs
            },
            self.quota,
        )

    def full(self, gh_path, host, refs: list[GitHubPullRequestRef], checked_at):
        self.full_batches.append([ref.number for ref in refs])
        return (
            {ref: _snapshot(self.states.get(ref.number, "open")) for ref in refs},
            self.quota,
        )


async def _pass(cache: PullRequestCache) -> None:
    await cache._pass()


def _manual(cache: PullRequestCache) -> PullRequestCache:
    """A cache whose refresh passes the test runs itself (``_pass``)."""
    cache._ensure_running = lambda: None  # type: ignore[method-assign]
    return cache


class _Owner:
    """An owner of watches that records the reads it is told of."""

    def __init__(self, cache: PullRequestCache, *, attached: bool = False) -> None:
        self.reads: list[Read] = []
        self.cache = cache
        cache.own(self, self.reads.append, attached=attached)

    def watch(self, session_id: str, *numbers: int) -> None:
        self.cache.watch(self, session_id, [(_key(n), _details(n)) for n in numbers])

    def states(self) -> list[tuple[str, bool]]:
        return [
            (
                read.entry.error_type
                if isinstance(read.entry, ReadFailure)
                else read.entry.snapshot.state,
                read.changed,
            )
            for read in self.reads
        ]


@pytest.fixture
def cache(tmp_path: Path) -> PullRequestCache:
    return _manual(PullRequestCache(tmp_path / "pull-requests"))


@pytest.mark.asyncio
async def test_one_check_query_covers_every_watched_pull_request(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    owner = _Owner(cache)
    owner.watch("s1", 1, 2)
    owner.watch("s2", 2, 3)

    await _pass(cache)

    # One query for the three PRs, the shared one included once.
    assert [sorted(batch) for batch in github.check_batches] == [[1, 2, 3]]
    assert sorted(n for batch in github.full_batches for n in batch) == [1, 2, 3]
    entry = cache.get(_key(2))
    assert entry is not None
    assert entry.snapshot.source_branch == "fix/login"


@pytest.mark.asyncio
async def test_an_unchanged_fingerprint_skips_the_full_read(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    _Owner(cache).watch("s1", 1, 2)
    await _pass(cache)
    github.full_batches.clear()
    # The next refresh is due.
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)
    github.fingerprints[2] = "b"

    await _pass(cache)

    assert github.full_batches == [[2]]


@pytest.mark.asyncio
async def test_a_pull_request_github_no_longer_returns_is_not_read_in_full(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    owner = _Owner(cache)
    owner.watch("s1", 1)
    await _pass(cache)
    github.full_batches.clear()
    owner.reads.clear()
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)
    github.missing.add(1)

    await _pass(cache)
    await _pass(cache)

    assert github.full_batches == []
    assert owner.states() == [("NotFound", False), ("NotFound", False)]
    entry = cache.get(_key(1))
    assert entry is not None
    assert entry.snapshot.state == "open"


@pytest.mark.asyncio
async def test_a_client_refresh_checks_first_and_a_turn_end_reads_in_full(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    _Owner(cache).watch("s1", 1)
    await _pass(cache)
    github.check_batches.clear()
    github.full_batches.clear()

    # Fresh, and nothing changed: a client asking costs one check.
    cache.refresh([_key(1)], full=False)
    await _pass(cache)
    assert (github.check_batches, github.full_batches) == ([[1]], [])

    # A turn end may change what the check does not see.
    cache.refresh([_key(1)], full=True)
    await _pass(cache)
    assert github.full_batches == [[1]]


@pytest.mark.asyncio
async def test_a_fresh_read_by_another_process_is_not_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    first = _manual(PullRequestCache(tmp_path / "pull-requests"))
    second = _manual(PullRequestCache(tmp_path / "pull-requests"))
    _Owner(first).watch("s1", 1)
    _Owner(second).watch("s2", 1)

    await _pass(first)
    await _pass(second)

    assert github.check_batches == [[1]]


@pytest.mark.asyncio
async def test_a_pull_request_another_process_is_reading_is_left_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    directory = tmp_path / "pull-requests"
    cache = _manual(PullRequestCache(directory))
    _Owner(cache).watch("s1", 1)
    directory.mkdir(parents=True, exist_ok=True)
    cache_module._claim(directory, _key(1), cache_module.time.time())

    await _pass(cache)

    assert github.check_batches == []


@pytest.mark.asyncio
async def test_an_asked_for_read_waits_for_another_process_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other process may only check; a turn end wants a full read.
    github = _GitHub(monkeypatch)
    monkeypatch.setattr(cache_module, "_CLAIM_RETRY_SECONDS", 0.0)
    directory = tmp_path / "pull-requests"
    cache = _manual(PullRequestCache(directory))
    _Owner(cache).watch("s1", 1)
    directory.mkdir(parents=True, exist_ok=True)
    cache_module._claim(directory, _key(1), cache_module.time.time())

    cache.refresh([_key(1)], full=True)
    await _pass(cache)
    assert github.full_batches == []

    cache_module._release_claim(directory, _key(1))
    await asyncio.sleep(0)
    await _pass(cache)
    assert github.full_batches == [[1]]


def test_an_unwatched_pull_request_reads_its_last_read_from_disk(
    tmp_path: Path,
) -> None:
    # Listing a peer session's PRs must not show the status at creation
    # when another process read them.
    directory = tmp_path / "pull-requests"
    now = cache_module._now_iso()
    cache_module._write_entry(
        directory,
        CachedPullRequest(
            external_key=_key(1),
            snapshot=_snapshot("merged"),
            read_at=now,
            full_read_at=now,
        ),
    )

    entry = PullRequestCache(directory).get(_key(1))

    assert entry is not None
    assert entry.snapshot.state == "merged"


@pytest.mark.asyncio
async def test_merged_pull_requests_are_settled_and_closed_ones_read_at_turn_end(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    github.states = {1: "merged", 2: "closed"}
    _Owner(cache).watch("s1", 1, 2)
    await _pass(cache)
    github.check_batches.clear()
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)

    await _pass(cache)
    cache.refresh([_key(1), _key(2)], full=False)
    await _pass(cache)
    assert github.check_batches == []

    # A turn end may have reopened the closed one.
    cache.refresh([_key(1), _key(2)], full=True)
    await _pass(cache)
    assert github.full_batches[-1] == [2]


@pytest.mark.asyncio
async def test_a_rate_limit_pauses_the_host_until_its_reset(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    github.error = RateLimitedSyncError("rate limited", reset_at=4_102_444_800.0)
    _Owner(cache).watch("s1", 1)

    await _pass(cache)
    github.error = None
    cache.refresh([_key(1)], full=True)
    await _pass(cache)

    assert github.check_batches == []
    assert github.full_batches == []


@pytest.mark.asyncio
async def test_a_full_read_asked_for_during_a_rate_limit_runs_after_it(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    _Owner(cache).watch("s1", 1)
    await _pass(cache)
    github.full_batches.clear()

    # A turn end asks for a full read; the pass meets a rate limit.
    github.error = RateLimitedSyncError("rate limited", reset_at=4_102_444_800.0)
    cache.refresh([_key(1)], full=True)
    await _pass(cache)
    github.error = None
    await _pass(cache)
    assert github.full_batches == []

    for host in cache._hosts.values():
        host.paused_until = 0.0
    await _pass(cache)

    assert github.full_batches == [[1]]


@pytest.mark.asyncio
async def test_a_failed_check_does_not_stop_an_asked_for_full_read(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    owner = _Owner(cache)
    owner.watch("s1", 1, 2)
    await _pass(cache)
    github.full_batches.clear()
    owner.reads.clear()
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)

    github.error = ForgeSyncError("gh api graphql timed out")
    cache.refresh([_key(1)], full=True)
    await _pass(cache)

    assert github.full_batches == [[1]]
    assert sorted(owner.states()) == [("ForgeSyncError", False), ("open", False)]


@pytest.mark.asyncio
async def test_background_reads_keep_the_quota_reserve(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    github.quota = GitHubQuota(remaining=400, limit=5000, reset_at=4_102_444_800.0)
    _Owner(cache).watch("s1", 1)
    await _pass(cache)
    github.check_batches.clear()
    github.full_batches.clear()
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)

    await _pass(cache)
    assert github.check_batches == []

    # The user asked: the reserve is for that.
    cache.refresh([_key(1)], full=False)
    await _pass(cache)
    assert github.check_batches == [[1]]


@pytest.mark.asyncio
async def test_owners_learn_which_reads_changed_the_status(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = _GitHub(monkeypatch)
    owner = _Owner(cache)
    owner.watch("s1", 1)
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)

    await _pass(cache)
    github.fingerprints[1] = "b"
    await _pass(cache)
    # A failed read keeps the status; the owner learns of it.
    github.error = ForgeSyncError("gh failed")
    await _pass(cache)
    github.error = None
    github.states[1] = "merged"
    github.fingerprints[1] = "c"
    await _pass(cache)

    assert owner.states() == [
        ("open", True),
        ("open", False),
        ("ForgeSyncError", False),
        ("merged", True),
    ]
    assert all(read.local for read in owner.reads)


@pytest.mark.asyncio
async def test_each_owner_hears_its_sessions_once(
    cache: PullRequestCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Desktop's sidebar subscription and an attached session watch the same
    # session: the attached session announces it, the subscription does not.
    _GitHub(monkeypatch)
    attached = _Owner(cache, attached=True)
    sidebar = _Owner(cache)
    attached.watch("s1", 1)
    sidebar.watch("s1", 1)
    sidebar.watch("s2", 2)

    await _pass(cache)

    assert [read.session_id for read in attached.reads] == ["s1"]
    assert [read.session_id for read in sidebar.reads] == ["s2"]

    # The session closes: the sidebar still shows it and keeps it fresh.
    cache.release(attached)
    monkeypatch.setattr(cache_module, "REFRESH_SECONDS", 0.0)
    sidebar.reads.clear()
    await _pass(cache)
    assert sorted(read.session_id for read in sidebar.reads) == ["s1", "s2"]

    # The sidebar goes away: nothing is watched, nothing is read.
    cache.release(sidebar)
    github_after = _GitHub(monkeypatch)
    await _pass(cache)
    assert github_after.check_batches == []


@pytest.mark.asyncio
async def test_a_read_by_another_process_reaches_the_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "pull-requests"

    def write(state: str) -> None:
        now = cache_module._now_iso()
        cache_module._write_entry(
            directory,
            CachedPullRequest(
                external_key=_key(1),
                snapshot=_snapshot(state),
                read_at=now,
                full_read_at=now,
            ),
        )

    # Fresh: this process's refresher does not read GitHub itself.
    write("open")
    github = _GitHub(monkeypatch)
    cache = PullRequestCache(directory)
    reads: asyncio.Queue[Read] = asyncio.Queue()
    cache.own("sidebar", reads.put_nowait, attached=False)
    cache.watch("sidebar", "s1", [(_key(1), _details(1))])

    # Write until the file watch, which starts in the background, sees it.
    async def delivered() -> Read:
        while True:
            write("merged")
            with suppress(TimeoutError):
                return await asyncio.wait_for(reads.get(), timeout=0.5)

    read = await asyncio.wait_for(delivered(), timeout=10)
    assert isinstance(read.entry, CachedPullRequest)
    assert read.entry.snapshot.state == "merged"
    assert not read.local
    assert github.check_batches == []
    await cache.aclose()
