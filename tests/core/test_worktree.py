from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, cast

from git import Repo
from git.exc import GitCommandError
import pytest

import vibe.core.worktree as worktree_module
from vibe.core.worktree import (
    WorktreeError,
    inspect_worktree_for_cleanup,
    list_linked_worktrees,
    prepare_worktree,
    prepare_worktree_session,
    remove_worktree,
)


def _init_repo(root: Path, *, separate_git_dir: Path | None = None) -> Repo:
    repo = Repo.init(root, initial_branch="main", separate_git_dir=separate_git_dir)
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def test_creates_named_worktree_for_separate_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = prepare_worktree_session(
        "feature-worktree", tmp_path, branch="feat/feature"
    )

    assert worktree.name == "feature-worktree"
    assert worktree.branch == "feat/feature"
    assert Repo(worktree.root).active_branch.name == "feat/feature"
    assert "feat/feature" in (head.name for head in repo.heads)


def test_prepare_worktree_forwards_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    path = prepare_worktree("feature-worktree", tmp_path, branch="feat/feature")

    assert Repo(path).active_branch.name == "feat/feature"
    assert "feat/feature" in (head.name for head in repo.heads)


def test_reuses_named_worktree_for_separate_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    first = prepare_worktree_session(
        "feature-worktree", tmp_path, branch="feat/feature"
    )

    second = prepare_worktree_session(
        "feature-worktree", tmp_path, branch="feat/feature"
    )

    assert second.root == first.root
    assert second.branch == first.branch
    assert second.created is False


def test_prepare_does_not_require_worktree_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)

    def fail_if_called(repo: Repo) -> tuple[object, ...]:
        raise AssertionError(repo)

    monkeypatch.setattr(worktree_module, "_worktree_records", fail_if_called)

    worktree = prepare_worktree_session("feature", tmp_path)

    assert worktree.root.is_dir()


def test_prepare_cleans_created_worktree_when_metadata_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_build_prepared(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(worktree_module, "_build_prepared", fail_build_prepared)

    with pytest.raises(RuntimeError, match="metadata failed"):
        prepare_worktree_session("feature", tmp_path, branch="feat/feature")

    assert list_linked_worktrees(tmp_path) == ()
    assert "feat/feature" not in (head.name for head in repo.heads)


def test_build_prepared_wraps_invalid_head_metadata(tmp_path: Path) -> None:
    target = tmp_path / "worktree"
    target.mkdir()

    with pytest.raises(WorktreeError, match="Failed to inspect worktree HEAD"):
        worktree_module._build_prepared(
            "worktree",
            "feat/worktree",
            target,
            Path("."),
            tmp_path,
            created=True,
            branch_created=True,
        )


def test_lists_worktree_when_null_output_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    linked_root = tmp_path.parent / f"{tmp_path.name}-feature"
    repo.git.worktree("add", "-b", "feat/feature", str(linked_root))
    worktree_list_porcelain = worktree_module._worktree_list_porcelain

    def unsupported(repo: Repo, *, null_terminated: bool) -> str:
        if null_terminated:
            raise GitCommandError("git worktree", 129)
        return worktree_list_porcelain(repo, null_terminated=False)

    monkeypatch.setattr(worktree_module, "_worktree_list_porcelain", unsupported)

    linked = list_linked_worktrees(tmp_path)

    assert [(item.branch, item.root) for item in linked] == [
        ("feat/feature", linked_root.resolve())
    ]


def test_worktree_listing_failure_is_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)

    def failed(repo: Repo, *, null_terminated: bool) -> str:
        raise GitCommandError("git worktree", 128)

    monkeypatch.setattr(worktree_module, "_worktree_list_porcelain", failed)

    with pytest.raises(WorktreeError, match="Failed to list git worktrees"):
        list_linked_worktrees(tmp_path)


def test_root_level_system_alias_is_not_an_unstable_path_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_alias = Path(Path.cwd().anchor) / "system-alias"
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == system_alias or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)

    assert (
        worktree_module._has_linked_path_component(system_alias / "repo" / "worktree")
        is False
    )


