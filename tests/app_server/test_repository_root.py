from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from vibe.app_server._repository_root import (
    Checkout,
    guess_gone_checkout,
    locate_checkout,
    repository_root,
    repository_roots,
)
from vibe.core.git.worktree.record import WorktreeClaim, WorktreeRecoveryRecord
from vibe.core.paths import WORKTREES_DIR


def _main_checkout(tmp_path: Path) -> Path:
    root = (tmp_path / "repository").resolve()
    (root / ".git" / "worktrees" / "feature").mkdir(parents=True)
    return root


def test_a_plain_repository_and_its_subfolders_report_its_root(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    nested = root / "packages" / "app"
    nested.mkdir(parents=True)

    assert repository_root(root) == root
    assert repository_root(nested) == root


def test_a_linked_worktree_reports_its_main_checkout(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    git_dir = root / ".git" / "worktrees" / "feature"
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = (tmp_path / "feature").resolve()
    (worktree / "src").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    assert repository_root(worktree) == root
    assert repository_root(worktree / "src") == root


def test_worktrees_of_bare_clones_report_their_own_repository(tmp_path: Path) -> None:
    """Two bare clones side by side never share the folder that holds them."""
    code = tmp_path.resolve()
    for name in ("alpha", "beta"):
        bare = code / f"{name}.git"
        git_dir = bare / "worktrees" / "main"
        git_dir.mkdir(parents=True)
        (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
        worktree = code / f"{name}-main"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

        assert locate_checkout(worktree) == Checkout(root=bare, checkout=worktree)


def test_a_relative_gitdir_is_read_from_the_checkout(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    git_dir = root / ".git" / "worktrees" / "feature"
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = root / "nested-feature"
    worktree.mkdir()
    (worktree / ".git").write_text(
        "gitdir: ../.git/worktrees/feature\n", encoding="utf-8"
    )

    assert repository_root(worktree) == root


def test_a_submodule_reports_itself(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    (root / ".git" / "modules" / "lib").mkdir(parents=True)
    submodule = root / "lib"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/lib\n", encoding="utf-8")

    assert repository_root(submodule) == submodule


def test_a_gitdir_marker_that_is_not_utf8_reports_none(tmp_path: Path) -> None:
    """A corrupt ``.git`` file leaves its folder unplaced instead of raising."""
    worktree = tmp_path / "corrupt"
    worktree.mkdir()
    (worktree / ".git").write_bytes(b"gitdir: \xff\xfe\n")

    assert locate_checkout(worktree) is None
    assert repository_roots([str(worktree)]) == {str(worktree): None}


def test_a_folder_outside_any_repository_reports_none(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()

    assert repository_root(folder) is None


def test_a_missing_folder_reports_none(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)

    assert repository_root(root / "deleted") is None


def test_a_retained_managed_worktree_reports_its_recorded_repository(
    tmp_path: Path,
) -> None:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    worktree = WORKTREES_DIR.path.resolve() / "bucket" / "retained"
    WorktreeClaim(bucket="bucket", name="retained").write_recovery(
        WorktreeRecoveryRecord(
            name="retained",
            branch="vibe/retained",
            repo_root=project,
            base_commit="0" * 40,
            snapshot_ref="refs/vibe/reaped/retained",
            removed_at=datetime.now(UTC),
        )
    )

    assert repository_root(worktree / "packages") == project


def test_each_folder_is_looked_up_once(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()

    roots = repository_roots([str(root), str(plain), str(root)])

    assert roots == {str(root): str(root), str(plain): None}


def test_a_linked_worktree_reports_itself_as_the_checkout(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    git_dir = root / ".git" / "worktrees" / "feature"
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = (tmp_path / "feature").resolve()
    (worktree / "src").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    assert locate_checkout(worktree / "src") == Checkout(root=root, checkout=worktree)
    assert locate_checkout(root) == Checkout(root=root, checkout=root)


def _linked_worktree(root: Path, folder: Path) -> Path:
    git_dir = root / ".git" / "worktrees" / folder.name
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    folder.mkdir(parents=True)
    (folder / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    return folder


def test_a_gone_worktree_belongs_to_the_repository_its_siblings_share(
    tmp_path: Path,
) -> None:
    root = _main_checkout(tmp_path)
    worktrees = (tmp_path / "worktrees" / "repository").resolve()
    _linked_worktree(root, worktrees / "feature-a")
    _linked_worktree(root, worktrees / "feature-b")

    assert guess_gone_checkout(worktrees / "removed" / "vibe") == Checkout(
        root=root, checkout=worktrees / "removed"
    )


def test_siblings_of_several_repositories_make_no_guess(tmp_path: Path) -> None:
    one = _main_checkout(tmp_path / "one")
    two = _main_checkout(tmp_path / "two")
    shared = (tmp_path / "scratch").resolve()
    _linked_worktree(one, shared / "a")
    _linked_worktree(two, shared / "b")

    assert guess_gone_checkout(shared / "removed") is None


def test_a_main_checkout_beside_the_worktrees_of_another_makes_no_guess(
    tmp_path: Path,
) -> None:
    code = (tmp_path / "code").resolve()
    one = _main_checkout(code)
    _linked_worktree(one, code / "one-feature")
    (code / "other" / ".git").mkdir(parents=True)

    assert guess_gone_checkout(code / "removed") is None


def test_a_main_checkout_beside_its_own_worktree_is_one_repository(
    tmp_path: Path,
) -> None:
    code = tmp_path / "code"
    root = _main_checkout(code)
    _linked_worktree(root, code / "repository-feature")

    assert guess_gone_checkout(code / "removed") == Checkout(
        root=root, checkout=code.resolve() / "removed"
    )


def test_a_lone_main_checkout_claims_no_gone_neighbour(tmp_path: Path) -> None:
    code = (tmp_path / "code").resolve()
    _main_checkout(code)

    assert guess_gone_checkout(code / "removed") is None


def test_a_crowded_parent_makes_no_guess(tmp_path: Path) -> None:
    root = _main_checkout(tmp_path)
    crowded = (tmp_path / "crowded").resolve()
    _linked_worktree(root, crowded / "linked")
    for index in range(64):
        (crowded / f"plain-{index}").mkdir()

    assert guess_gone_checkout(crowded / "removed") is None


def test_a_gone_folder_with_no_checkout_beside_it_makes_no_guess(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain").mkdir()

    assert guess_gone_checkout(tmp_path / "removed") is None


def test_a_gone_folder_inside_a_live_checkout_is_left_to_the_lookup(
    tmp_path: Path,
) -> None:
    root = _main_checkout(tmp_path)

    assert guess_gone_checkout(root / "deleted" / "deeper") is None
