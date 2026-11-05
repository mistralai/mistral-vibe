"""The status of the pull requests the sessions refer to, read from GitHub/GitLab.

A cache of provider reads, not session state: a session's ``meta.json`` keeps
which PRs/MRs it refers to; their status lives here, one entry per PR/MR
(keyed by ``external_key``) for the whole machine. Each app-server process
loads the entries it needs, keeps them fresh, and tells its listeners when
one changes.

Freshness: every open or draft PR that a process watches is checked every
``REFRESH_SECONDS``. On GitHub, one cheap query checks up to 50 PRs
(``read_github_checks``), and only the PRs whose check fingerprint changed —
or whose last full read is older than ``FULL_READ_SECONDS`` (the merge state
moves with the base branch, which the fingerprint does not see) — get a full
read, a few PRs per query. GitLab MRs get a full read each. A merged PR is
not read again; a closed one is read when a session asks for it (turn end).

Owners: an attached session, or a client's subscription to the sessions it
shows, watches PRs and is told of their reads; a subscription leaves the
sessions attached in this process to their own announcements. A PR no owner
watches is not refreshed.

Quota: a machine-wide file lock covers the choice of the PRs to read, and a
claim file per PR keeps two processes from reading the same PR; a PR read by
one process is fresh for the others. Background refreshes stop while the
provider's quota is under ``RESERVE_RATIO`` of its limit; reads a client or
a turn asked for may use the rest. A rate limit pauses the provider host
until its reset time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Literal
import weakref

from pydantic import BaseModel, ValidationError
import watchfiles

from vibe.core.git.forge_sync import (
    GITHUB_CHECK_BATCH,
    GITHUB_FULL_READ_BATCH,
    ForgeSyncError,
    GitHubPullRequestRef,
    GitHubQuota,
    PullRequestSnapshot,
    RateLimitedSyncError,
    fetch_gitlab_merge_request,
    read_github_checks,
    read_github_pull_requests,
)
from vibe.core.paths import VIBE_HOME
from vibe.core.session.artifact_detector import find_cli_binary
from vibe.core.session.artifacts import (
    GitHubPullRequestDetails,
    GitLabMergeRequestDetails,
)
from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock
from vibe.observability.logging import logger

__all__ = [
    "CachedPullRequest",
    "PullRequestCache",
    "PullRequestDetails",
    "Read",
    "ReadFailure",
    "pull_request_cache",
]

PullRequestDetails = GitHubPullRequestDetails | GitLabMergeRequestDetails

REFRESH_SECONDS = 60.0
FULL_READ_SECONDS = 600.0
RESERVE_RATIO = 0.1
_RATE_LIMIT_PAUSE_SECONDS = 300.0
# A claim older than this is a crashed reader's: the PR is read again.
_CLAIM_SECONDS = 120.0
# When to ask again for a read another process had claimed.
_CLAIM_RETRY_SECONDS = 15.0
_WATCH_DEBOUNCE_MS = 200


class CachedPullRequest(BaseModel):
    """One PR/MR as last read from its provider."""

    external_key: str
    snapshot: PullRequestSnapshot
    # When the provider last confirmed the snapshot (ISO, UTC): the
    # artifact's ``provider_updated_at``.
    read_at: str
    # When the snapshot itself was read.
    full_read_at: str
    # On GitHub, the check fingerprint at the snapshot's read
    # (``read_github_checks``): a full read is due when it changes.
    fingerprint: str | None = None


@dataclass(frozen=True)
class ReadFailure:
    """A read of a watched PR/MR that failed: its last status stands."""

    external_key: str
    error_type: str


@dataclass(frozen=True)
class Read:
    """A new read of a PR/MR that an owner watches."""

    session_id: str
    entry: CachedPullRequest | ReadFailure
    # Whether the status changed (more than its read times).
    changed: bool
    # Whether this process read it (another process's read arrives through
    # the cache files).
    local: bool


Listener = Callable[[Read], None]


@dataclass
class _Owner:
    """Who keeps PRs fresh and is told of their reads: an attached session,
    or a client's subscription to the sessions it shows.
    """

    listener: Listener
    # The PRs/MRs of each session the owner watches.
    sessions: dict[str, set[str]] = field(default_factory=dict)
    # Attached sessions announce their own PRs; a subscription leaves those
    # sessions to them.
    attached: bool = False


@dataclass
class _Host:
    paused_until: float = 0.0
    quota: GitHubQuota | None = None


# How a PR is due at a pass: by its age (background, keeps the reserve), now
# with the cheap check first (a client asked), or now in full (a turn end may
# have changed what the check does not see, like a reopened PR).
_Due = Literal["background", "check", "full"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso_seconds(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _host_key(details: PullRequestDetails) -> str:
    provider = "github" if isinstance(details, GitHubPullRequestDetails) else "gitlab"
    return f"{provider}:{details.repo_host.lower()}"


def _github_ref(details: GitHubPullRequestDetails) -> GitHubPullRequestRef | None:
    if details.pull_request_number is None:
        return None
    return GitHubPullRequestRef(
        owner=details.repo_owner,
        repo=details.repo_name,
        number=details.pull_request_number,
    )


def _entry_file(directory: Path, external_key: str) -> Path:
    digest = hashlib.sha256(external_key.encode()).hexdigest()[:32]
    return directory / f"{digest}.json"


class PullRequestCache:
    """The PR/MR status cache and refresher of one app-server process."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._entries: dict[str, CachedPullRequest] = {}
        # How to read each watched PR/MR.
        self._details: dict[str, PullRequestDetails] = {}
        self._owners: dict[object, _Owner] = {}
        self._hosts: dict[str, _Host] = {}
        self._due: dict[str, _Due] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None

    # --- What sessions see -------------------------------------------------

    def get(self, external_key: str) -> CachedPullRequest | None:
        """The last read of the PR/MR, by this process or another one.

        A PR this process watches is kept current in memory (its refreshes,
        and the cache files' changes); another one is read from its file.
        """
        if external_key in self._details:
            return self._entries.get(external_key)
        return _read_entry(self._directory, external_key)

    def own(self, owner: object, listener: Listener, *, attached: bool) -> None:
        """Register an owner of watches, told of each read of its PRs."""
        self._owners[owner] = _Owner(listener=listener, attached=attached)

    def release(self, owner: object) -> None:
        """Drop the owner and the watches no other owner holds."""
        self._owners.pop(owner, None)
        self._forget_unwatched()

    def watch(
        self,
        owner: object,
        session_id: str,
        artifacts: Iterable[tuple[str, PullRequestDetails]],
    ) -> None:
        """Keep the session's PRs/MRs fresh for ``owner``: all of them, in
        place of the ones it watched for that session before.
        """
        registered = self._owners.get(owner)
        if registered is None:
            return
        keys: set[str] = set()
        for external_key, details in artifacts:
            keys.add(external_key)
            if external_key not in self._details:
                self._details[external_key] = details
                self._load(external_key)
                # Read it now if it is stale, not at the next pass.
                self._wake.set()
        registered.sessions[session_id] = keys
        self._forget_unwatched()
        self._ensure_running()

    def unwatch(self, owner: object, session_id: str) -> None:
        registered = self._owners.get(owner)
        if registered is not None:
            registered.sessions.pop(session_id, None)
        self._forget_unwatched()

    def _forget_unwatched(self) -> None:
        watched = self._watched_keys()
        for external_key in list(self._details):
            if external_key not in watched:
                del self._details[external_key]
                self._entries.pop(external_key, None)

    def refresh(self, external_keys: Iterable[str], *, full: bool) -> None:
        """Read these PRs/MRs at once: the cheap check first, or in full."""
        for external_key in external_keys:
            if full or self._due.get(external_key) != "full":
                self._due[external_key] = "full" if full else "check"
        self._wake.set()

    async def read_now(self, external_key: str, details: PullRequestDetails) -> None:
        """Read one PR/MR now (a new artifact): its session waits for it."""
        if time.time() < self._host(details).paused_until:
            return
        for entry in await asyncio.to_thread(
            self._read_and_write, {external_key: details}, {external_key: "full"}, {}
        ):
            self._store(entry, local=True)

    async def aclose(self) -> None:
        for task in (self._task, self._watch_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._watch_task = None

    # --- Bookkeeping -------------------------------------------------------

    def _watched_keys(self) -> set[str]:
        return {
            key
            for owner in self._owners.values()
            for keys in owner.sessions.values()
            for key in keys
        }

    def _attached_sessions(self) -> set[str]:
        return {
            session_id
            for owner in self._owners.values()
            if owner.attached
            for session_id in owner.sessions
        }

    def _host(self, details: PullRequestDetails) -> _Host:
        return self._hosts.setdefault(_host_key(details), _Host())

    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="vibe-pr-refresh")
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(
                self._watch_files(), name="vibe-pr-cache-watch"
            )

    def _load(self, external_key: str) -> None:
        if external_key in self._entries:
            return
        entry = _read_entry(self._directory, external_key)
        if entry is not None:
            self._entries[external_key] = entry

    def _store(self, entry: CachedPullRequest | ReadFailure, *, local: bool) -> None:
        changed = False
        if isinstance(entry, CachedPullRequest):
            previous = self._entries.get(entry.external_key)
            if previous is not None and previous.read_at >= entry.read_at:
                return
            self._entries[entry.external_key] = entry
            changed = previous is None or _status(previous) != _status(entry)
        attached = self._attached_sessions()
        for owner in list(self._owners.values()):
            for session_id, keys in owner.sessions.items():
                if entry.external_key not in keys:
                    continue
                if not owner.attached and session_id in attached:
                    continue
                try:
                    owner.listener(Read(session_id, entry, changed, local))
                except Exception:
                    logger.warning("Pull request listener failed", exc_info=True)

    # --- Refresh -----------------------------------------------------------

    async def _run(self) -> None:
        while True:
            # Cleared before the pass: a wake during the pass runs another.
            self._wake.clear()
            try:
                await self._pass()
            except Exception:
                logger.warning("Pull request refresh pass failed", exc_info=True)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), REFRESH_SECONDS)

    async def _pass(self) -> None:
        asked = self._due
        self._due = {}
        now = time.time()
        due: dict[str, PullRequestDetails] = {}
        how: dict[str, _Due] = {}
        for external_key, details in self._details.items():
            asked_for = asked.get(external_key)
            if now < self._host(details).paused_until:
                if asked_for is not None:
                    # Read once the pause ends.
                    self._due[external_key] = asked_for
                continue
            entry = self._entries.get(external_key)
            state = entry.snapshot.state if entry else details.state
            if state == "merged":
                continue
            if asked_for == "full" or (asked_for == "check" and state != "closed"):
                # Asked for by the user or a turn end: may use the reserve.
                due[external_key] = details
                how[external_key] = asked_for
            elif state != "closed" and self._quota_allows(details):
                due[external_key] = details
                how[external_key] = "background"
        if not due:
            return
        known = {key: self._entries[key] for key in due if key in self._entries}
        try:
            reads, claimed = await asyncio.to_thread(self._refresh, due, how, known)
        except BaseException:
            # Asked-for reads stay due.
            for external_key, asked_for in asked.items():
                self._due.setdefault(external_key, asked_for)
            raise
        for entry in reads:
            self._store(entry, local=True)
        # A rate limit in this pass paused the host: the asked-for reads wait
        # for the end of the pause.
        now = time.time()
        for external_key, asked_for in how.items():
            if (
                asked_for != "background"
                and now < self._host(due[external_key]).paused_until
                and self._due.get(external_key) != "full"
            ):
                self._due[external_key] = asked_for
        # Another process is reading these: its read may not be the one asked
        # for (a background check where a turn end wants a full read). Ask
        # again once its claim is likely over.
        retry: dict[str, _Due] = {
            key: how[key] for key in claimed if how[key] != "background"
        }
        if retry:
            asyncio.get_running_loop().call_later(
                _CLAIM_RETRY_SECONDS, self._ask_again, retry
            )

    def _ask_again(self, asked: dict[str, _Due]) -> None:
        for external_key, asked_for in asked.items():
            if self._due.get(external_key) != "full":
                self._due[external_key] = asked_for
        self._wake.set()

    def _quota_allows(self, details: PullRequestDetails) -> bool:
        quota = self._host(details).quota
        if quota is None or time.time() >= quota.reset_at:
            return True
        return quota.remaining >= quota.limit * RESERVE_RATIO

    def _refresh(
        self,
        due: dict[str, PullRequestDetails],
        how: dict[str, _Due],
        known: dict[str, CachedPullRequest],
    ) -> tuple[list[CachedPullRequest | ReadFailure], list[str]]:
        """Read the due PRs that no process read or claimed within the refresh
        interval. Runs in a thread.

        The machine's lock covers the choice and the claim, not the reads: a
        PR another process claimed is left to it, and its read arrives
        through the cache files. Returns the reads and the claimed PRs.
        """
        claimed: list[str] = []
        with _directory_lock(self._directory):
            now = time.time()
            stale: dict[str, PullRequestDetails] = {}
            for external_key, details in due.items():
                on_disk = _read_entry(self._directory, external_key)
                if on_disk is not None and (
                    external_key not in known
                    or on_disk.read_at > known[external_key].read_at
                ):
                    known[external_key] = on_disk
                entry = known.get(external_key)
                fresh = (
                    entry is not None
                    and now - _iso_seconds(entry.read_at) < REFRESH_SECONDS
                )
                if how[external_key] == "background" and fresh:
                    continue
                if _claimed(self._directory, external_key, now):
                    claimed.append(external_key)
                    continue
                stale[external_key] = details
            for external_key in stale:
                _claim(self._directory, external_key, now)
        try:
            return self._read_and_write(stale, how, known), claimed
        finally:
            for external_key in stale:
                _release_claim(self._directory, external_key)

    def _read_and_write(
        self,
        stale: dict[str, PullRequestDetails],
        how: dict[str, _Due],
        known: dict[str, CachedPullRequest],
    ) -> list[CachedPullRequest | ReadFailure]:
        read = self._read(stale, how, known)
        for entry in read:
            if isinstance(entry, CachedPullRequest):
                _write_entry(self._directory, entry)
        return read

    def _read(
        self,
        stale: dict[str, PullRequestDetails],
        how: dict[str, _Due],
        known: dict[str, CachedPullRequest],
    ) -> list[CachedPullRequest | ReadFailure]:
        by_host: dict[str, dict[str, PullRequestDetails]] = {}
        for external_key, details in stale.items():
            by_host.setdefault(_host_key(details), {})[external_key] = details
        read: list[CachedPullRequest | ReadFailure] = []
        for host_key, prs in by_host.items():
            host = self._hosts.setdefault(host_key, _Host())
            try:
                if host_key.startswith("github:"):
                    read.extend(self._read_github(host, prs, how, known))
                else:
                    read.extend(self._read_gitlab(prs))
            except RateLimitedSyncError as exc:
                host.paused_until = (
                    exc.reset_at + 1.0
                    if exc.reset_at is not None
                    else time.time() + _RATE_LIMIT_PAUSE_SECONDS
                )
                logger.warning(
                    "Pull request reads paused after a rate limit",
                    extra={"host": host_key, "paused_until": host.paused_until},
                )
                read.extend(_failures(prs, exc))
            except ForgeSyncError as exc:
                logger.info(
                    "Pull request read failed; keeping the last status",
                    extra={"host": host_key, "reason": str(exc)},
                )
                read.extend(_failures(prs, exc))
        return read

    def _read_github(
        self,
        host: _Host,
        prs: dict[str, PullRequestDetails],
        how: dict[str, _Due],
        known: dict[str, CachedPullRequest],
    ) -> list[CachedPullRequest | ReadFailure]:
        """Check the PRs in batches, then read in full the ones that changed.

        A PR whose check fingerprint did not change keeps its snapshot with a
        new read time, unless its full read is older than
        ``FULL_READ_SECONDS`` or a full read was asked for.
        """
        gh_path = find_cli_binary("gh")
        if gh_path is None:
            raise ForgeSyncError("gh CLI not found (system or bundled)")
        refs: dict[GitHubPullRequestRef, str] = {}
        repo_host = ""
        for external_key, details in prs.items():
            assert isinstance(details, GitHubPullRequestDetails)
            ref = _github_ref(details)
            if ref is not None:
                refs[ref] = external_key
                repo_host = details.repo_host
        read: list[CachedPullRequest | ReadFailure] = []
        fingerprints: dict[GitHubPullRequestRef, str | None] = {}
        try:
            for batch in _batches(list(refs), GITHUB_CHECK_BATCH):
                checked, host.quota = read_github_checks(gh_path, repo_host, batch)
                fingerprints.update(checked)
        except RateLimitedSyncError:
            raise
        except ForgeSyncError as exc:
            # A full read does not need the check: the asked-for ones go on.
            go_on = {
                ref: key
                for ref, key in refs.items()
                if ref in fingerprints or how[key] == "full"
            }
            if not go_on:
                raise
            logger.info(
                "Pull request check failed; reading the others",
                extra={"reason": str(exc)},
            )
            read.extend(
                ReadFailure(key, type(exc).__name__)
                for ref, key in refs.items()
                if ref not in go_on
            )
            refs = go_on
        to_read: list[GitHubPullRequestRef] = []
        now = time.time()
        for ref, external_key in refs.items():
            entry = known.get(external_key)
            if fingerprints.get(ref) is None and how[external_key] != "full":
                # GitHub did not return it (deleted, or access lost): a full
                # read would fail the same way at every pass.
                read.append(ReadFailure(external_key, "NotFound"))
                continue
            if (
                how[external_key] == "full"
                or entry is None
                or entry.fingerprint != fingerprints.get(ref)
                or now - _iso_seconds(entry.full_read_at) >= FULL_READ_SECONDS
            ):
                to_read.append(ref)
                continue
            read.append(entry.model_copy(update={"read_at": _now_iso()}))
        for batch in _batches(to_read, GITHUB_FULL_READ_BATCH):
            read_at = _now_iso()
            results, host.quota = read_github_pull_requests(
                gh_path, repo_host, batch, read_at
            )
            for ref, result in results.items():
                if isinstance(result, ForgeSyncError):
                    logger.info(
                        "Pull request read failed; keeping the last status",
                        extra={"reason": str(result)},
                    )
                    read.append(ReadFailure(refs[ref], type(result).__name__))
                    continue
                read.append(
                    CachedPullRequest(
                        external_key=refs[ref],
                        snapshot=result,
                        read_at=read_at,
                        full_read_at=read_at,
                        fingerprint=fingerprints.get(ref),
                    )
                )
        return read

    def _read_gitlab(
        self, prs: dict[str, PullRequestDetails]
    ) -> list[CachedPullRequest | ReadFailure]:
        read: list[CachedPullRequest | ReadFailure] = []
        for external_key, details in prs.items():
            assert isinstance(details, GitLabMergeRequestDetails)
            read_at = _now_iso()
            try:
                snapshot = fetch_gitlab_merge_request(details, read_at)
            except RateLimitedSyncError:
                raise
            except ForgeSyncError as exc:
                logger.info(
                    "Merge request read failed; keeping the last status",
                    extra={"reason": str(exc)},
                )
                read.append(ReadFailure(external_key, type(exc).__name__))
                continue
            read.append(
                CachedPullRequest(
                    external_key=external_key,
                    snapshot=snapshot,
                    read_at=read_at,
                    full_read_at=read_at,
                )
            )
        return read

    # --- Other processes' reads --------------------------------------------

    async def _watch_files(self) -> None:
        await asyncio.to_thread(self._directory.mkdir, parents=True, exist_ok=True)
        async for changes in watchfiles.awatch(
            self._directory,
            debounce=_WATCH_DEBOUNCE_MS,
            recursive=False,
            watch_filter=lambda _change, path: path.endswith(".json"),
        ):
            names = {Path(path).name for _change, path in changes}
            for external_key in list(self._details):
                if _entry_file(self._directory, external_key).name not in names:
                    continue
                entry = await asyncio.to_thread(
                    _read_entry, self._directory, external_key
                )
                # Its own writes come back too: _store drops what it has.
                if entry is not None:
                    self._store(entry, local=False)


