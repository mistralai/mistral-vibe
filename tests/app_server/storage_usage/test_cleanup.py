from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import time

from git import Repo
import pytest

from tests.app_server.storage_usage._tree import Tree, age, write
from vibe.app_server._storage_usage import cleanup
from vibe.app_server._storage_usage.cleanup import CleanRequest, clean
from vibe.core.git.worktree import (
    SNAPSHOT_REF_PREFIX,
    ManagedWorktree,
    WorktreeRelease,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
from vibe.core.paths import WORKTREES_DIR

_BUCKET = "repo-0123456789ab"


@pytest.fixture(autouse=True)
def _no_reservation_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 0)


def _request(
    tree: Tree, *, worktrees: tuple[str, ...] = (), folders: tuple[str, ...] = ()
) -> CleanRequest:
    return CleanRequest(
        worktrees_root=tree.worktrees_root,
        session_roots=(tree.sessions_root,),
        worktree_ids=worktrees,
        folder_ids=folders,
    )


def _reasons(response: object) -> dict[str, str]:
    return {item.id: item.reason for item in response.kept}  # type: ignore[attr-defined]


def test_an_unknown_folder_in_a_bucket_is_removed(tree: Tree) -> None:
    """*Prepare*: A build cache folder in a worktree bucket, no claim, no .git.
    *Do*: Clean it.
    *Assert*: Removed, and nothing else in the bucket is touched.
    """
    # Prepare
    write(tree.worktrees_root / _BUCKET / ".cargo-target" / "lib", 1_000)
    tree.worktree("kept", sessions=["s"])

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/.cargo-target",)))

    # Assert
    assert response.removed == [f"{_BUCKET}/.cargo-target"]
    assert not (tree.worktrees_root / _BUCKET / ".cargo-target").exists()
    assert (tree.worktrees_root / _BUCKET / "kept").is_dir()


