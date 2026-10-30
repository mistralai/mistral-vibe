"""Copy-on-write worktree creation from an idle template checkout (macOS only).

Each repository gets one template: a detached worktree under
`~/.vibe/worktrees/.templates/<bucket>`, on the remote's default branch, that
nobody works in. A new worktree is populated by cloning the template's
top-level entries and its index with APFS `clonefile(2)`. The regular checkout
that follows then writes only the files that differ between the template's
commit and the new worktree's. Cloned files share their data blocks with the
template until one copy changes, so the worktree costs little disk and the
file system sees a few hundred events, not one per file.

Cloned files get new inodes and change times, so with git's default stat check
the first `git status` rehashes every file (13s on an 87k-file repository).
The new worktree therefore keeps `core.checkStat=minimal` in its own config,
which needs `extensions.worktreeConfig` in the repository. That is the one
change this makes to the user's repository, made only once a clone has been
shown to work on this volume.

A template is deleted once its repository has no managed worktree left.

Apple discourages `clonefile(2)` on directories: the kernel blocks writers to
the source tree until the clone ends. The source here is the idle template,
and each call clones one top-level entry (0.4s at most on that repository).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
import ctypes
from enum import StrEnum, auto
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from uuid import uuid4

from vibe.core.git.errors import GitError
from vibe.core.git.repo import GitRepo
from vibe.core.git.worktree.record import TEMPLATES_DIR_NAME, WorktreeClaim
from vibe.core.paths import WORKTREES_DIR
from vibe.core.session.session_lease import _acquire_file_lock, _release_file_lock
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager
from vibe.observability.logging import logger

_CLONE_NOFOLLOW = 0x0001
_WORKTREE_CONFIG = (("core.checkStat", "minimal"), ("core.trustctime", "false"))
_refreshes: dict[str, threading.Thread] = {}
_refreshes_lock = threading.Lock()


class CopyOnWriteOutcome(StrEnum):
    CLONED = auto()
    # No ready template: the first worktree of a repository, or one created
    # while the template moves to a newer commit. The template is (re)built
    # in the background after this creation.
    TEMPLATE_PENDING = auto()
    # Not macOS, a worktrees directory on a file system that does not clone,
    # an untrusted repository, or a repository whose config cannot be split
    # per worktree. Nothing in the repository was changed.
    UNSUPPORTED = auto()
    FAILED = auto()


def templates_root() -> Path:
    return WORKTREES_DIR.path.resolve() / TEMPLATES_DIR_NAME


def populate(git: GitRepo, bucket: str, target: Path) -> CopyOnWriteOutcome:
    """Clone the template into `target`, a registered worktree with no files.

    Anything but CLONED leaves `target` as it found it, so the caller's checkout
    writes every file as it does without this.
    """
    # Checked in this order, before the template, so that a machine or a
    # repository that cannot use clones keeps its config and gets no template.
    # Broad: the worktree is already registered, and an error here must leave
    # it to the normal checkout rather than abort the creation.
    try:
        eligible = (
            sys.platform == "darwin"
            and _trusted(git)
            and _clones_supported()
            and _enable_worktree_config(git)
        )
    except Exception as e:
        logger.warning("Copy-on-write worktrees are unavailable for %s: %s", target, e)
        eligible = False
    if not eligible:
        return CopyOnWriteOutcome.UNSUPPORTED
    template = _Template(bucket)
    with template.locked(blocking=False) as held:
        if not held or not template.ready:
            return CopyOnWriteOutcome.TEMPLATE_PENDING
        try:
            for entry in sorted(template.path.iterdir()):
                if entry.name != ".git":
                    _clone(entry, target / entry.name)
            _clone(
                _git_dir(git, template.path) / "index", _git_dir(git, target) / "index"
            )
            for key, value in _WORKTREE_CONFIG:
                git.run("config", "--worktree", key, value, cwd=target)
        # Broad: whatever went wrong, the normal checkout that follows still
        # gives the session its worktree.
        except Exception as e:
            logger.warning("Copy-on-write population of %s failed: %s", target, e)
            _discard(git, target)
            return CopyOnWriteOutcome.FAILED
    return CopyOnWriteOutcome.CLONED


def schedule_refresh(
    repo_root: Path, bucket: str, trust_store: TrustedFoldersManager | None
) -> None:
    """Bring the template to the default branch's tip, off the calling thread.

    One refresh per repository at a time in this process. Across processes, the
    template's lock serializes them and the second finds nothing to do.
    """
    if sys.platform != "darwin":
        return
    # Keeps this session's temporary grants: the creation that asks for this
    # refresh ran under them, and a repository trusted for this session only
    # would otherwise never get a template.
    with _refreshes_lock:
        running = _refreshes.get(bucket)
        if running is not None and running.is_alive():
            return
        thread = threading.Thread(
            target=_refresh_logged,
            args=(repo_root, bucket, trust_store),
            name="vibe-worktree-template",
            daemon=True,
        )
        _refreshes[bucket] = thread
        thread.start()


def refresh(
    repo_root: Path, bucket: str, trust_store: TrustedFoldersManager | None = None
) -> None:
    template = _Template(bucket)
    with GitRepo.open(repo_root, trust_store=trust_store) as git:
        if not _trusted(git):
            return
        ref = git.remote_default_branch_ref() or "HEAD"
        commit = git.run("rev-parse", "--verify", f"{ref}^{{commit}}") or ""
        with template.locked(blocking=True):
            if template.ready and _head(git, template.path) == commit:
                return
            template.mark_unready()
            if not (template.path / ".git").is_file():
                # --force: git refuses a path still registered after its
                # directory was deleted.
                git.run(
                    "worktree",
                    "add",
                    "--force",
                    "--no-checkout",
                    "--detach",
                    str(template.path),
                    commit,
                )
            # Only writes the files that differ from what the template has.
            git.run(
                "checkout",
                "--force",
                "--no-recurse-submodules",
                "--detach",
                commit,
                cwd=template.path,
            )
            git.run("clean", "-ffdxq", cwd=template.path)
            # Fresh stat data is what the clones inherit: an entry left racy
            # here would make every clone's first status reread that file.
            git.run("update-index", "-q", "--refresh", cwd=template.path)
            template.mark_ready()


def _refresh_logged(
    repo_root: Path, bucket: str, trust_store: TrustedFoldersManager | None
) -> None:
    # Broad: nothing waits on this thread, so an escaping error is only lost.
    try:
        refresh(repo_root, bucket, trust_store)
    except Exception as exc:
        logger.warning("Worktree template refresh failed for %s", bucket, exc_info=exc)


def evict_unused(used_buckets: set[str]) -> None:
    """Delete the template of every repository with no managed worktree left.

    Moved into the worktree trash, as worktree removal does: a clone in
    progress in another process never sees a half-deleted template, and the
    caller's trash sweep deletes it, or the next sweep does if this process
    dies first. The template's git admin directory goes with it, which is
    all `git worktree remove` does for a worktree whose directory is gone.
    """
    root = templates_root()
    try:
        locks = tuple(root.glob("*.lock"))
    except OSError:
        return
    for lock in locks:
        bucket = lock.name.removesuffix(".lock")
        if bucket in used_buckets:
            continue
        template = _Template(bucket)
        with template.locked(blocking=False) as held:
            if not held or not template.path.exists():
                continue
            template.mark_unready()
            _move_to_trash(template.path, bucket)


def _move_to_trash(path: Path, bucket: str) -> None:
    admin = None
    with suppress(OSError, ValueError):
        admin = Path((path / ".git").read_text().strip().removeprefix("gitdir: "))
    trash = WorktreeClaim.trash_root()
    trash.mkdir(parents=True, exist_ok=True)
    path.rename(trash / f"{TEMPLATES_DIR_NAME}-{bucket}-{uuid4().hex}")
    if admin is not None and admin.parent.name == "worktrees":
        shutil.rmtree(admin, ignore_errors=True)


class _Template:
    """The template checkout of one repository, and its lock and ready marker.

    The marker is removed before the template moves and written once it is
    clean and indexed, so a refresh interrupted at any point leaves a template
    nobody clones until the next refresh completes it.

    The lock file is never deleted: it is how eviction finds a template, and a
    refresh blocked on a deleted lock would rebuild a template eviction cannot
    find. Each one is an empty file.
    """

    def __init__(self, bucket: str) -> None:
        root = templates_root()
        self.path = root / bucket
        self._marker = root / f"{bucket}.ready"
        self._lock = root / f"{bucket}.lock"

    @property
    def ready(self) -> bool:
        return self._marker.exists()

    def mark_ready(self) -> None:
        self._marker.touch()

    def mark_unready(self) -> None:
        self._marker.unlink(missing_ok=True)

    @contextmanager
    def locked(self, *, blocking: bool) -> Iterator[bool]:
        self._lock.parent.mkdir(parents=True, exist_ok=True)
        with self._lock.open("a+b") as file:
            try:
                _acquire_file_lock(file, blocking=blocking)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                _release_file_lock(file)


def _clone(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    if libc.clonefile(bytes(source), bytes(destination), _CLONE_NOFOLLOW) != 0:
        code = ctypes.get_errno()
        raise OSError(code, f"clonefile {source} -> {destination} failed", str(source))


def _clones_supported() -> bool:
    """Whether the worktrees directory sits on a file system that clones.

    Tried on a scratch file there rather than inferred, since only the call
    answers for this volume. Each call uses its own scratch directory, so
    concurrent creations cannot make each other's probe fail.
    """
    root = templates_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".probe-", dir=root) as scratch:
            source = Path(scratch) / "source"
            source.write_bytes(b"probe")
            _clone(source, Path(scratch) / "clone")
    except OSError as e:
        logger.info("Copy-on-write worktrees are unavailable here: %s", e)
        return False
    return True


def _trusted(git: GitRepo) -> bool:
    # Cloning carries ignored files (build output, installed dependencies) from
    # the template, and the template is built with the repository's own LFS
    # and filter config. Both are only right for a repository the user trusts.
    trust_store = git.trust_store or trusted_folders_manager
    return trust_store.is_trusted(git.paths.repo_root) is True


def _enable_worktree_config(git: GitRepo) -> bool:
    """Let each worktree carry its own config, as `git sparse-checkout` does.

    Git requires `core.bare = true` and `core.worktree` to move to the main
    worktree's own config when this is turned on. Rather than move the user's
    settings, a repository that sets either is refused.
    """

    def local(*query: str) -> str | None:
        return git.run("config", "--local", *query, missing_ok=True)

    if local("--type=bool", "--get", "extensions.worktreeConfig") == "true":
        return True
    if (
        local("--type=bool", "--get", "core.bare") == "true"
        or local("--get", "core.worktree") is not None
    ):
        return False
    git.run("config", "--local", "extensions.worktreeConfig", "true")
    return True


def _git_dir(git: GitRepo, checkout: Path) -> Path:
    return Path(git.run("rev-parse", "--absolute-git-dir", cwd=checkout) or "")


def _head(git: GitRepo, checkout: Path) -> str | None:
    if not (checkout / ".git").is_file():
        return None
    try:
        return git.run("rev-parse", "HEAD", cwd=checkout)
    except GitError:
        return None


def _discard(git: GitRepo, target: Path) -> None:
    for entry in target.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    with suppress(GitError):
        (_git_dir(git, target) / "index").unlink(missing_ok=True)
    for key, _ in _WORKTREE_CONFIG:
        with suppress(GitError):
            git.run("config", "--worktree", "--unset", key, cwd=target)