def _failures(
    prs: dict[str, PullRequestDetails], error: Exception
) -> list[ReadFailure]:
    return [ReadFailure(key, type(error).__name__) for key in prs]


def _status(entry: CachedPullRequest) -> PullRequestSnapshot:
    """The entry's snapshot without its read time."""
    checks = entry.snapshot.checks
    if checks is None:
        return entry.snapshot
    return replace(entry.snapshot, checks=checks.model_copy(update={"checked_at": ""}))


def _batches[T](items: list[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _read_entry(directory: Path, external_key: str) -> CachedPullRequest | None:
    path = _entry_file(directory, external_key)
    try:
        entry = CachedPullRequest.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError):
        logger.debug("Unreadable pull request cache entry", exc_info=True)
        return None
    return entry if entry.external_key == external_key else None


def _write_entry(directory: Path, entry: CachedPullRequest) -> None:
    """Write an entry; a failed write (a reader holds the file open on
    Windows) leaves the read in memory only.
    """
    temporary: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp", dir=directory, delete=False, encoding="utf-8"
        ) as file:
            temporary = Path(file.name)
            file.write(entry.model_dump_json())
        os.replace(temporary, _entry_file(directory, entry.external_key))
    except OSError:
        logger.debug("Pull request cache entry not written", exc_info=True)
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _claim_file(directory: Path, external_key: str) -> Path:
    return _entry_file(directory, external_key).with_suffix(".claim")