def test_a_folder_is_deleted_only_once_the_prune_lock_is_released(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An unknown folder; the trash's delete records whether the
    prune lock, which attaching a session to a worktree waits on, is held.
    *Do*: Clean it.
    *Assert*: Under the lock it is only moved to the trash; the delete runs
    after the lock is released.
    """
    # Prepare
    write(tree.worktrees_root / _BUCKET / ".cargo-target" / "lib", 1_000)
    held = False
    real_lock = cleanup.worktree_prune_lock

    @contextmanager
    def watched_lock() -> Iterator[None]:
        nonlocal held
        with real_lock():
            held = True
            try:
                yield
            finally:
                held = False

    deletes: list[bool] = []
    real_empty = cleanup.ManagedWorktree.empty_trash

    def watched_empty() -> None:
        deletes.append(held)
        real_empty()

    monkeypatch.setattr(cleanup, "worktree_prune_lock", watched_lock)
    monkeypatch.setattr(cleanup.ManagedWorktree, "empty_trash", watched_empty)

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/.cargo-target",)))

    # Assert
    assert response.removed == [f"{_BUCKET}/.cargo-target"]
    assert deletes == [False]
    trash = tree.worktrees_root / ".trash"
    assert not trash.exists() or not any(trash.iterdir())


def test_a_claimed_worktree_is_never_removed_as_a_folder_or_a_worktree(
    tree: Tree,
) -> None:
    """*Prepare*: A worktree whose claim names a session that still exists.
    *Do*: Ask to clean it both as an unknown folder and as a leftover worktree.
    *Assert*: Kept as claimed both times; the folder is untouched.
    """
    # Prepare
    tree.session("alive")
    path = tree.worktree("busy", sessions=["alive"])
    item = f"{_BUCKET}/busy"

    # Do
    response = clean(_request(tree, worktrees=(item,), folders=(item,)))

    # Assert
    assert response.removed == []
    assert [kept.reason for kept in response.kept] == ["claimed", "claimed"]
    assert path.is_dir()


def test_a_folder_with_a_git_file_is_not_an_unknown_folder(tree: Tree) -> None:
    """*Prepare*: An unclaimed folder with a .git file: maybe a worktree.
    *Do*: Clean it as an unknown folder.
    *Assert*: Refused as unsafe.
    """
    # Prepare
    path = tree.worktree("stray", claim=False)

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/stray",)))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/stray": "unsafe"}
    assert path.is_dir()


@pytest.mark.parametrize(
    "item",
    [
        "../outside",
        f"{_BUCKET}/../../outside",
        f"{_BUCKET}/..",
        f"{_BUCKET}/a/b",
        ".claims/x",
        ".trash/x",
        f"{_BUCKET}/.claims",
        "not-a-bucket/x",
        f"{_BUCKET}\\x",
    ],
)
def test_an_id_that_could_name_a_path_outside_a_bucket_is_refused(
    tree: Tree, item: str
) -> None:
    """*Prepare*: A folder beside the worktrees root.
    *Do*: Clean ids that try to reach it or a reserved folder.
    *Assert*: Each refused; nothing outside is removed.
    """
    # Prepare
    write(tree.home / "outside" / "precious", 10)
    write(tree.worktrees_root / _BUCKET / "a" / "b" / "f", 10)

    # Do
    response = clean(_request(tree, folders=(item,), worktrees=(item,)))

    # Assert
    assert response.removed == []
    assert set(_reasons(response).values()) <= {"unsafe", "not_found"}
    assert (tree.home / "outside" / "precious").exists()


def test_a_symlink_in_a_bucket_is_never_followed(tree: Tree) -> None:
    """*Prepare*: A bucket entry that is a symlink to a folder outside the root,
    and a real unknown folder holding a symlink to another outside folder.
    *Do*: Clean both.
    *Assert*: The symlink entry is refused; the real folder goes without the
    target of the symlink it held.
    """
    # Prepare
    write(tree.home / "outside" / "precious", 10)
    write(tree.home / "elsewhere" / "precious", 10)
    bucket = tree.worktrees_root / _BUCKET
    bucket.mkdir(parents=True)
    (bucket / "link").symlink_to(tree.home / "outside", target_is_directory=True)
    write(bucket / "real" / "f", 10)
    (bucket / "real" / "escape").symlink_to(
        tree.home / "elsewhere", target_is_directory=True
    )

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/link", f"{_BUCKET}/real")))

    # Assert
    assert response.removed == [f"{_BUCKET}/real"]
    assert _reasons(response) == {f"{_BUCKET}/link": "unsafe"}
    assert (tree.home / "outside" / "precious").exists()
    assert (tree.home / "elsewhere" / "precious").exists()


def test_a_bucket_that_is_a_symlink_is_refused(tree: Tree) -> None:
    """*Prepare*: A bucket that is a symlink to a folder outside the root.
    *Do*: Clean a folder through it.
    *Assert*: Refused; the folder outside stays.
    """
    # Prepare
    write(tree.home / "outside" / "inner" / "precious", 10)
    tree.worktrees_root.mkdir(parents=True)
    (tree.worktrees_root / _BUCKET).symlink_to(
        tree.home / "outside", target_is_directory=True
    )

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/inner",)))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/inner": "unsafe"}
    assert (tree.home / "outside" / "inner" / "precious").exists()


def test_a_folder_an_open_session_works_in_is_kept(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An unknown folder that an open session's working directory is in.
    *Do*: Clean it.
    *Assert*: Kept as in use.
    """
    # Prepare
    folder = tree.worktrees_root / _BUCKET / "scratch"
    write(folder / "f", 10)
    tree.session("open", cwd=folder / "sub")
    monkeypatch.setattr(cleanup.live, "open_sessions", lambda roots: {"open"})

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/scratch",)))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/scratch": "in_use"}
    assert folder.is_dir()


