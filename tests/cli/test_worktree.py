from __future__ import annotations

import hashlib
import json
from pathlib import Path

from git import Repo
import pytest

from vibe.core.config.models import SessionLoggingConfig
from vibe.core.paths import VIBE_HOME
from vibe.core.session.session_loader import SessionLoader
from vibe.core.worktree import (
    WorktreeError,
    inspect_worktree_for_cleanup,
    prepare_worktree,
    prepare_worktree_session,
    remove_worktree,
)


def _init_repo(workdir: Path) -> Repo:
    repo = Repo.init(workdir, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (workdir / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


@pytest.fixture
def git_repo(tmp_path: Path) -> Repo:
    return _init_repo(tmp_path)


def _expected_worktree_target(repo_root: Path, name: str) -> Path:
    common_git_dir = (repo_root / ".git").resolve()
    repo_hash = hashlib.sha256(str(common_git_dir).encode()).hexdigest()[:12]
    return VIBE_HOME.path / "worktrees" / f"{repo_root.name}-{repo_hash}" / name


def test_creates_worktree_and_branch(git_repo: Repo, tmp_path: Path) -> None:
    target = prepare_worktree("feature", tmp_path)

    assert target == _expected_worktree_target(tmp_path, "feature")
    assert target.is_dir()
    assert (target / ".git").is_file()
    assert "feature" in (h.name for h in git_repo.heads)


def test_does_not_touch_git_info_exclude(git_repo: Repo, tmp_path: Path) -> None:
    # Worktrees live under VIBE_HOME, not inside the repo, so prepare_worktree
    # must not append a ".worktrees/" ignore line to .git/info/exclude.
    prepare_worktree("feature", tmp_path)

    exclude_file = tmp_path / ".git" / "info" / "exclude"
    exclude = exclude_file.read_text() if exclude_file.exists() else ""
    assert ".worktrees/" not in exclude.splitlines()


def test_namespaces_same_worktree_name_by_repo(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _init_repo(first_root)
    _init_repo(second_root)

    first = prepare_worktree("feature", first_root)
    second = prepare_worktree("feature", second_root)

    assert first == _expected_worktree_target(first_root, "feature")
    assert second == _expected_worktree_target(second_root, "feature")
    assert first != second


def test_preserves_source_subdirectory(git_repo: Repo, tmp_path: Path) -> None:
    subdir = tmp_path / "pkg"
    subdir.mkdir()
    (subdir / "module.py").write_text("print('hello')\n")
    git_repo.index.add(["pkg/module.py"])
    git_repo.index.commit("add package")

    target = prepare_worktree("feature", subdir)

    assert target == _expected_worktree_target(tmp_path, "feature") / "pkg"
    assert target.is_dir()
    assert (target / "module.py").is_file()


def test_linked_worktree_uses_primary_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    linked = prepare_worktree("feature", repo_root)
    exclude = repo_root / ".git" / "info" / "exclude"
    exclude.write_text("")

    target = prepare_worktree("other", linked)

    assert target == _expected_worktree_target(repo_root, "other")
    assert target.is_dir()
    assert not (linked / ".worktrees").exists()
    assert exclude.read_text() == ""


def test_reuse_existing_worktree(git_repo: Repo, tmp_path: Path) -> None:
    first = prepare_worktree("feature", tmp_path)
    second = prepare_worktree("feature", tmp_path)

    assert first == second


def test_clean_worktree_cleanup_state_is_clean(git_repo: Repo, tmp_path: Path) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)

    cleanup_state = inspect_worktree_for_cleanup(worktree)

    assert cleanup_state.is_clean is True
    assert cleanup_state.reasons == ()


def test_cleanup_state_detects_untracked_files(git_repo: Repo, tmp_path: Path) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)
    (worktree.root / "new.txt").write_text("hello\n")

    cleanup_state = inspect_worktree_for_cleanup(worktree)

    assert cleanup_state.is_clean is False
    assert cleanup_state.has_untracked_files is True
    assert cleanup_state.reasons == ("untracked files",)


def test_cleanup_state_detects_uncommitted_changes(
    git_repo: Repo, tmp_path: Path
) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)
    (worktree.root / "file.txt").write_text("changed\n")

    cleanup_state = inspect_worktree_for_cleanup(worktree)

    assert cleanup_state.is_clean is False
    assert cleanup_state.has_uncommitted_changes is True
    assert cleanup_state.reasons == ("uncommitted changes",)