def test_cleanup_and_remove_use_separate_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = prepare_worktree_session(
        "feature-worktree", tmp_path, branch="feat/feature"
    )
    worktree_repo = Repo(worktree.root)
    (worktree.root / "file.txt").write_text("changed\n")
    worktree_repo.index.add(["file.txt"])
    worktree_repo.index.commit("change")

    assert inspect_worktree_for_cleanup(worktree).new_commit_count == 1

    remove_worktree(worktree)

    assert not worktree.root.exists()
    assert "feat/feature" not in (head.name for head in repo.heads)


def test_cleanup_ignores_ignored_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".cache/\n")
    repo.index.add([".gitignore"])
    repo.index.commit("ignore generated file")
    worktree = prepare_worktree_session("feature", tmp_path)
    cache = worktree.root / ".cache"
    cache.mkdir()
    (cache / "first").write_text("important\n")
    (cache / "second").write_text("important\n")

    cleanup = inspect_worktree_for_cleanup(worktree)

    assert cleanup.is_clean is True


def test_cleanup_detects_untracked_files_when_git_config_hides_them(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    repo.config_writer().set_value("status", "showUntrackedFiles", "no").release()
    worktree = prepare_worktree_session("feature", tmp_path)
    (worktree.root / "untracked.txt").write_text("important\n")

    cleanup = inspect_worktree_for_cleanup(worktree)

    assert cleanup.has_untracked_files is True
    assert cleanup.reasons == ("untracked files",)


def test_cleanup_detects_detached_head_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session("feature", tmp_path)
    worktree_repo = Repo(worktree.root)
    worktree_repo.git.checkout("--detach")
    (worktree.root / "file.txt").write_text("detached change\n")
    worktree_repo.index.add(["file.txt"])
    worktree_repo.index.commit("detached change")

    cleanup = inspect_worktree_for_cleanup(worktree)

    assert cleanup.new_commit_count == 1


def test_lists_linked_worktrees_at_matching_project_subdirectory(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    first = prepare_worktree_session("first", project, branch="feat/first")
    second = prepare_worktree_session("second", project, branch="feat/second")

    linked = list_linked_worktrees(project)

    assert [(item.name, item.branch, item.path) for item in linked] == [
        ("first", "feat/first", first.path),
        ("second", "feat/second", second.path),
    ]


def test_lists_current_and_sibling_worktrees_from_linked_worktree(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    first = prepare_worktree_session("first", tmp_path, branch="feat/first")
    second = prepare_worktree_session("second", tmp_path, branch="feat/second")

    linked = list_linked_worktrees(first.path)

    assert [(item.name, item.branch, item.path) for item in linked] == [
        ("first", "feat/first", first.path),
        ("second", "feat/second", second.path),
    ]
    assert {item.repo_root for item in linked} == {tmp_path.resolve()}


def test_separate_git_dir_uses_primary_worktree_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    git_dir = tmp_path / "git-data"
    _init_repo(repo_root, separate_git_dir=git_dir)

    worktree = prepare_worktree_session(
        "feature-worktree", repo_root, branch="feat/feature"
    )
    linked = list_linked_worktrees(repo_root)

    assert worktree.repo_root == repo_root.resolve()
    assert len(linked) == 1
    assert linked[0].name == worktree.name
    assert linked[0].branch == worktree.branch
    assert linked[0].root == worktree.root
    assert linked[0].path == worktree.path
    assert linked[0].repo_root == repo_root.resolve()


def test_separate_git_dir_linked_worktree_cannot_infer_primary_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    git_dir = tmp_path / "git-data"
    _init_repo(repo_root, separate_git_dir=git_dir)
    worktree = prepare_worktree_session(
        "feature-worktree", repo_root, branch="feat/feature"
    )

    with pytest.raises(WorktreeError, match="Cannot determine the primary checkout"):
        list_linked_worktrees(worktree.path)


def test_skips_worktree_when_project_path_escapes_through_symlink(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    worktree = prepare_worktree_session("escape", project, branch="feat/escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(worktree.path)
    worktree.path.symlink_to(outside, target_is_directory=True)

    assert list_linked_worktrees(project) == ()


def test_skips_worktree_when_registered_root_is_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session("feature", tmp_path, branch="feat/feature")
    shutil.rmtree(worktree.root)
    worktree.root.symlink_to(tmp_path, target_is_directory=True)

    assert list_linked_worktrees(tmp_path) == ()


def test_skips_worktree_when_registered_parent_is_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session("feature", tmp_path, branch="feat/feature")
    registered_parent = worktree.root.parent
    moved_parent = tmp_path / "moved-worktrees"
    registered_parent.rename(moved_parent)
    registered_parent.symlink_to(moved_parent, target_is_directory=True)

    assert list_linked_worktrees(tmp_path) == ()


def test_rejects_managed_repository_directory_symlink_escape(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    common_git_dir = worktree_module._common_git_dir(repo)
    managed_repo_root = worktree_module._worktree_root(
        tmp_path.resolve(), common_git_dir
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    managed_repo_root.parent.mkdir(parents=True, exist_ok=True)
    managed_repo_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorktreeError, match="resolves outside"):
        prepare_worktree_session("feature", tmp_path, branch="feat/feature")


def test_skips_worktree_when_project_path_is_foreign_repository(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    worktree = prepare_worktree_session("foreign", project, branch="feat/foreign")
    Repo.init(worktree.path)

    assert list_linked_worktrees(project) == ()


def test_rejects_base_that_resolves_outside_worktree(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    _init_repo(repo_root)
    escaped = repo_root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorktreeError, match="outside Git worktree"):
        list_linked_worktrees(escaped)


def test_existing_worktree_with_malformed_rev_parse_output_raises_worktree_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session("feature", tmp_path)
    original_git = worktree_module._git_python()

    class FakeGit:
        def rev_parse(self, *_args: object) -> str:
            return "only-one-line"

    class FakeRepo:
        git = FakeGit()

    def repo_factory(path: Path, *args: Any, **kwargs: Any) -> Repo:
        if path == worktree.root:
            return cast(Repo, FakeRepo())
        return original_git.repo(path, *args, **kwargs)

    monkeypatch.setattr(
        worktree_module,
        "_git_python",
        lambda: worktree_module._GitPython(
            repo=cast(type[Repo], repo_factory),
            invalid_git_repository_error=original_git.invalid_git_repository_error,
            git_command_error=original_git.git_command_error,
            no_such_path_error=original_git.no_such_path_error,
        ),
    )

    with pytest.raises(WorktreeError, match="expected git rev-parse to return 2 lines"):
        prepare_worktree_session("feature", tmp_path)


def test_missing_base_raises_worktree_error(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="not inside a git repository"):
        list_linked_worktrees(tmp_path / "missing")


def test_rejects_invalid_branch_name(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    with pytest.raises(WorktreeError, match="Invalid worktree branch"):
        prepare_worktree_session("feature", tmp_path, branch="invalid branch")


@pytest.mark.parametrize(
    "name",
    [
        "bad*name",
        "bad:name",
        "trailing.",
        "trailing ",
        "NUL",
        "con.txt",
        "CONIN$",
        "CONOUT$.txt",
        "bad\0name",
    ],
)
def test_rejects_nonportable_worktree_name(tmp_path: Path, name: str) -> None:
    _init_repo(tmp_path)

    with pytest.raises(WorktreeError, match="portable filename"):
        prepare_worktree_session(name, tmp_path, branch="feat/feature")


@pytest.mark.parametrize("branch", ["", "invalid\0branch"])
def test_rejects_empty_or_nul_branch(tmp_path: Path, branch: str) -> None:
    _init_repo(tmp_path)

    with pytest.raises(WorktreeError, match="Invalid worktree branch"):
        prepare_worktree_session("feature", tmp_path, branch=branch)


def test_create_error_identifies_worktree_and_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    prepare_worktree_session("first", tmp_path, branch="feat/shared")

    with pytest.raises(
        WorktreeError,
        match="Failed to create worktree 'second' for branch 'feat/shared'",
    ):
        prepare_worktree_session("second", tmp_path, branch="feat/shared")