def test_a_folder_younger_than_a_reservation_is_kept(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An empty unclaimed folder made a moment ago, like a new
    worktree's reservation before its claim is written.
    *Do*: Clean it with the real grace period.
    *Assert*: Kept as in use.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    folder = tree.worktrees_root / _BUCKET / "new-worktree"
    folder.mkdir(parents=True)
    os.utime(folder, (time.time(), time.time()))

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/new-worktree",)))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/new-worktree": "in_use"}
    assert folder.is_dir()


def _init_repo(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def test_an_unclaimed_worktree_is_removed_and_its_work_saved_to_a_ref(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: A real managed worktree whose only session folder is gone,
    with an uncommitted file.
    *Do*: Clean it.
    *Assert*: Removed through the reap path, the work saved to a ref.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = _init_repo(repo_root)
    with WorktreeRepository.open(repo_root) as repository:
        worktree = repository.prepare_auto(suggested_name="gone")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("gone", session_dir=tmp_path / "sessions" / "gone")
    managed.release_holder("gone")
    (worktree.root / "notes.txt").write_text("unsaved\n")
    item = f"{managed.claim.bucket}/{managed.claim.name}"

    # Do
    response = clean(
        CleanRequest(
            worktrees_root=WORKTREES_DIR.path,
            session_roots=(),
            worktree_ids=(item,),
            folder_ids=(),
        )
    )

    # Assert
    assert response.removed == [item]
    assert not worktree.root.exists()
    ref = f"{SNAPSHOT_REF_PREFIX}/{managed.claim.name}"
    assert "notes.txt" in repo.git.show("--stat", ref)


def test_a_held_worktree_is_kept_in_use(tmp_path: Path, config_dir: Path) -> None:
    """*Prepare*: A managed worktree whose session folder is gone but whose
    holder (a live process) is still there.
    *Do*: Clean it.
    *Assert*: Kept as in use; the folder stays.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    with WorktreeRepository.open(repo_root) as repository:
        worktree = repository.prepare_auto(suggested_name="held")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("held", session_dir=tmp_path / "sessions" / "held")
    item = f"{managed.claim.bucket}/{managed.claim.name}"

    # Do
    response = clean(
        CleanRequest(
            worktrees_root=WORKTREES_DIR.path,
            session_roots=(),
            worktree_ids=(item,),
            folder_ids=(),
        )
    )

    # Assert
    assert _reasons(response) == {item: "in_use"}
    assert worktree.root.is_dir()
    managed.release_holder("held")


def _corrupt_claim(tree: Tree, name: str) -> None:
    """A claim whose ``.removing`` holder is a symlink out of its folder: the
    release refuses it with ``WorktreeRecordError``.
    """
    holders = tree.worktrees_root / ".claims" / _BUCKET / name / "holders"
    holders.mkdir(parents=True)
    (holders / ".removing").symlink_to(tree.home / "elsewhere")


def test_a_corrupt_claim_is_kept_as_an_error_and_the_rest_still_goes(
    tree: Tree,
) -> None:
    """*Prepare*: A leftover worktree whose claim the release refuses, and an
    unknown folder.
    *Do*: Clean both.
    *Assert*: The worktree is kept with ``error``; the folder is removed.
    """
    # Prepare
    path = tree.worktree("corrupt", sessions=["gone"])
    _corrupt_claim(tree, "corrupt")
    write(tree.worktrees_root / _BUCKET / ".cache" / "lib", 10)

    # Do
    response = clean(
        _request(
            tree, worktrees=(f"{_BUCKET}/corrupt",), folders=(f"{_BUCKET}/.cache",)
        )
    )

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/corrupt": "error"}
    assert response.removed == [f"{_BUCKET}/.cache"]
    assert path.is_dir()


def _cargo_like(root: Path, crates: int) -> int:
    """A build tree shaped like a cargo ``target``: per crate a build script
    folder, an ``out`` folder and incremental session folders. Folders only:
    the freshness check never reads a file. The number of folders made.
    """
    made = 0
    for crate in range(crates):
        for folder in (
            root / "debug" / "build" / f"crate{crate}-0123456789abcdef" / "out",
            root / "debug" / "incremental" / f"crate{crate}-abc" / "s-1-x",
            root / "debug" / "incremental" / f"crate{crate}-abc" / "s-2-y",
            root / "debug" / ".fingerprint" / f"crate{crate}-0123456789abcdef",
        ):
            folder.mkdir(parents=True, exist_ok=True)
            made += 1
    (root / "debug" / "deps").mkdir(parents=True, exist_ok=True)
    return made


def test_a_build_writing_deep_inside_an_old_folder_keeps_it(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An unknown folder untouched for an hour, but a build just
    made a folder deep inside it.
    *Do*: Clean it with the real grace period.
    *Assert*: Kept as in use.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    folder = tree.worktrees_root / _BUCKET / ".cargo-target"
    _cargo_like(folder, 20)
    age(folder, 3600)
    deep = folder / "debug" / "incremental" / "crate7-abc" / "s-3-z"
    deep.mkdir()

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/.cargo-target",)))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/.cargo-target": "in_use"}
    assert deep.is_dir()


def test_the_freshness_check_never_follows_a_symlink(tree: Tree) -> None:
    """*Prepare*: An old folder holding a symlink to a folder changed just now.
    *Do*: Check whether it changed recently.
    *Assert*: It did not: what the symlink points to is never read.
    """
    # Prepare
    outside = tree.home / "elsewhere"
    outside.mkdir()
    folder = tree.worktrees_root / _BUCKET / ".cache"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "link").symlink_to(outside, target_is_directory=True)
    age(folder, 3600)
    (outside / "new").mkdir()

    # Do
    fresh = cleanup.recently_changed(folder, time.time())

    # Assert
    assert fresh is False


@pytest.mark.parametrize("bound", ["entries", "time"])
def test_a_folder_too_large_to_check_in_time_is_kept_as_such(
    tree: Tree, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    """*Prepare*: An old unknown folder, and a check allowed fewer entries,
    or less time, than the folder needs.
    *Do*: Clean it with the real grace period.
    *Assert*: Kept as too large to check, with its top folder's time for the
    caller to remember: an unfinished check never removes.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    if bound == "entries":
        monkeypatch.setattr(cleanup, "FRESH_CHECK_MAX_ENTRIES", 10)
    else:
        monkeypatch.setattr(cleanup, "FRESH_CHECK_BUDGET_S", 0.0)
    folder = tree.worktrees_root / _BUCKET / ".cargo-target"
    _cargo_like(folder, 20)
    age(folder, 3600)
    progress = cleanup.CleanProgress()

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/.cargo-target",)), progress)

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/.cargo-target": "too_large_to_check"}
    assert progress.unverified == {
        f"{_BUCKET}/.cargo-target": folder.stat().st_mtime_ns
    }
    assert folder.is_dir()


def test_an_old_build_folder_of_thousands_of_folders_is_checked_and_removed(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An unknown folder shaped like a large cargo target, about
    6,000 folders, all untouched for an hour.
    *Do*: Clean it with the real grace period, timing the freshness check.
    *Assert*: Removed, the check well inside its time budget.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    folder = tree.worktrees_root / _BUCKET / ".cargo-target"
    made = _cargo_like(folder, 1_500)
    age(folder, 3600)
    checks: list[tuple[cleanup.Freshness, float]] = []
    real_freshness = cleanup.freshness

    def freshness(path: Path, now_s: float, **kwargs: object) -> cleanup.Freshness:
        started = time.monotonic()
        result = real_freshness(path, now_s, **kwargs)  # type: ignore[arg-type]
        checks.append((result, time.monotonic() - started))
        return result

    monkeypatch.setattr(cleanup, "freshness", freshness)
    monkeypatch.setattr(
        cleanup.ManagedWorktree, "empty_trash", staticmethod(lambda: None)
    )

    # Do
    response = clean(_request(tree, folders=(f"{_BUCKET}/.cargo-target",)))

    # Assert
    assert made >= 6_000
    [(fresh, elapsed)] = checks
    assert fresh is cleanup.Freshness.UNCHANGED
    assert elapsed < cleanup.FRESH_CHECK_BUDGET_S / 2, f"{elapsed:.2f} s"
    assert response.removed == [f"{_BUCKET}/.cargo-target"]
    assert not folder.exists()


def test_a_new_worktree_no_session_joined_yet_is_kept(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A managed worktree claimed a moment ago that no session has
    joined, and one claimed long ago that none ever joined; a release that
    would remove either.
    *Do*: Clean both with the real grace period.
    *Assert*: The new one is kept as in use; the old one goes.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    released: list[str] = []

    def release(self: ManagedWorktree, **_kwargs: object) -> WorktreeRelease:
        released.append(self.claim.name)
        return WorktreeRelease(WorktreeReleaseOutcome.REMOVED)

    monkeypatch.setattr(ManagedWorktree, "release", release)
    tree.worktree("new", claimed_at=datetime.now(UTC))
    tree.worktree("old")
    claims = tree.worktrees_root / ".claims" / _BUCKET
    age(claims / "old", 3600)

    # Do
    response = clean(_request(tree, worktrees=(f"{_BUCKET}/new", f"{_BUCKET}/old")))

    # Assert
    assert _reasons(response) == {f"{_BUCKET}/new": "in_use"}
    assert response.removed == [f"{_BUCKET}/old"]
    assert released == ["old"]


def test_an_id_with_a_nul_byte_is_kept_unsafe_and_the_rest_still_goes(
    tree: Tree,
) -> None:
    """*Prepare*: An unknown folder, and ids carrying a NUL byte, which no
    path may hold.
    *Do*: Clean the NUL ids as a worktree and as a folder, with the folder.
    *Assert*: The NUL ids are kept as unsafe; the folder is removed.
    """
    # Prepare
    write(tree.worktrees_root / _BUCKET / ".cache" / "lib", 10)
    bad = f"{_BUCKET}/a\x00b"

    # Do
    response = clean(
        _request(tree, worktrees=(bad,), folders=(bad, f"{_BUCKET}/.cache"))
    )

    # Assert
    assert [(kept.id, kept.reason) for kept in response.kept] == [
        (bad, "unsafe"),
        (bad, "unsafe"),
    ]
    assert response.removed == [f"{_BUCKET}/.cache"]


def test_each_folder_is_checked_again_right_before_it_moves(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Two old unknown folders, each check and move recorded.
    *Do*: Clean both.
    *Assert*: Each folder's deep check is followed by its own move, so a
    check is never one whole request stale by the time its folder moves.
    """
    # Prepare
    monkeypatch.setattr(cleanup, "RESERVATION_GRACE_S", 600)
    bucket = tree.worktrees_root / "repo-0123456789ab"
    for name in ("first", "second"):
        write(bucket / name / "deep" / "lib", 10)
        age(bucket / name, 3600)
    events: list[str] = []
    real_freshness = cleanup.freshness
    real_remove = cleanup._remove_folder

    def freshness(path: Path, now_s: float, **kwargs: object) -> cleanup.Freshness:
        events.append(f"check {path.name}")
        return real_freshness(path, now_s, **kwargs)  # type: ignore[arg-type]

    def remove(root: Path, item: str, open_cwds: list[Path]) -> object:
        events.append(f"move {item.split('/')[1]}")
        return real_remove(root, item, open_cwds)

    monkeypatch.setattr(cleanup, "freshness", freshness)
    monkeypatch.setattr(cleanup, "_remove_folder", remove)

    # Do
    response = cleanup.clean(
        cleanup.CleanRequest(
            worktrees_root=tree.worktrees_root,
            session_roots=(),
            worktree_ids=(),
            folder_ids=("repo-0123456789ab/first", "repo-0123456789ab/second"),
        )
    )

    # Assert
    assert response.removed == ["repo-0123456789ab/first", "repo-0123456789ab/second"]
    assert events == ["check first", "move first", "check second", "move second"]