def _claimed(directory: Path, external_key: str, now: float) -> bool:
    """Whether another process is reading the PR now."""
    try:
        claimed_at = float(_claim_file(directory, external_key).read_text())
    except (OSError, ValueError):
        return False
    return now - claimed_at < _CLAIM_SECONDS


def _claim(directory: Path, external_key: str, now: float) -> None:
    path = _claim_file(directory, external_key)
    temporary = path.with_suffix(".claim.tmp")
    with suppress(OSError):
        temporary.write_text(str(now))
        os.replace(temporary, path)


def _release_claim(directory: Path, external_key: str) -> None:
    with suppress(OSError):
        _claim_file(directory, external_key).unlink(missing_ok=True)


@contextmanager
def _directory_lock(directory: Path) -> Iterator[None]:
    """Serialize the choice of the PRs to read across the processes of this
    machine (held for file reads and writes only, never for a network read).
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as file:
        acquire_file_lock(file, blocking=True)
        try:
            yield
        finally:
            release_file_lock(file)


# One cache per event loop: an app-server process serves every channel on
# one loop, so its sessions share the cache; test loops each get their own.
_CACHES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, PullRequestCache] = (
    weakref.WeakKeyDictionary()
)


def pull_request_cache() -> PullRequestCache:
    loop = asyncio.get_running_loop()
    cache = _CACHES.get(loop)
    if cache is None:
        cache = PullRequestCache(VIBE_HOME.path / "cache" / "pull-requests")
        _CACHES[loop] = cache
    return cache