def test_cleanup_state_detects_new_commits(git_repo: Repo, tmp_path: Path) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)
    repo = Repo(worktree.root)
    (worktree.root / "file.txt").write_text("changed\n")
    repo.index.add(["file.txt"])
    repo.index.commit("change")

    cleanup_state = inspect_worktree_for_cleanup(worktree)

    assert cleanup_state.is_clean is False
    assert cleanup_state.new_commit_count == 1
    assert cleanup_state.reasons == (
        "1 commit added to the branch during this session",
    )


def test_reused_worktree_cleanup_starts_from_current_head(
    git_repo: Repo, tmp_path: Path
) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)
    repo = Repo(worktree.root)
    (worktree.root / "file.txt").write_text("changed\n")
    repo.index.add(["file.txt"])
    repo.index.commit("prior session commit")

    # Reusing the worktree resets base_commit to the worktree's current HEAD, so
    # commits from a previous session are not recounted as new work.
    reused = prepare_worktree_session("feature", tmp_path)

    assert inspect_worktree_for_cleanup(reused).new_commit_count == 0


def test_remove_worktree_deletes_directory_and_branch(
    git_repo: Repo, tmp_path: Path
) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)

    remove_worktree(worktree)

    assert not worktree.root.exists()
    assert "feature" not in (h.name for h in git_repo.heads)


def test_remove_worktree_keeps_branch_when_delete_branch_false(
    git_repo: Repo, tmp_path: Path
) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)

    remove_worktree(worktree, delete_branch=False)

    assert not worktree.root.exists()
    assert "feature" in (h.name for h in git_repo.heads)


def test_attaching_existing_branch_is_not_marked_branch_created(
    git_repo: Repo, tmp_path: Path
) -> None:
    git_repo.create_head("feature")

    worktree = prepare_worktree_session("feature", tmp_path)

    assert worktree.created is True
    assert worktree.branch_created is False


def test_new_branch_worktree_is_marked_branch_created(
    git_repo: Repo, tmp_path: Path
) -> None:
    worktree = prepare_worktree_session("feature", tmp_path)

    assert worktree.created is True
    assert worktree.branch_created is True


def test_existing_worktree_on_wrong_branch_raises(
    git_repo: Repo, tmp_path: Path
) -> None:
    target = prepare_worktree("feature", tmp_path)
    Repo(target).git.checkout("-b", "other")

    with pytest.raises(WorktreeError, match="expected 'feature'"):
        prepare_worktree("feature", tmp_path)


def test_existing_worktree_for_different_repo_raises(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _init_repo(first_root)
    second_repo = _init_repo(second_root)
    target = _expected_worktree_target(first_root, "feature")
    second_repo.git.worktree("add", "-b", "feature", str(target))

    with pytest.raises(WorktreeError, match="different git repository"):
        prepare_worktree("feature", first_root)


def test_attaches_existing_branch(git_repo: Repo, tmp_path: Path) -> None:
    git_repo.create_head("feature")

    target = prepare_worktree("feature", tmp_path)

    assert target.is_dir()
    assert (target / ".git").is_file()


@pytest.mark.parametrize(
    "name", ["", ".", "..", "nested/name", "nested\\name", "/tmp/outside"]
)
def test_rejects_worktree_names_that_are_not_path_segments(
    git_repo: Repo, tmp_path: Path, name: str
) -> None:
    with pytest.raises(WorktreeError, match="single path segment"):
        prepare_worktree(name, tmp_path)


def test_existing_non_worktree_dir_raises(git_repo: Repo, tmp_path: Path) -> None:
    target = _expected_worktree_target(tmp_path, "feature")
    target.mkdir(parents=True)

    with pytest.raises(WorktreeError, match="not a git worktree"):
        prepare_worktree("feature", tmp_path)


def test_non_git_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError):
        prepare_worktree("feature", tmp_path)


def _write_session(save_dir: Path, name: str, working_directory: Path) -> None:
    session_dir = save_dir / f"session_{name}"
    session_dir.mkdir(parents=True)
    (session_dir / "messages.jsonl").write_text("{}\n")
    (session_dir / "meta.json").write_text(
        json.dumps({"environment": {"working_directory": str(working_directory)}})
    )


def test_worktree_continue_scopes_to_worktree(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    target = prepare_worktree("feature", repo_root)

    save_dir = tmp_path / "sessions"
    save_dir.mkdir()
    _write_session(save_dir, "main", repo_root)
    config = SessionLoggingConfig(save_dir=str(save_dir))

    assert SessionLoader.find_latest_session(config, working_directory=target) is None
    assert (
        SessionLoader.find_latest_session(config, working_directory=repo_root)
        is not None
    )
